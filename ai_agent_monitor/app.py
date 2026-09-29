#!/usr/bin/env python3
"""Serve the AI Agent Monitor dashboard and record agent state."""

from __future__ import annotations

import argparse
import hashlib
import html
import os
import json
import mimetypes
import re
import shutil
import smtplib
import socket
import sqlite3
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import closing, contextmanager, nullcontext
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parseaddr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

from . import __version__
from . import codex_cost
from . import codex_usage
from . import registry as project_registry
from . import telemetry as project_telemetry
from . import telegram_inbox

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_JSON_BODY = 64 * 1024
STATUS_SCHEMA_VERSION = 1
STATUS_PROGRESS_LIMIT = 10
STATUS_ANSWER_LIMIT = 10
PACKAGE_PATH = Path(__file__).resolve().parent
PROJECT_ROOT = Path.cwd().resolve()
DEFAULT_DASHBOARD_PATH = PACKAGE_PATH / "dashboard.html"
REGISTRY_PAGE_PATH = PACKAGE_PATH / "registry.html"
AGENT_CONTRACT_PATH = PACKAGE_PATH / "agent_integration.md"
CODEX_SKILL_PATH = PACKAGE_PATH / "codex_skill.md"
AGENTS_BLOCK_START = "<!-- ai-agent-monitor:start -->"
AGENTS_BLOCK_END = "<!-- ai-agent-monitor:end -->"
AGENTS_BLOCK = """<!-- ai-agent-monitor:start -->
## AI Agent Monitor

For development work in this repository, use the `ai-agent-monitor` repository skill in `.agents/skills/ai-agent-monitor/SKILL.md`. At the start of a new run or when resuming work, follow the skill's runner-registration step and then run `monitor status` before reporting new monitor state.
<!-- ai-agent-monitor:end -->"""
DATA_DIR = PROJECT_ROOT / ".agent-monitor"
DB_PATH = DATA_DIR / "monitor.db"
CODEX_COST_CONFIG_PATH = DATA_DIR / "runtime" / "codex-cost.json"
CODEX_COST_CACHE_PATH = DATA_DIR / "runtime" / "codex-cost-cache.json"
CODEX_COST_MANAGER = codex_cost.CostEstimateManager()
CODEX_USAGE_CONFIG_PATH = DATA_DIR / "runtime" / "codex-usage.json"
CODEX_USAGE_MANAGER = codex_usage.WeeklyUsageManager()


PROJECT_GITIGNORE = """monitor.db
monitor.db-shm
monitor.db-wal
monitor.db-journal
notifications.json
runner.json
registry.json
artifacts/
runtime/
"""


def dashboard_path() -> Path:
    project_dashboard = DATA_DIR / "dashboard.html"
    if project_dashboard.is_file():
        return project_dashboard
    return DEFAULT_DASHBOARD_PATH


def rendered_dashboard_html() -> bytes:
    content = dashboard_path().read_text(encoding="utf-8")
    escaped_name = html.escape(PROJECT_ROOT.name)
    title = f"<title>{escaped_name}</title>"
    title_pattern = r"<title\b[^>]*>.*?</title\s*>"
    if re.search(title_pattern, content, flags=re.IGNORECASE | re.DOTALL):
        content = re.sub(
            title_pattern,
            lambda _: title,
            content,
            count=1,
            flags=re.IGNORECASE | re.DOTALL,
        )
    elif re.search(r"<head\b[^>]*>", content, flags=re.IGNORECASE):
        content = re.sub(
            r"<head\b[^>]*>",
            lambda match: f"{match.group(0)}\n  {title}",
            content,
            count=1,
            flags=re.IGNORECASE,
        )
    else:
        content = f"{title}\n{content}"

    content = re.sub(
        r'(<h1\b[^>]*\bid=["\']monitor-project-heading["\'][^>]*>).*?(</h1\s*>)',
        lambda match: f"{match.group(1)}{escaped_name}{match.group(2)}",
        content,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return content.encode("utf-8")


def registry_path() -> Path:
    return DATA_DIR / "registry.json"


def _ensure_project_gitignore(ignore_path: Path) -> None:
    required = [line for line in PROJECT_GITIGNORE.splitlines() if line]
    existing_text = ignore_path.read_text(encoding="utf-8") if ignore_path.exists() else ""
    existing_lines = existing_text.splitlines()

    missing = [line for line in required if line not in existing_lines]
    if not missing:
        return

    content = existing_text
    if content and not content.endswith("\n"):
        content += "\n"
    content += "\n".join(missing) + "\n"
    ignore_path.write_text(content, encoding="utf-8")


def init_project(copy_dashboard: bool = False) -> dict[str, object]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    ignore_path = DATA_DIR / ".gitignore"
    _ensure_project_gitignore(ignore_path)

    dashboard_created = False
    project_dashboard = DATA_DIR / "dashboard.html"
    if copy_dashboard and not project_dashboard.exists():
        shutil.copyfile(DEFAULT_DASHBOARD_PATH, project_dashboard)
        dashboard_created = True

    return {
        "project_root": str(PROJECT_ROOT),
        "data_dir": str(DATA_DIR),
        "dashboard": str(project_dashboard) if project_dashboard.exists() else None,
        "dashboard_created": dashboard_created,
    }


def agent_contract_target() -> Path:
    return PROJECT_ROOT / "AI_AGENT_MONITOR.md"


def codex_skill_target() -> Path:
    return PROJECT_ROOT / ".agents" / "skills" / "ai-agent-monitor" / "SKILL.md"


def agents_file_path() -> Path:
    return PROJECT_ROOT / "AGENTS.md"


def _replace_managed_agents_block(existing: str) -> str:
    start_count = existing.count(AGENTS_BLOCK_START)
    end_count = existing.count(AGENTS_BLOCK_END)
    if start_count != end_count or start_count > 1:
        raise RuntimeError(
            "AGENTS.md contains invalid AI Agent Monitor managed-block markers."
        )

    if start_count == 0:
        content = existing
        if content and not content.endswith("\n"):
            content += "\n"
        if content and not content.endswith("\n\n"):
            content += "\n"
        return content + AGENTS_BLOCK + "\n"

    start = existing.index(AGENTS_BLOCK_START)
    end = existing.index(AGENTS_BLOCK_END, start) + len(AGENTS_BLOCK_END)
    return existing[:start] + AGENTS_BLOCK + existing[end:]


def _remove_managed_agents_block(existing: str) -> tuple[str, bool]:
    start_count = existing.count(AGENTS_BLOCK_START)
    end_count = existing.count(AGENTS_BLOCK_END)
    if start_count != end_count or start_count > 1:
        raise RuntimeError(
            "AGENTS.md contains invalid AI Agent Monitor managed-block markers."
        )
    if start_count == 0:
        return existing, False

    start = existing.index(AGENTS_BLOCK_START)
    end = existing.index(AGENTS_BLOCK_END, start) + len(AGENTS_BLOCK_END)

    before = existing[:start].rstrip()
    after = existing[end:].lstrip()
    pieces = [piece for piece in (before, after) if piece]
    if not pieces:
        return "", True
    return "\n\n".join(pieces) + "\n", True


def install_agent_integration(kind: str) -> dict[str, object]:
    if kind not in {"codex", "generic"}:
        raise ValueError("Agent integration kind must be 'codex' or 'generic'.")

    contract_content = AGENT_CONTRACT_PATH.read_text(encoding="utf-8")
    contract_target = agent_contract_target()

    result: dict[str, object] = {
        "kind": kind,
        "contract": str(contract_target),
        "agents_file": None,
        "skill": None,
    }

    if kind == "generic":
        contract_target.write_text(contract_content, encoding="utf-8")
        return result

    agents_path = agents_file_path()
    existing = agents_path.read_text(encoding="utf-8") if agents_path.exists() else ""
    updated_agents = _replace_managed_agents_block(existing)

    skill_target = codex_skill_target()
    skill_content = CODEX_SKILL_PATH.read_text(encoding="utf-8")

    contract_target.write_text(contract_content, encoding="utf-8")
    skill_target.parent.mkdir(parents=True, exist_ok=True)
    skill_target.write_text(skill_content, encoding="utf-8")
    agents_path.write_text(updated_agents, encoding="utf-8")

    result["agents_file"] = str(agents_path)
    result["skill"] = str(skill_target)
    return result


def remove_agent_integration(kind: str) -> dict[str, object]:
    if kind not in {"codex", "generic"}:
        raise ValueError("Agent integration kind must be 'codex' or 'generic'.")

    contract_target = agent_contract_target()
    if contract_target.exists():
        expected_contract = AGENT_CONTRACT_PATH.read_text(encoding="utf-8")
        if contract_target.read_text(encoding="utf-8") != expected_contract:
            raise RuntimeError(
                "AI_AGENT_MONITOR.md was modified after installation; refusing to delete it."
            )

    agents_path = agents_file_path()
    updated_agents: str | None = None
    agents_changed = False
    skill_target = codex_skill_target()

    if kind == "codex":
        if skill_target.exists():
            expected_skill = CODEX_SKILL_PATH.read_text(encoding="utf-8")
            if skill_target.read_text(encoding="utf-8") != expected_skill:
                raise RuntimeError(
                    "Codex skill was modified after installation; refusing to delete it."
                )

        if agents_path.exists():
            updated_agents, agents_changed = _remove_managed_agents_block(
                agents_path.read_text(encoding="utf-8")
            )

    removed: list[str] = []

    if kind == "codex":
        if agents_changed:
            if updated_agents:
                agents_path.write_text(updated_agents, encoding="utf-8")
            else:
                agents_path.unlink()
            removed.append(str(agents_path))

        if skill_target.exists():
            skill_target.unlink()
            removed.append(str(skill_target))
            try:
                skill_target.parent.rmdir()
                skill_target.parent.parent.rmdir()
                skill_target.parent.parent.parent.rmdir()
            except OSError:
                pass

    if contract_target.exists():
        contract_target.unlink()
        removed.append(str(contract_target))

    return {"kind": kind, "removed": removed}


def emit_agent_integration(kind: str) -> str:
    if kind == "generic":
        return AGENT_CONTRACT_PATH.read_text(encoding="utf-8")
    if kind == "codex":
        return AGENTS_BLOCK + "\n\n" + CODEX_SKILL_PATH.read_text(encoding="utf-8")
    raise ValueError("Agent integration kind must be 'codex' or 'generic'.")


class QuestionNotFoundError(Exception):
    pass


class QuestionClosedError(Exception):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _ensure_question_columns(connection: sqlite3.Connection) -> None:
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(questions)").fetchall()
    }
    if "answer" not in columns:
        connection.execute("ALTER TABLE questions ADD COLUMN answer TEXT")
    if "answered_at" not in columns:
        connection.execute("ALTER TABLE questions ADD COLUMN answered_at TEXT")
    if "runner_id" not in columns:
        connection.execute("ALTER TABLE questions ADD COLUMN runner_id INTEGER")


def _ensure_notification_deliveries_target(connection: sqlite3.Connection) -> None:
    columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(notification_deliveries)"
        ).fetchall()
    }
    if "target" in columns:
        return

    connection.execute(
        "ALTER TABLE notification_deliveries RENAME TO notification_deliveries_legacy"
    )
    connection.execute(
        """
        CREATE TABLE notification_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notification_id INTEGER NOT NULL,
            channel TEXT NOT NULL,
            target TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            last_attempt_at TEXT,
            delivered_at TEXT,
            cancelled_at TEXT,
            last_error TEXT,
            UNIQUE(notification_id, channel, target),
            FOREIGN KEY(notification_id) REFERENCES notification_outbox(id)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO notification_deliveries (
            id,
            notification_id,
            channel,
            target,
            status,
            attempt_count,
            created_at,
            last_attempt_at,
            delivered_at,
            cancelled_at,
            last_error
        )
        SELECT
            id,
            notification_id,
            channel,
            '',
            status,
            attempt_count,
            created_at,
            last_attempt_at,
            delivered_at,
            cancelled_at,
            last_error
        FROM notification_deliveries_legacy
        """
    )
    connection.execute("DROP TABLE notification_deliveries_legacy")

    recipients = email_recipients()
    if len(recipients) == 1:
        connection.execute(
            """
            UPDATE notification_deliveries
            SET target = ?
            WHERE channel = 'email' AND target = ''
            """,
            (recipients[0],),
        )


def _ensure_notification_outbox_columns(connection: sqlite3.Connection) -> None:
    columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(notification_outbox)"
        ).fetchall()
    }
    if "cancelled_at" not in columns:
        connection.execute(
            "ALTER TABLE notification_outbox ADD COLUMN cancelled_at TEXT"
        )


def connect_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=5)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL,
            message_ja TEXT
        )
        """
    )
    progress_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(progress)")
    }
    if "message_ja" not in progress_columns:
        connection.execute("ALTER TABLE progress ADD COLUMN message_ja TEXT")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS current_task (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            title TEXT NOT NULL,
            started_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS display_translations (
            entity_type TEXT NOT NULL,
            entity_id INTEGER NOT NULL,
            source_text TEXT NOT NULL,
            ja_text TEXT NOT NULL,
            PRIMARY KEY (entity_type, entity_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS task_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT NOT NULL,
            outcome TEXT NOT NULL CHECK (outcome IN ('completed', 'replaced'))
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL,
            answer TEXT,
            answered_at TEXT
        )
        """
    )
    _ensure_question_columns(connection)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_runners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            adapter TEXT NOT NULL,
            external_id TEXT NOT NULL,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_error TEXT,
            UNIQUE(adapter, external_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS answer_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL UNIQUE,
            answer TEXT NOT NULL,
            answered_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO answer_events (question_id, answer, answered_at)
        SELECT id, answer, answered_at
        FROM questions
        WHERE status = 'answered' AND answer IS NOT NULL AND answered_at IS NOT NULL
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS resume_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            answer_event_id INTEGER NOT NULL UNIQUE,
            question_id INTEGER NOT NULL,
            runner_id INTEGER,
            status TEXT NOT NULL,
            reason TEXT,
            created_at TEXT NOT NULL,
            claimed_at TEXT,
            completed_at TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS resume_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            pid INTEGER,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            exit_code INTEGER,
            last_error TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            display_name TEXT NOT NULL,
            original_name TEXT NOT NULL,
            storage_name TEXT NOT NULL UNIQUE,
            size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            git_commit TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS metrics (
            key TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            value TEXT NOT NULL,
            unit TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS notification_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            last_attempt_at TEXT,
            delivered_at TEXT,
            cancelled_at TEXT,
            last_error TEXT,
            UNIQUE(event_type, entity_type, entity_id)
        )
        """
    )
    _ensure_notification_outbox_columns(connection)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS notification_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notification_id INTEGER NOT NULL,
            channel TEXT NOT NULL,
            target TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            last_attempt_at TEXT,
            delivered_at TEXT,
            cancelled_at TEXT,
            last_error TEXT,
            UNIQUE(notification_id, channel, target),
            FOREIGN KEY(notification_id) REFERENCES notification_outbox(id)
        )
        """
    )
    _ensure_notification_deliveries_target(connection)
    delivery_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(notification_deliveries)")
    }
    if "telegram_message_id" not in delivery_columns:
        connection.execute(
            "ALTER TABLE notification_deliveries ADD COLUMN telegram_message_id INTEGER"
        )
    if "telegram_chat_id" not in delivery_columns:
        connection.execute(
            "ALTER TABLE notification_deliveries ADD COLUMN telegram_chat_id TEXT"
        )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS telegram_inbox_cursor (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            next_offset INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    connection.commit()
    return connection


@contextmanager
def database_session() -> Iterator[sqlite3.Connection]:
    connection = connect_db()
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def record_progress(message: str, message_ja: str | None = None) -> dict[str, object]:
    message = message.strip()
    if not message:
        raise ValueError("Progress message must not be empty.")
    if message_ja is not None:
        message_ja = message_ja.strip()
        if not message_ja:
            raise ValueError("Japanese progress display text must not be empty.")

    created_at = utc_now()

    with database_session() as connection:
        cursor = connection.execute(
            "INSERT INTO progress (message, created_at, message_ja) VALUES (?, ?, ?)",
            (message, created_at, message_ja),
        )
        progress_id = cursor.lastrowid

    return {
        "id": progress_id,
        "message": message,
        "created_at": created_at,
        "message_ja": message_ja,
    }


def set_progress_translation(progress_id: int, message_ja: str) -> None:
    message_ja = message_ja.strip()
    if not message_ja:
        raise ValueError("Japanese progress display text must not be empty.")
    with database_session() as connection:
        cursor = connection.execute(
            "UPDATE progress SET message_ja = ? WHERE id = ?",
            (message_ja, progress_id),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"Progress record {progress_id} does not exist.")


def list_progress(limit: int = 50) -> list[dict[str, object]]:
    with database_session() as connection:
        rows = connection.execute(
            """
            SELECT id, message, created_at, message_ja
            FROM progress
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [
        {"id": row[0], "message": row[1], "created_at": row[2], "message_ja": row[3]}
        for row in rows
    ]


def set_display_translation(entity_type: str, entity_id: int, ja_text: str) -> None:
    sources = {
        "task": ("current_task", "title"),
        "question": ("questions", "question"),
        "artifact": ("artifacts", "display_name"),
    }
    if entity_type not in sources:
        raise ValueError("Unsupported display translation type.")
    ja_text = ja_text.strip()
    if not ja_text:
        raise ValueError("Japanese display text must not be empty.")
    table, column = sources[entity_type]
    with database_session() as connection:
        row = connection.execute(
            f"SELECT {column} FROM {table} WHERE id = ?", (entity_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"{entity_type} record {entity_id} does not exist.")
        connection.execute(
            """
            INSERT INTO display_translations (entity_type, entity_id, source_text, ja_text)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(entity_type, entity_id) DO UPDATE SET
                source_text = excluded.source_text,
                ja_text = excluded.ja_text
            """,
            (entity_type, entity_id, row[0], ja_text),
        )


def start_task(title: str) -> dict[str, str | None]:
    title = title.strip()
    if not title:
        raise ValueError("Task title must not be empty.")

    with database_session() as connection:
        connection.execute("BEGIN IMMEDIATE")
        started_at = utc_now()
        previous = connection.execute(
            "SELECT title, started_at FROM current_task WHERE id = 1"
        ).fetchone()
        if previous is not None:
            connection.execute(
                """
                INSERT INTO task_runs (title, started_at, ended_at, outcome)
                VALUES (?, ?, ?, 'replaced')
                """,
                (previous[0], previous[1], started_at),
            )
        connection.execute(
            """
            INSERT INTO current_task (id, title, started_at)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                started_at = excluded.started_at
            """,
            (title, started_at),
        )

    return {
        "title": title,
        "started_at": started_at,
        "title_ja": None,
    }


def get_current_task() -> dict[str, str | None] | None:
    with database_session() as connection:
        row = connection.execute(
            """
            SELECT task.title, task.started_at, translation.ja_text
            FROM current_task AS task
            LEFT JOIN display_translations AS translation
              ON translation.entity_type = 'task'
             AND translation.entity_id = task.id
             AND translation.source_text = task.title
            WHERE task.id = 1
            """
        ).fetchone()

    if row is None:
        return None

    return {
        "title": row[0],
        "started_at": row[1],
        "title_ja": row[2],
    }


def complete_task() -> bool:
    with database_session() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute(
            "SELECT title, started_at FROM current_task WHERE id = 1"
        ).fetchone()
        if current is None:
            return False
        connection.execute(
            """
            INSERT INTO task_runs (title, started_at, ended_at, outcome)
            VALUES (?, ?, ?, 'completed')
            """,
            (current[0], current[1], utc_now()),
        )
        connection.execute("DELETE FROM current_task WHERE id = 1")
        return True


TELEGRAM_TOKEN_ENV = "AI_AGENT_MONITOR_TELEGRAM_BOT_TOKEN"
TELEGRAM_REPLY_MAPPING_LOCK = threading.RLock()
SMTP_PASSWORD_ENV = "AI_AGENT_MONITOR_SMTP_PASSWORD"
NOTIFICATION_POLL_SECONDS = 5.0
NOTIFICATION_RETRY_SECONDS = 60.0


def notification_config_path() -> Path:
    return DATA_DIR / "notifications.json"


def default_notification_config() -> dict[str, object]:
    return {
        "telegram": {
            "enabled": False,
            "chat_id": None,
            "replies_enabled": False,
        },
        "email": {
            "enabled": False,
            "recipients": [],
            "from_address": None,
            "smtp_host": None,
            "smtp_port": 587,
            "username": None,
            "security": "starttls",
        },
    }


def read_notification_config() -> dict[str, object]:
    config = default_notification_config()
    path = notification_config_path()
    if not path.is_file():
        return config

    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Notification config is invalid: {path}") from exc
    if not isinstance(stored, dict):
        raise RuntimeError(f"Notification config is invalid: {path}")

    for channel in ("telegram", "email"):
        value = stored.get(channel)
        if isinstance(value, dict):
            config[channel].update(value)

    email = config["email"]
    legacy_to = email.pop("to", None)
    recipients = email.get("recipients")
    if not isinstance(recipients, list):
        recipients = []
    recipients = [
        value.strip()
        for value in recipients
        if isinstance(value, str) and value.strip()
    ]
    if isinstance(legacy_to, str) and legacy_to.strip():
        if legacy_to.strip() not in recipients:
            recipients.insert(0, legacy_to.strip())
    email["recipients"] = recipients
    return config


def write_notification_config(config: dict[str, object]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _ensure_project_gitignore(DATA_DIR / ".gitignore")
    path = notification_config_path()
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def configure_telegram(chat_id: str) -> dict[str, object]:
    chat_id = chat_id.strip()
    if not chat_id:
        raise ValueError("Telegram chat ID must not be empty.")
    config = read_notification_config()
    telegram = config["telegram"]
    telegram["chat_id"] = chat_id
    write_notification_config(config)
    return telegram


def _normalize_email_address(address: str) -> str:
    value = address.strip()
    if not value or "\n" in value or "\r" in value:
        raise ValueError("Email address must not be empty or contain newlines.")
    parsed = parseaddr(value)[1]
    if (
        not parsed
        or "@" not in parsed
        or parsed.startswith("@")
        or parsed.endswith("@")
        or any(character.isspace() for character in parsed)
    ):
        raise ValueError(f"Invalid email address: {address}")
    return parsed


def _normalize_email_recipients(addresses: list[str]) -> list[str]:
    recipients: list[str] = []
    seen: set[str] = set()
    for address in addresses:
        normalized = _normalize_email_address(address)
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        recipients.append(normalized)
    return recipients


def email_recipients() -> list[str]:
    config = read_notification_config()["email"]
    recipients = config.get("recipients")
    if not isinstance(recipients, list):
        return []
    return _normalize_email_recipients(
        [value for value in recipients if isinstance(value, str)]
    )


def configure_email(
    *,
    to_address: str | None = None,
    to_addresses: list[str] | None = None,
    from_address: str,
    smtp_host: str,
    smtp_port: int,
    username: str | None,
    security: str,
) -> dict[str, object]:
    requested = list(to_addresses or [])
    if to_address:
        requested.append(to_address)
    recipients = _normalize_email_recipients(requested)
    from_address = _normalize_email_address(from_address)
    smtp_host = smtp_host.strip()
    username = username.strip() if username else None
    if not recipients or not from_address or not smtp_host:
        raise ValueError("At least one email recipient, sender and SMTP host are required.")
    if smtp_port < 1 or smtp_port > 65535:
        raise ValueError("SMTP port must be between 1 and 65535.")
    if security not in {"starttls", "ssl", "none"}:
        raise ValueError("Email security must be starttls, ssl or none.")

    config = read_notification_config()
    email = config["email"]
    email.update(
        {
            "recipients": recipients,
            "from_address": from_address,
            "smtp_host": smtp_host,
            "smtp_port": smtp_port,
            "username": username,
            "security": security,
        }
    )
    write_notification_config(config)
    return email


def add_email_recipients(addresses: list[str]) -> list[str]:
    additions = _normalize_email_recipients(addresses)
    config = read_notification_config()
    email = config["email"]
    existing = email_recipients()
    recipients = _normalize_email_recipients(existing + additions)
    email["recipients"] = recipients
    write_notification_config(config)
    return recipients


def remove_email_recipients(addresses: list[str]) -> list[str]:
    removals = {
        address.casefold()
        for address in _normalize_email_recipients(addresses)
    }
    config = read_notification_config()
    email = config["email"]
    recipients = [
        address
        for address in email_recipients()
        if address.casefold() not in removals
    ]
    email["recipients"] = recipients
    if not recipients:
        email["enabled"] = False
    write_notification_config(config)

    if DB_PATH.is_file() and removals:
        cancelled_at = utc_now()
        with database_session() as connection:
            pending = connection.execute(
                """
                SELECT id, notification_id, target
                FROM notification_deliveries
                WHERE channel = 'email' AND status = 'pending'
                """
            ).fetchall()
            matching = [
                (int(row[0]), int(row[1]))
                for row in pending
                if str(row[2]).casefold() in removals
            ]
            for delivery_id, _ in matching:
                connection.execute(
                    """
                    UPDATE notification_deliveries
                    SET status = 'cancelled', cancelled_at = ?
                    WHERE id = ?
                    """,
                    (cancelled_at, delivery_id),
                )
            for notification_id in sorted({item[1] for item in matching}):
                _refresh_notification_status(connection, notification_id)

    return recipients


def set_notification_channel_enabled(channel: str, enabled: bool) -> dict[str, object]:
    if channel not in {"telegram", "email"}:
        raise ValueError("Notification channel must be telegram or email.")

    config = read_notification_config()
    settings = config[channel]
    if enabled:
        if channel == "telegram":
            if not settings.get("chat_id"):
                raise RuntimeError("Configure a Telegram chat ID before enabling Telegram.")
            if not os.environ.get(TELEGRAM_TOKEN_ENV):
                raise RuntimeError(
                    f"Set {TELEGRAM_TOKEN_ENV} before enabling Telegram."
                )
        else:
            required = ("from_address", "smtp_host", "smtp_port")
            if (
                not email_recipients()
                or any(not settings.get(key) for key in required)
            ):
                raise RuntimeError("Configure email SMTP settings before enabling email.")
            if settings.get("username") and not os.environ.get(SMTP_PASSWORD_ENV):
                raise RuntimeError(
                    f"Set {SMTP_PASSWORD_ENV} before enabling authenticated email."
                )

    was_receiving_replies = channel == "telegram" and settings.get("replies_enabled") is True
    settings["enabled"] = enabled
    if channel == "telegram" and not enabled:
        settings["replies_enabled"] = False
    write_notification_config(config)
    if was_receiving_replies and not enabled:
        token = os.environ.get(TELEGRAM_TOKEN_ENV)
        if token:
            telegram_inbox.SharedTelegramInbox(token).unregister(project_id())

    if not enabled and DB_PATH.is_file():
        cancelled_at = utc_now()
        with database_session() as connection:
            notification_ids = [
                int(row[0])
                for row in connection.execute(
                    """
                    SELECT DISTINCT notification_id
                    FROM notification_deliveries
                    WHERE channel = ? AND status = 'pending'
                    """,
                    (channel,),
                ).fetchall()
            ]
            connection.execute(
                """
                UPDATE notification_deliveries
                SET status = 'cancelled', cancelled_at = ?
                WHERE channel = ? AND status = 'pending'
                """,
                (cancelled_at, channel),
            )
            for notification_id in notification_ids:
                _refresh_notification_status(connection, notification_id)

    return settings


def set_telegram_replies_enabled(enabled: bool) -> dict[str, object]:
    config = read_notification_config()
    telegram = config["telegram"]
    previously_enabled = telegram.get("replies_enabled") is True
    token = os.environ.get(TELEGRAM_TOKEN_ENV)
    if enabled:
        if telegram.get("enabled") is not True or not telegram.get("chat_id"):
            raise RuntimeError("Enable Telegram notifications and configure a chat first.")
        if not token:
            raise RuntimeError(f"Set {TELEGRAM_TOKEN_ENV} before enabling replies.")
        # The former project-local cursor is retained for migration to the
        # bot-wide inbox when an already-enabled project upgrades.
        with database_session() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO telegram_inbox_cursor (id, next_offset) VALUES (1, 0)"
            )
            legacy_offset = int(connection.execute(
                "SELECT next_offset FROM telegram_inbox_cursor WHERE id = 1"
            ).fetchone()[0])
        telegram_inbox.SharedTelegramInbox(token).register(
            project_id(),
            start_at_current=not previously_enabled,
            legacy_offset=legacy_offset,
        )
    elif token:
        telegram_inbox.SharedTelegramInbox(token).unregister(project_id())
    telegram["replies_enabled"] = enabled
    write_notification_config(config)
    return telegram


def notification_config_public() -> dict[str, object]:
    config = read_notification_config()
    return {
        "telegram": {
            **config["telegram"],
            "token_env": TELEGRAM_TOKEN_ENV,
            "token_available": bool(os.environ.get(TELEGRAM_TOKEN_ENV)),
        },
        "email": {
            **config["email"],
            "password_env": SMTP_PASSWORD_ENV,
            "password_available": bool(os.environ.get(SMTP_PASSWORD_ENV)),
        },
    }


def discover_telegram_chat_id() -> str:
    token = os.environ.get(TELEGRAM_TOKEN_ENV)
    if not token:
        raise RuntimeError(f"{TELEGRAM_TOKEN_ENV} is not set.")

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError("Telegram getUpdates failed.") from exc

    if not isinstance(payload, dict) or payload.get("ok") is not True:
        description = payload.get("description") if isinstance(payload, dict) else None
        raise RuntimeError(f"Telegram getUpdates failed: {description or 'unknown error'}")

    results = payload.get("result")
    if not isinstance(results, list):
        raise RuntimeError("Telegram getUpdates returned unexpected data.")

    for update in reversed(results):
        if not isinstance(update, dict):
            continue
        message = update.get("message")
        if not isinstance(message, dict):
            continue
        chat = message.get("chat")
        if not isinstance(chat, dict):
            continue
        chat_id = chat.get("id")
        if isinstance(chat_id, int):
            return str(chat_id)

    raise RuntimeError(
        "No Telegram chat was found. Send /start to the bot, then run discovery again."
    )


def enabled_notification_targets() -> list[tuple[str, str]]:
    config = read_notification_config()
    targets: list[tuple[str, str]] = []

    telegram = config["telegram"]
    if telegram.get("enabled") is True:
        targets.append(("telegram", ""))

    email = config["email"]
    if email.get("enabled") is True:
        targets.extend(("email", recipient) for recipient in email_recipients())

    return targets


def _enqueue_notification_event(
    connection: sqlite3.Connection,
    *,
    event_type: str,
    entity_type: str,
    entity_id: int,
    payload: dict[str, object],
    created_at: str,
) -> int:
    targets = enabled_notification_targets()
    initial_status = "pending" if targets else "cancelled"
    cancelled_at = None if targets else created_at

    cursor = connection.execute(
        """
        INSERT INTO notification_outbox (
            event_type,
            entity_type,
            entity_id,
            payload_json,
            status,
            attempt_count,
            created_at,
            cancelled_at
        )
        VALUES (?, ?, ?, ?, ?, 0, ?, ?)
        ON CONFLICT(event_type, entity_type, entity_id) DO NOTHING
        """,
        (
            event_type,
            entity_type,
            entity_id,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            initial_status,
            created_at,
            cancelled_at,
        ),
    )
    if cursor.rowcount > 0 and cursor.lastrowid is not None:
        notification_id = int(cursor.lastrowid)
        for channel, target in targets:
            connection.execute(
                """
                INSERT INTO notification_deliveries (
                    notification_id,
                    channel,
                    target,
                    status,
                    attempt_count,
                    created_at
                )
                VALUES (?, ?, ?, 'pending', 0, ?)
                """,
                (notification_id, channel, target, created_at),
            )
        return notification_id

    row = connection.execute(
        """
        SELECT id
        FROM notification_outbox
        WHERE event_type = ? AND entity_type = ? AND entity_id = ?
        """,
        (event_type, entity_type, entity_id),
    ).fetchone()
    if row is None:
        raise RuntimeError("Notification outbox event could not be created.")
    return int(row[0])


def list_notification_outbox(
    *,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, object]]:
    if limit < 1:
        raise ValueError("Notification limit must be at least 1.")
    if status is not None and status not in {"pending", "delivered", "cancelled"}:
        raise ValueError(
            "Notification status must be 'pending', 'delivered' or 'cancelled'."
        )

    query = """
        SELECT
            id,
            event_type,
            entity_type,
            entity_id,
            payload_json,
            status,
            attempt_count,
            created_at,
            last_attempt_at,
            delivered_at,
            cancelled_at,
            last_error
        FROM notification_outbox
    """
    parameters: list[object] = []
    if status is not None:
        query += " WHERE status = ?"
        parameters.append(status)
    query += " ORDER BY id ASC LIMIT ?"
    parameters.append(limit)

    with database_session() as connection:
        rows = connection.execute(query, tuple(parameters)).fetchall()

    return [
        {
            "id": row[0],
            "event_type": row[1],
            "entity_type": row[2],
            "entity_id": row[3],
            "payload": json.loads(row[4]),
            "status": row[5],
            "attempt_count": row[6],
            "created_at": row[7],
            "last_attempt_at": row[8],
            "delivered_at": row[9],
            "cancelled_at": row[10],
            "last_error": row[11],
        }
        for row in rows
    ]


def list_notification_deliveries(
    *,
    notification_id: int | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, object]]:
    if limit < 1:
        raise ValueError("Delivery limit must be at least 1.")
    if status is not None and status not in {"pending", "delivered", "cancelled"}:
        raise ValueError("Delivery status must be pending, delivered or cancelled.")

    clauses: list[str] = []
    parameters: list[object] = []
    if notification_id is not None:
        clauses.append("notification_id = ?")
        parameters.append(notification_id)
    if status is not None:
        clauses.append("status = ?")
        parameters.append(status)

    query = """
        SELECT
            id,
            notification_id,
            channel,
            target,
            status,
            attempt_count,
            created_at,
            last_attempt_at,
            delivered_at,
            cancelled_at,
            last_error,
            telegram_message_id,
            telegram_chat_id
        FROM notification_deliveries
    """
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id ASC LIMIT ?"
    parameters.append(limit)

    with database_session() as connection:
        rows = connection.execute(query, tuple(parameters)).fetchall()

    return [
        {
            "id": row[0],
            "notification_id": row[1],
            "channel": row[2],
            "target": row[3],
            "status": row[4],
            "attempt_count": row[5],
            "created_at": row[6],
            "last_attempt_at": row[7],
            "delivered_at": row[8],
            "cancelled_at": row[9],
            "last_error": row[10],
            "telegram_message_id": row[11],
            "telegram_chat_id": row[12],
        }
        for row in rows
    ]


def _refresh_notification_status(
    connection: sqlite3.Connection,
    notification_id: int,
) -> None:
    rows = connection.execute(
        """
        SELECT channel, target, status, attempt_count, last_attempt_at, delivered_at, last_error
        FROM notification_deliveries
        WHERE notification_id = ?
        """,
        (notification_id,),
    ).fetchall()
    if not rows:
        return

    statuses = {row[2] for row in rows}
    attempt_count = sum(int(row[3]) for row in rows)
    last_attempts = [row[4] for row in rows if row[4]]
    delivered_times = [row[5] for row in rows if row[5]]
    errors = [
        f"{row[0]}{f' ({row[1]})' if row[1] else ''}: {row[6]}"
        for row in rows
        if row[6]
    ]

    if "pending" in statuses:
        status = "pending"
        delivered_at = None
        cancelled_at = None
    elif "delivered" in statuses:
        status = "delivered"
        delivered_at = max(delivered_times) if delivered_times else utc_now()
        cancelled_at = None
    else:
        status = "cancelled"
        delivered_at = None
        cancelled_at = utc_now()

    connection.execute(
        """
        UPDATE notification_outbox
        SET
            status = ?,
            attempt_count = ?,
            last_attempt_at = ?,
            delivered_at = ?,
            cancelled_at = ?,
            last_error = ?
        WHERE id = ?
        """,
        (
            status,
            attempt_count,
            max(last_attempts) if last_attempts else None,
            delivered_at,
            cancelled_at,
            "; ".join(errors) if errors else None,
            notification_id,
        ),
    )


def record_notification_delivery_attempt(
    delivery_id: int,
    *,
    delivered: bool,
    error: str | None = None,
    telegram_message_id: int | None = None,
    telegram_chat_id: str | None = None,
) -> dict[str, object]:
    attempted_at = utc_now()
    with database_session() as connection:
        row = connection.execute(
            """
            SELECT notification_id, channel, target, status, attempt_count
            FROM notification_deliveries
            WHERE id = ?
            """,
            (delivery_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Notification delivery {delivery_id} does not exist.")

        notification_id = int(row[0])
        channel = str(row[1])
        target = str(row[2])
        current_status = str(row[3])
        attempt_count = int(row[4])

        if current_status in {"delivered", "cancelled"}:
            return {
                "id": delivery_id,
                "notification_id": notification_id,
                "channel": channel,
                "target": target,
                "status": current_status,
                "attempt_count": attempt_count,
            }

        attempt_count += 1
        if delivered:
            connection.execute(
                """
                UPDATE notification_deliveries
                SET
                    status = 'delivered',
                    attempt_count = ?,
                    last_attempt_at = ?,
                    delivered_at = ?,
                    telegram_message_id = ?,
                    telegram_chat_id = ?,
                    last_error = NULL
                WHERE id = ?
                """,
                (
                    attempt_count, attempted_at, attempted_at,
                    telegram_message_id, telegram_chat_id, delivery_id,
                ),
            )
            status = "delivered"
        else:
            message = (error or "Notification delivery failed.").strip()
            connection.execute(
                """
                UPDATE notification_deliveries
                SET
                    status = 'pending',
                    attempt_count = ?,
                    last_attempt_at = ?,
                    last_error = ?
                WHERE id = ?
                """,
                (attempt_count, attempted_at, message, delivery_id),
            )
            status = "pending"

        _refresh_notification_status(connection, notification_id)
        return {
            "id": delivery_id,
            "notification_id": notification_id,
            "channel": channel,
            "target": target,
            "status": status,
            "attempt_count": attempt_count,
        }


def _notification_dashboard_url() -> str | None:
    try:
        state = read_remote_state()
    except (RuntimeError, OSError):
        return None
    if state is None:
        return None
    url = state.get("tailnet_url")
    return url if isinstance(url, str) and url else None


def _notification_text(
    payload: dict[str, object], *, telegram_reply: bool = False
) -> str:
    project = payload.get("project")
    project_name = (
        project.get("name")
        if isinstance(project, dict) and isinstance(project.get("name"), str)
        else PROJECT_ROOT.name
    )
    question_id = payload.get("question_id")
    question = payload.get("question")
    if isinstance(question_id, int) and DB_PATH.is_file():
        with database_session() as connection:
            translation = connection.execute(
                """
                SELECT translation.ja_text
                FROM questions AS question
                JOIN display_translations AS translation
                  ON translation.entity_type = 'question'
                 AND translation.entity_id = question.id
                 AND translation.source_text = question.question
                WHERE question.id = ?
                """,
                (question_id,),
            ).fetchone()
        if translation is not None:
            question = translation[0]
    lines = [
        "AIエージェント・モニター",
        f"案件: {project_name}",
        f"質問 #{question_id}: {question}",
    ]
    if telegram_reply:
        lines.append("この通知に返信すると回答を保存します。")
    dashboard_url = _notification_dashboard_url()
    if dashboard_url:
        lines.extend(["", f"画面: {dashboard_url}"])
    return "\n".join(lines)


def _send_telegram_notification(payload: dict[str, object]) -> int | None:
    config = read_notification_config()["telegram"]
    if config.get("enabled") is not True:
        raise RuntimeError("Telegram notifications are disabled.")

    chat_id = config.get("chat_id")
    if not isinstance(chat_id, str) or not chat_id:
        raise RuntimeError("Telegram chat ID is not configured.")

    token = os.environ.get(TELEGRAM_TOKEN_ENV)
    if not token:
        raise RuntimeError(f"{TELEGRAM_TOKEN_ENV} is not set.")

    text = _notification_text(
        payload, telegram_reply=config.get("replies_enabled") is True
    )
    if len(text) > 4096:
        text = text[:4093] + "..."

    message: dict[str, object] = {"chat_id": chat_id, "text": text}
    if config.get("replies_enabled") is True:
        message["reply_markup"] = {
            "force_reply": True,
            "input_field_placeholder": "回答を入力してください",
        }
    body = json.dumps(message, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            error_payload = json.loads(exc.read().decode("utf-8"))
            description = error_payload.get("description")
        except (json.JSONDecodeError, UnicodeDecodeError):
            description = None
        raise RuntimeError(
            f"Telegram sendMessage failed: {description or f'HTTP {exc.code}'}"
        ) from exc
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError("Telegram sendMessage failed.") from exc

    if not isinstance(result, dict) or result.get("ok") is not True:
        description = result.get("description") if isinstance(result, dict) else None
        raise RuntimeError(
            f"Telegram sendMessage failed: {description or 'unknown error'}"
        )
    sent = result.get("result")
    message_id = sent.get("message_id") if isinstance(sent, dict) else None
    if isinstance(message_id, int) and not isinstance(message_id, bool):
        return message_id
    if config.get("replies_enabled") is True:
        raise RuntimeError("Telegram did not return the sent message ID.")
    return None


def _telegram_api_request(
    method: str, payload: dict[str, object], *, timeout: int = 10
) -> object:
    token = os.environ.get(TELEGRAM_TOKEN_ENV)
    if not token:
        raise RuntimeError(f"{TELEGRAM_TOKEN_ENV} is not set.")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Telegram {method} failed (HTTP {exc.code}).") from exc
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Telegram {method} failed.") from exc
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError(f"Telegram {method} failed.")
    return result.get("result")


def _telegram_answer_from_update(update: dict[str, object], chat_id: str) -> bool:
    message = update.get("message")
    if not isinstance(message, dict):
        return False
    chat = message.get("chat")
    sender = message.get("from")
    if (
        not isinstance(chat, dict)
        or chat.get("type") != "private"
        or str(chat.get("id")) != chat_id
        or not isinstance(sender, dict)
        or sender.get("is_bot") is True
        or sender.get("id") != chat.get("id")
    ):
        return False
    answer = message.get("text")
    reply_to = message.get("reply_to_message")
    if (
        not isinstance(answer, str)
        or not answer.strip()
        or answer.lstrip().startswith("/")
        or not isinstance(reply_to, dict)
        or not isinstance(reply_to.get("message_id"), int)
    ):
        return False
    with TELEGRAM_REPLY_MAPPING_LOCK:
        with database_session() as connection:
            row = connection.execute(
                """
                SELECT event.entity_id
                FROM notification_deliveries AS delivery
                JOIN notification_outbox AS event ON event.id = delivery.notification_id
                WHERE delivery.channel = 'telegram'
                  AND delivery.status = 'delivered'
                  AND delivery.telegram_message_id = ?
                  AND delivery.telegram_chat_id = ?
                  AND event.event_type = 'question.created'
                  AND event.entity_type = 'question'
                """,
                (reply_to["message_id"], chat_id),
            ).fetchone()
    if row is None:
        return False
    question_id = int(row[0])
    try:
        answer_question(question_id, answer)
    except QuestionClosedError:
        confirmation = f"質問 #{question_id} は回答済みです。"
    else:
        confirmation = f"質問 #{question_id} の回答を保存しました。"
    try:
        _telegram_api_request(
            "sendMessage", {"chat_id": chat_id, "text": confirmation}
        )
    except RuntimeError:
        # The answer is already committed. A failed acknowledgement must not
        # cause the same Telegram update to create another answer.
        pass
    return True


def poll_telegram_replies_once() -> dict[str, int]:
    telegram = read_notification_config()["telegram"]
    if telegram.get("enabled") is not True or telegram.get("replies_enabled") is not True:
        return {"updates": 0, "answers": 0}
    chat_id = telegram.get("chat_id")
    if not isinstance(chat_id, str) or not chat_id:
        raise RuntimeError("Telegram chat ID is not configured.")
    token = os.environ.get(TELEGRAM_TOKEN_ENV)
    if not token:
        raise RuntimeError(f"{TELEGRAM_TOKEN_ENV} is not set.")
    inbox = telegram_inbox.SharedTelegramInbox(token)
    with database_session() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO telegram_inbox_cursor (id, next_offset) VALUES (1, 0)"
        )
        legacy_offset = int(connection.execute(
            "SELECT next_offset FROM telegram_inbox_cursor WHERE id = 1"
        ).fetchone()[0])
    inbox.register(project_id(), start_at_current=False, legacy_offset=legacy_offset)
    processed = 0
    answered = 0

    def process_saved() -> None:
        nonlocal processed, answered
        for update in inbox.pending(project_id()):
            update_id = update.get("update_id")
            if isinstance(update_id, bool) or not isinstance(update_id, int):
                continue
            if _telegram_answer_from_update(update, chat_id):
                answered += 1
            inbox.advance(project_id(), update_id + 1)
            with database_session() as connection:
                connection.execute(
                    "UPDATE telegram_inbox_cursor "
                    "SET next_offset = MAX(next_offset, ?) WHERE id = 1",
                    (update_id + 1,),
                )
            processed += 1

    # A saved reply can be answered even while a new Telegram request fails.
    process_saved()
    claim = inbox.claim_poll()
    if claim is not None:
        claim_id, next_offset = claim
        try:
            updates = _telegram_api_request(
                "getUpdates",
                {
                    "offset": next_offset,
                    "limit": 100,
                    "timeout": 3,
                    "allowed_updates": ["message"],
                },
                timeout=8,
            )
            if not isinstance(updates, list):
                raise RuntimeError("Telegram getUpdates returned unexpected data.")
            inbox.save_poll(claim_id, updates)
        finally:
            inbox.release_poll(claim_id)
    process_saved()
    return {"updates": processed, "answers": answered}


def _telegram_reply_loop(stop_event: threading.Event) -> None:
    while not stop_event.wait(2):
        try:
            poll_telegram_replies_once()
        except Exception:
            # Poll failures are retried; the persisted offset is advanced only
            # after an update has been processed successfully.
            continue


def _send_email_notification(
    payload: dict[str, object],
    recipient: str | None = None,
) -> None:
    config = read_notification_config()["email"]
    if config.get("enabled") is not True:
        raise RuntimeError("Email notifications are disabled.")

    recipients = email_recipients()
    if recipient is None:
        if len(recipients) != 1:
            raise RuntimeError(
                "Email delivery target is required when multiple recipients are configured."
            )
        recipient = recipients[0]
    recipient = _normalize_email_address(recipient)
    from_address = config.get("from_address")
    smtp_host = config.get("smtp_host")
    smtp_port = config.get("smtp_port")
    username = config.get("username")
    security = config.get("security")

    if not all(
        [
            recipient,
            isinstance(from_address, str) and from_address,
            isinstance(smtp_host, str) and smtp_host,
            isinstance(smtp_port, int),
            security in {"starttls", "ssl", "none"},
        ]
    ):
        raise RuntimeError("Email notification settings are incomplete.")

    password = os.environ.get(SMTP_PASSWORD_ENV) if username else None
    if username and not password:
        raise RuntimeError(f"{SMTP_PASSWORD_ENV} is not set.")

    project = payload.get("project")
    project_name = (
        project.get("name")
        if isinstance(project, dict) and isinstance(project.get("name"), str)
        else PROJECT_ROOT.name
    )
    message = EmailMessage()
    message["Subject"] = f"[AI Agent Monitor] {project_name} needs your answer"
    message["From"] = from_address
    message["To"] = recipient
    message.set_content(_notification_text(payload))

    context = ssl.create_default_context()
    if security == "ssl":
        smtp_class = smtplib.SMTP_SSL
        with smtp_class(smtp_host, smtp_port, timeout=10, context=context) as client:
            if username:
                client.login(str(username), str(password))
            client.send_message(message)
        return

    with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as client:
        client.ehlo()
        if security == "starttls":
            client.starttls(context=context)
            client.ehlo()
        if username:
            client.login(str(username), str(password))
        client.send_message(message)


def _pending_notification_jobs(
    limit: int = 20,
    *,
    force: bool = False,
) -> list[dict[str, object]]:
    with database_session() as connection:
        rows = connection.execute(
            """
            SELECT
                d.id,
                d.channel,
                d.target,
                d.last_attempt_at,
                o.payload_json
            FROM notification_deliveries AS d
            JOIN notification_outbox AS o ON o.id = d.notification_id
            WHERE d.status = 'pending' AND o.status = 'pending'
            ORDER BY d.id ASC
            LIMIT ?
            """,
            (limit * 4,),
        ).fetchall()

    now = datetime.now(timezone.utc)
    jobs: list[dict[str, object]] = []
    for row in rows:
        last_attempt_at = row[3]
        if not force and isinstance(last_attempt_at, str) and last_attempt_at:
            try:
                last_attempt = datetime.fromisoformat(
                    last_attempt_at.replace("Z", "+00:00")
                )
            except ValueError:
                last_attempt = None
            if (
                last_attempt is not None
                and (now - last_attempt).total_seconds() < NOTIFICATION_RETRY_SECONDS
            ):
                continue

        jobs.append(
            {
                "delivery_id": int(row[0]),
                "channel": str(row[1]),
                "target": str(row[2]),
                "payload": json.loads(row[4]),
            }
        )
        if len(jobs) >= limit:
            break

    return jobs


def dispatch_pending_notifications(
    limit: int = 20,
    *,
    force: bool = False,
) -> dict[str, int]:
    attempted = 0
    delivered = 0
    failed = 0

    for job in _pending_notification_jobs(limit, force=force):
        attempted += 1
        delivery_id = int(job["delivery_id"])
        channel = str(job["channel"])
        target = str(job["target"])
        payload = job["payload"]
        telegram_message_id = None
        with TELEGRAM_REPLY_MAPPING_LOCK if channel == "telegram" else nullcontext():
            try:
                if channel == "telegram":
                    telegram_message_id = _send_telegram_notification(payload)
                elif channel == "email":
                    _send_email_notification(payload, target)
                else:
                    raise RuntimeError(f"Unsupported notification channel: {channel}")
            except Exception as exc:
                record_notification_delivery_attempt(
                    delivery_id,
                    delivered=False,
                    error=str(exc),
                )
                failed += 1
            else:
                record_notification_delivery_attempt(
                    delivery_id,
                    delivered=True,
                    telegram_message_id=(
                        telegram_message_id
                        if isinstance(telegram_message_id, int)
                        and not isinstance(telegram_message_id, bool)
                        else None
                    ),
                    telegram_chat_id=(
                        str(read_notification_config()["telegram"]["chat_id"])
                        if channel == "telegram" else None
                    ),
                )
                delivered += 1

    return {
        "attempted": attempted,
        "delivered": delivered,
        "failed": failed,
    }


def _notification_dispatch_loop(stop_event: threading.Event) -> None:
    while not stop_event.wait(NOTIFICATION_POLL_SECONDS):
        try:
            dispatch_pending_notifications(force=False)
        except Exception:
            # Individual delivery failures are persisted by the dispatcher.
            # Unexpected loop errors must not stop the dashboard server.
            continue


RUNNER_POLL_SECONDS = 5.0
RUNNER_CONFIG_FILENAME = "runner.json"
DEFAULT_CODEX_COMMAND = "codex"


def runner_config_path() -> Path:
    return DATA_DIR / RUNNER_CONFIG_FILENAME


def default_runner_config() -> dict[str, object]:
    return {
        "auto_resume": False,
        "codex_command": DEFAULT_CODEX_COMMAND,
    }


def read_runner_config() -> dict[str, object]:
    config = default_runner_config()
    path = runner_config_path()
    if not path.is_file():
        return config
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Runner config is invalid: {path}") from exc
    if not isinstance(stored, dict):
        raise RuntimeError(f"Runner config is invalid: {path}")
    config.update(stored)
    return config


def write_runner_config(config: dict[str, object]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _ensure_project_gitignore(DATA_DIR / ".gitignore")
    path = runner_config_path()
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def configure_codex_command(command: str) -> str:
    command = command.strip()
    if not command:
        raise ValueError("Codex command must not be empty.")
    config = read_runner_config()
    config["codex_command"] = command
    write_runner_config(config)
    return command


def set_auto_resume_enabled(enabled: bool) -> dict[str, object]:
    config = read_runner_config()
    if enabled:
        command = str(config.get("codex_command") or DEFAULT_CODEX_COMMAND)
        if Path(command).is_absolute():
            if not Path(command).is_file():
                raise RuntimeError(f"Configured Codex executable does not exist: {command}")
        elif shutil.which(command) is None:
            raise RuntimeError(
                "Codex executable was not found. Configure it before enabling auto-resume."
            )
    config["auto_resume"] = enabled
    write_runner_config(config)
    return config


def runner_config_public() -> dict[str, object]:
    config = read_runner_config()
    command = str(config.get("codex_command") or DEFAULT_CODEX_COMMAND)
    resolved = str(Path(command)) if Path(command).is_absolute() else shutil.which(command)
    return {
        "auto_resume": config.get("auto_resume") is True,
        "codex_command": command,
        "codex_available": bool(resolved),
        "codex_resolved": resolved,
    }


def _resume_prompt(question_id: int) -> str:
    return (
        "Continue the current monitored project work after the human answer to "
        f"question #{question_id}. First run monitor runner register codex and "
        "monitor status, read the stored answer, then continue the existing task. "
        "Use the repository instructions and AI Agent Monitor contract. If the task "
        "is complete, run the required validation, record meaningful progress, run "
        "monitor task done, and then monitor runner complete. If another genuine "
        "human decision is required, use monitor ask and stop at that decision."
    )


def _claim_resume_request() -> tuple[int, int] | None:
    now = utc_now()
    with database_session() as connection:
        row = connection.execute(
            """
            SELECT r.id, r.runner_id, runner.state
            FROM resume_requests AS r
            LEFT JOIN agent_runners AS runner ON runner.id = r.runner_id
            WHERE r.status = 'pending'
            ORDER BY r.id ASC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None

        request_id = int(row[0])
        runner_id = int(row[1]) if row[1] is not None else None
        runner_state = str(row[2]) if row[2] is not None else None
        active_task = connection.execute(
            "SELECT 1 FROM current_task WHERE id = 1"
        ).fetchone()

        cancel_reason = None
        if active_task is None:
            cancel_reason = "no_active_task_before_dispatch"
        elif runner_id is None or runner_state is None:
            cancel_reason = "runner_missing_before_dispatch"
        elif runner_state != "waiting_for_human":
            cancel_reason = f"runner_state_{runner_state}_before_dispatch"

        if cancel_reason is None and runner_id is not None:
            other_active = connection.execute(
                """
                SELECT 1
                FROM resume_requests
                WHERE runner_id = ?
                  AND id != ?
                  AND status IN ('claimed', 'running')
                LIMIT 1
                """,
                (runner_id, request_id),
            ).fetchone()
            if other_active is not None:
                return None

        if cancel_reason is not None:
            connection.execute(
                """
                UPDATE resume_requests
                SET status = 'cancelled', reason = ?, completed_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (cancel_reason, now, request_id),
            )
            return None

        cursor = connection.execute(
            """
            UPDATE resume_requests
            SET status = 'claimed', claimed_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (now, request_id),
        )
        if cursor.rowcount != 1:
            return None
        attempt_cursor = connection.execute(
            """
            INSERT INTO resume_attempts (request_id, status, started_at)
            VALUES (?, 'launching', ?)
            """,
            (request_id, now),
        )
        return request_id, int(attempt_cursor.lastrowid)


def _runner_worker_command(attempt_id: int) -> list[str]:
    return [
        sys.executable,
        "-m",
        "ai_agent_monitor.app",
        "runner",
        "worker",
        str(attempt_id),
    ]


def _spawn_resume_worker(request_id: int, attempt_id: int) -> int:
    runtime = remote_runtime_dir()
    runtime.mkdir(parents=True, exist_ok=True)
    stdout_path = runtime / f"resume-{attempt_id}.stdout.log"
    stderr_path = runtime / f"resume-{attempt_id}.stderr.log"

    kwargs: dict[str, object] = {
        "cwd": str(PROJECT_ROOT),
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )
    else:
        kwargs["start_new_session"] = True

    with (
        stdout_path.open("ab") as stdout_handle,
        stderr_path.open("ab") as stderr_handle,
    ):
        process = subprocess.Popen(
            _runner_worker_command(attempt_id),
            stdout=stdout_handle,
            stderr=stderr_handle,
            **kwargs,
        )

    now = utc_now()
    with database_session() as connection:
        connection.execute(
            """
            UPDATE resume_attempts
            SET status = 'running', pid = ?
            WHERE id = ? AND status = 'launching'
            """,
            (process.pid, attempt_id),
        )
        connection.execute(
            """
            UPDATE resume_requests
            SET status = 'running'
            WHERE id = ? AND status = 'claimed'
            """,
            (request_id,),
        )
    return int(process.pid)


def recover_resume_requests() -> dict[str, int]:
    uncertain = 0
    active = 0
    now = utc_now()
    with database_session() as connection:
        rows = connection.execute(
            """
            SELECT r.id, a.id, a.pid, a.status
            FROM resume_requests AS r
            JOIN resume_attempts AS a ON a.request_id = r.id
            WHERE r.status IN ('claimed', 'running')
            AND a.id = (
                SELECT MAX(a2.id)
                FROM resume_attempts AS a2
                WHERE a2.request_id = r.id
            )
            """
        ).fetchall()
        for request_id, attempt_id, pid, attempt_status in rows:
            if isinstance(pid, int) and _process_is_alive(pid):
                active += 1
                continue
            connection.execute(
                """
                UPDATE resume_requests
                SET status = 'uncertain', reason = ?
                WHERE id = ?
                """,
                ("worker_missing_after_restart", request_id),
            )
            connection.execute(
                """
                UPDATE resume_attempts
                SET status = 'uncertain', finished_at = ?,
                    last_error = COALESCE(last_error, ?)
                WHERE id = ?
                """,
                (now, "Worker state could not be proven after restart.", attempt_id),
            )
            uncertain += 1
    return {"active": active, "uncertain": uncertain}


def dispatch_resume_requests(*, force: bool = False) -> dict[str, int]:
    config = read_runner_config()
    if not force and config.get("auto_resume") is not True:
        return {"claimed": 0, "launched": 0}

    recover_resume_requests()
    claimed = _claim_resume_request()
    if claimed is None:
        return {"claimed": 0, "launched": 0}

    request_id, attempt_id = claimed
    try:
        _spawn_resume_worker(request_id, attempt_id)
    except Exception as exc:
        now = utc_now()
        with database_session() as connection:
            connection.execute(
                """
                UPDATE resume_requests
                SET status = 'pending', reason = ?
                WHERE id = ? AND status = 'claimed'
                """,
                (f"launch_failed: {exc}", request_id),
            )
            connection.execute(
                """
                UPDATE resume_attempts
                SET status = 'failed', finished_at = ?, last_error = ?
                WHERE id = ?
                """,
                (now, str(exc), attempt_id),
            )
        return {"claimed": 1, "launched": 0}

    return {"claimed": 1, "launched": 1}


def retry_resume_request(request_id: int) -> dict[str, object]:
    with database_session() as connection:
        row = connection.execute(
            "SELECT status FROM resume_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Resume request {request_id} does not exist.")
        if row[0] != "uncertain":
            raise RuntimeError("Only an uncertain resume request can be retried manually.")
        connection.execute(
            """
            UPDATE resume_requests
            SET status = 'pending', reason = 'manual_retry', claimed_at = NULL
            WHERE id = ?
            """,
            (request_id,),
        )
    return {"id": request_id, "status": "pending"}



def _codex_exec_base_argv(command: str) -> list[str]:
    return [
        command,
        "-c",
        'sandbox_mode="workspace-write"',
        "-c",
        'approval_policy="on-request"',
        "exec",
    ]


def run_codex_preflight() -> dict[str, object]:
    config = read_runner_config()
    command = str(config.get("codex_command") or DEFAULT_CODEX_COMMAND)
    argv = [
        *_codex_exec_base_argv(command),
        "-C",
        str(PROJECT_ROOT),
        "Reply with only: AI_AGENT_MONITOR_CODEX_OK",
    ]
    result = subprocess.run(
        argv,
        cwd=str(PROJECT_ROOT),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = (result.stdout or "").strip()
    error_output = (result.stderr or "").strip()
    ok = result.returncode == 0 and "AI_AGENT_MONITOR_CODEX_OK" in output
    return {
        "ok": ok,
        "exit_code": int(result.returncode),
        "output": output,
        "error": error_output,
    }

def run_resume_worker(attempt_id: int) -> int:
    with database_session() as connection:
        row = connection.execute(
            """
            SELECT
                a.request_id,
                r.question_id,
                r.runner_id,
                runner.adapter,
                runner.external_id,
                r.status
            FROM resume_attempts AS a
            JOIN resume_requests AS r ON r.id = a.request_id
            JOIN agent_runners AS runner ON runner.id = r.runner_id
            WHERE a.id = ?
            """,
            (attempt_id,),
        ).fetchone()

    if row is None:
        raise RuntimeError(f"Resume attempt {attempt_id} does not exist.")

    request_id = int(row[0])
    question_id = int(row[1])
    runner_id = int(row[2])
    adapter = str(row[3])
    external_id = str(row[4])
    request_status = str(row[5])
    if adapter != "codex":
        raise RuntimeError(f"Unsupported runner adapter: {adapter}")
    if request_status not in {"claimed", "running"}:
        return 0

    config = read_runner_config()
    command = str(config.get("codex_command") or DEFAULT_CODEX_COMMAND)
    argv = [
        *_codex_exec_base_argv(command),
        "-C",
        str(PROJECT_ROOT),
        "resume",
        external_id,
        _resume_prompt(question_id),
    ]

    now = utc_now()
    with database_session() as connection:
        connection.execute(
            """
            UPDATE agent_runners
            SET state = 'running', updated_at = ?, last_error = NULL
            WHERE id = ?
            """,
            (now, runner_id),
        )

    try:
        result = subprocess.run(
            argv,
            cwd=str(PROJECT_ROOT),
            stdin=subprocess.DEVNULL,
            timeout=None,
            check=False,
        )
        exit_code = int(result.returncode)
        error = None if exit_code == 0 else f"Codex exited with code {exit_code}."
    except Exception as exc:
        exit_code = -1
        error = str(exc)

    finished_at = utc_now()
    with database_session() as connection:
        if exit_code == 0:
            current_request = connection.execute(
                "SELECT status FROM resume_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
            if current_request is not None and current_request[0] == "running":
                connection.execute(
                    """
                    UPDATE resume_requests
                    SET status = 'completed', completed_at = ?
                    WHERE id = ?
                    """,
                    (finished_at, request_id),
                )
            connection.execute(
                """
                UPDATE resume_attempts
                SET status = 'completed', finished_at = ?, exit_code = 0
                WHERE id = ?
                """,
                (finished_at, attempt_id),
            )
        else:
            connection.execute(
                """
                UPDATE resume_requests
                SET status = 'cancelled', reason = ?, completed_at = ?
                WHERE id = ?
                """,
                (error, finished_at, request_id),
            )
            connection.execute(
                """
                UPDATE resume_attempts
                SET status = 'failed', finished_at = ?, exit_code = ?, last_error = ?
                WHERE id = ?
                """,
                (finished_at, exit_code, error, attempt_id),
            )
            connection.execute(
                """
                UPDATE agent_runners
                SET state = 'failed', updated_at = ?, last_error = ?
                WHERE id = ?
                """,
                (finished_at, error, runner_id),
            )

    return exit_code


def _runner_dispatch_loop(stop_event: threading.Event) -> None:
    recover_resume_requests()
    while not stop_event.wait(RUNNER_POLL_SECONDS):
        try:
            dispatch_resume_requests(force=False)
        except Exception:
            continue


RUNNER_STATES = {"running", "waiting_for_human", "stopped", "completed", "failed"}
RESUME_REQUEST_STATES = {"pending", "claimed", "running", "completed", "cancelled", "uncertain"}
CODEX_THREAD_ID_ENV = "CODEX_THREAD_ID"


def register_runner(adapter: str, external_id: str | None = None) -> dict[str, object]:
    adapter = adapter.strip().lower()
    if adapter != "codex":
        raise ValueError("The first runner adapter supports only codex.")

    if external_id is None:
        external_id = os.environ.get(CODEX_THREAD_ID_ENV)
    external_id = external_id.strip() if external_id else ""
    if not external_id:
        raise RuntimeError(
            f"{CODEX_THREAD_ID_ENV} is not available. Run runner registration from a Codex shell tool."
        )

    now = utc_now()
    with database_session() as connection:
        connection.execute(
            """
            INSERT INTO agent_runners (
                adapter, external_id, state, created_at, updated_at, last_error
            )
            VALUES (?, ?, 'running', ?, ?, NULL)
            ON CONFLICT(adapter, external_id) DO UPDATE SET
                state = 'running',
                updated_at = excluded.updated_at,
                last_error = NULL
            """,
            (adapter, external_id, now, now),
        )
        row = connection.execute(
            """
            SELECT id, adapter, external_id, state, created_at, updated_at, last_error
            FROM agent_runners
            WHERE adapter = ? AND external_id = ?
            """,
            (adapter, external_id),
        ).fetchone()

    return {
        "id": row[0],
        "adapter": row[1],
        "external_id": row[2],
        "state": row[3],
        "created_at": row[4],
        "updated_at": row[5],
        "last_error": row[6],
    }


def _runner_for_current_process(
    connection: sqlite3.Connection,
) -> tuple[object, ...] | None:
    external_id = os.environ.get(CODEX_THREAD_ID_ENV)
    if not external_id:
        return None
    return connection.execute(
        """
        SELECT id, adapter, external_id, state, created_at, updated_at, last_error
        FROM agent_runners
        WHERE adapter = 'codex' AND external_id = ?
        """,
        (external_id,),
    ).fetchone()


def set_current_runner_state(
    state: str,
    *,
    error: str | None = None,
) -> dict[str, object]:
    if state not in RUNNER_STATES:
        raise ValueError(f"Invalid runner state: {state}")

    now = utc_now()
    with database_session() as connection:
        row = _runner_for_current_process(connection)
        if row is None:
            raise RuntimeError(
                "No registered Codex runner matches the current CODEX_THREAD_ID."
            )
        runner_id = int(row[0])
        connection.execute(
            """
            UPDATE agent_runners
            SET state = ?, updated_at = ?, last_error = ?
            WHERE id = ?
            """,
            (state, now, error, runner_id),
        )
        if state == "completed":
            connection.execute(
                """
                UPDATE resume_requests
                SET status = 'completed', completed_at = ?
                WHERE runner_id = ? AND status IN ('claimed', 'running')
                """,
                (now, runner_id),
            )

    return {
        "id": runner_id,
        "adapter": row[1],
        "external_id": row[2],
        "state": state,
        "updated_at": now,
        "last_error": error,
    }


def list_runners(limit: int = 20) -> list[dict[str, object]]:
    with database_session() as connection:
        rows = connection.execute(
            """
            SELECT id, adapter, external_id, state, created_at, updated_at, last_error
            FROM agent_runners
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        {
            "id": row[0],
            "adapter": row[1],
            "external_id": row[2],
            "state": row[3],
            "created_at": row[4],
            "updated_at": row[5],
            "last_error": row[6],
        }
        for row in rows
    ]


def list_resume_requests(limit: int = 50) -> list[dict[str, object]]:
    with database_session() as connection:
        rows = connection.execute(
            """
            SELECT
                id, answer_event_id, question_id, runner_id, status, reason,
                created_at, claimed_at, completed_at
            FROM resume_requests
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        {
            "id": row[0],
            "answer_event_id": row[1],
            "question_id": row[2],
            "runner_id": row[3],
            "status": row[4],
            "reason": row[5],
            "created_at": row[6],
            "claimed_at": row[7],
            "completed_at": row[8],
        }
        for row in rows
    ]


def list_resume_attempts(limit: int = 50) -> list[dict[str, object]]:
    with database_session() as connection:
        rows = connection.execute(
            """
            SELECT
                id, request_id, status, pid, started_at, finished_at,
                exit_code, last_error
            FROM resume_attempts
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        {
            "id": row[0],
            "request_id": row[1],
            "status": row[2],
            "pid": row[3],
            "started_at": row[4],
            "finished_at": row[5],
            "exit_code": row[6],
            "last_error": row[7],
        }
        for row in rows
    ]


def runner_snapshot() -> dict[str, object]:
    return {
        "runners": list_runners(),
        "resume_requests": list_resume_requests(),
        "resume_attempts": list_resume_attempts(),
    }


def ask_question(question: str) -> dict[str, object]:
    question = question.strip()
    if not question:
        raise ValueError("Question must not be empty.")

    created_at = utc_now()

    with database_session() as connection:
        runner = _runner_for_current_process(connection)
        runner_id = int(runner[0]) if runner is not None else None
        cursor = connection.execute(
            """
            INSERT INTO questions (question, status, created_at, runner_id)
            VALUES (?, 'open', ?, ?)
            """,
            (question, created_at, runner_id),
        )
        question_id = int(cursor.lastrowid)
        if runner_id is not None:
            connection.execute(
                """
                UPDATE agent_runners
                SET state = 'waiting_for_human', updated_at = ?, last_error = NULL
                WHERE id = ?
                """,
                (created_at, runner_id),
            )
        _enqueue_notification_event(
            connection,
            event_type="question.created",
            entity_type="question",
            entity_id=question_id,
            payload={
                "project": project_info(),
                "question_id": question_id,
                "question": question,
                "created_at": created_at,
            },
            created_at=created_at,
        )

    return {
        "id": question_id,
        "question": question,
        "created_at": created_at,
        "question_ja": None,
    }


def list_open_questions(limit: int | None = 50) -> list[dict[str, object]]:
    query = """
        SELECT question.id, question.question, question.created_at, translation.ja_text
        FROM questions AS question
        LEFT JOIN display_translations AS translation
          ON translation.entity_type = 'question'
         AND translation.entity_id = question.id
         AND translation.source_text = question.question
        WHERE question.status = 'open'
        ORDER BY question.id DESC
    """
    parameters: tuple[object, ...] = ()
    if limit is not None:
        query += " LIMIT ?"
        parameters = (limit,)

    with database_session() as connection:
        rows = connection.execute(query, parameters).fetchall()

    return [
        {"id": row[0], "question": row[1], "created_at": row[2], "question_ja": row[3]}
        for row in rows
    ]


def answer_question(question_id: int, answer: str) -> dict[str, object]:
    answer = answer.strip()
    if not answer:
        raise ValueError("Answer must not be empty.")

    answered_at = utc_now()

    with database_session() as connection:
        row = connection.execute(
            "SELECT question, status, runner_id FROM questions WHERE id = ?",
            (question_id,),
        ).fetchone()

        if row is None:
            raise QuestionNotFoundError(f"Question {question_id} does not exist.")
        if row[1] != "open":
            raise QuestionClosedError(f"Question {question_id} is already answered.")

        connection.execute(
            """
            UPDATE questions
            SET status = 'answered', answer = ?, answered_at = ?
            WHERE id = ?
            """,
            (answer, answered_at, question_id),
        )
        answer_cursor = connection.execute(
            """
            INSERT INTO answer_events (question_id, answer, answered_at)
            VALUES (?, ?, ?)
            """,
            (question_id, answer, answered_at),
        )
        answer_event_id = int(answer_cursor.lastrowid)

        runner_id = int(row[2]) if row[2] is not None else None
        if runner_id is not None:
            runner = connection.execute(
                "SELECT state FROM agent_runners WHERE id = ?",
                (runner_id,),
            ).fetchone()
            active_task = connection.execute(
                "SELECT 1 FROM current_task WHERE id = 1"
            ).fetchone()

            request_status = "pending"
            request_reason = None
            if active_task is None:
                request_status = "cancelled"
                request_reason = "no_active_task"
            elif runner is None:
                request_status = "cancelled"
                request_reason = "runner_missing"
            elif runner[0] != "waiting_for_human":
                request_status = "cancelled"
                request_reason = f"runner_state_{runner[0]}"

            connection.execute(
                """
                INSERT INTO resume_requests (
                    answer_event_id,
                    question_id,
                    runner_id,
                    status,
                    reason,
                    created_at,
                    completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    answer_event_id,
                    question_id,
                    runner_id,
                    request_status,
                    request_reason,
                    answered_at,
                    answered_at if request_status == "cancelled" else None,
                ),
            )
        notification = connection.execute(
            """
            SELECT id
            FROM notification_outbox
            WHERE
                event_type = 'question.created'
                AND entity_type = 'question'
                AND entity_id = ?
            """,
            (question_id,),
        ).fetchone()
        if notification is not None:
            notification_id = int(notification[0])
            connection.execute(
                """
                UPDATE notification_deliveries
                SET status = 'cancelled', cancelled_at = ?
                WHERE notification_id = ? AND status = 'pending'
                """,
                (answered_at, notification_id),
            )
            _refresh_notification_status(connection, notification_id)

    return {
        "id": question_id,
        "question": row[0],
        "answer": answer,
        "answered_at": answered_at,
    }


def list_answered_questions(limit: int | None = 50) -> list[dict[str, object]]:
    query = """
        SELECT question.id, question.question, question.answer,
               question.created_at, question.answered_at, translation.ja_text
        FROM questions AS question
        LEFT JOIN display_translations AS translation
          ON translation.entity_type = 'question'
         AND translation.entity_id = question.id
         AND translation.source_text = question.question
        WHERE question.status = 'answered'
        ORDER BY question.answered_at DESC, question.id DESC
    """
    parameters: tuple[object, ...] = ()
    if limit is not None:
        query += " LIMIT ?"
        parameters = (limit,)
    with database_session() as connection:
        rows = connection.execute(query, parameters).fetchall()

    return [
        {
            "id": row[0],
            "question": row[1],
            "answer": row[2],
            "created_at": row[3],
            "answered_at": row[4],
            "question_ja": row[5],
        }
        for row in rows
    ]


def artifact_dir() -> Path:
    return DATA_DIR / "artifacts"


def _git_commit_for_path(path: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(path.parent), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None

    commit = result.stdout.strip()
    return commit or None


def _public_artifact(row: tuple[object, ...]) -> dict[str, object]:
    return {
        "id": row[0],
        "display_name": row[1],
        "display_name_ja": row[9] if len(row) > 9 else None,
        "original_name": row[2],
        "size_bytes": row[4],
        "sha256": row[5],
        "mime_type": row[6],
        "git_commit": row[7],
        "created_at": row[8],
        "url": f"/artifacts/{row[0]}",
    }


def register_artifact(
    path: str | Path,
    display_name: str | None = None,
    *,
    allowed_root: Path | None = None,
) -> dict[str, object]:
    source = Path(path).expanduser().resolve(strict=True)
    if not source.is_file():
        raise ValueError("Artifact path must point to a regular file.")

    root = (allowed_root or Path.cwd()).resolve()
    try:
        source.relative_to(root)
    except ValueError as exc:
        raise ValueError("Artifact must be inside the current working directory.") from exc

    name = (display_name or source.name).strip()
    if not name:
        raise ValueError("Artifact display name must not be empty.")

    destination_dir = artifact_dir()
    destination_dir.mkdir(parents=True, exist_ok=True)
    storage_name = f"{uuid.uuid4().hex}{source.suffix.lower()}"
    destination = destination_dir / storage_name

    digest = hashlib.sha256()
    size_bytes = 0
    try:
        with source.open("rb") as src, destination.open("xb") as dst:
            while chunk := src.read(1024 * 1024):
                dst.write(chunk)
                digest.update(chunk)
                size_bytes += len(chunk)

        created_at = utc_now()
        mime_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        git_commit = _git_commit_for_path(source)

        with database_session() as connection:
            cursor = connection.execute(
                """
                INSERT INTO artifacts (
                    display_name,
                    original_name,
                    storage_name,
                    size_bytes,
                    sha256,
                    mime_type,
                    git_commit,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    source.name,
                    storage_name,
                    size_bytes,
                    digest.hexdigest(),
                    mime_type,
                    git_commit,
                    created_at,
                ),
            )
            artifact_id = cursor.lastrowid
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    record = (
        artifact_id,
        name,
        source.name,
        storage_name,
        size_bytes,
        digest.hexdigest(),
        mime_type,
        git_commit,
        created_at,
    )
    return _public_artifact(record)


def get_latest_artifact() -> dict[str, object] | None:
    with database_session() as connection:
        row = connection.execute(
            """
            SELECT
                artifact.id,
                artifact.display_name,
                artifact.original_name,
                artifact.storage_name,
                artifact.size_bytes,
                artifact.sha256,
                artifact.mime_type,
                artifact.git_commit,
                artifact.created_at,
                translation.ja_text
            FROM artifacts AS artifact
            LEFT JOIN display_translations AS translation
              ON translation.entity_type = 'artifact'
             AND translation.entity_id = artifact.id
             AND translation.source_text = artifact.display_name
            ORDER BY artifact.id DESC
            LIMIT 1
            """
        ).fetchone()

    if row is None:
        return None
    return _public_artifact(row)


def get_artifact_record(artifact_id: int) -> dict[str, object] | None:
    with database_session() as connection:
        row = connection.execute(
            """
            SELECT
                artifact.id,
                artifact.display_name,
                artifact.original_name,
                artifact.storage_name,
                artifact.size_bytes,
                artifact.sha256,
                artifact.mime_type,
                artifact.git_commit,
                artifact.created_at,
                translation.ja_text
            FROM artifacts AS artifact
            LEFT JOIN display_translations AS translation
              ON translation.entity_type = 'artifact'
             AND translation.entity_id = artifact.id
             AND translation.source_text = artifact.display_name
            WHERE artifact.id = ?
            """,
            (artifact_id,),
        ).fetchone()

    if row is None:
        return None

    public = _public_artifact(row)
    public["storage_name"] = row[3]
    return public


METRIC_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _normalize_metric_key(key: str) -> str:
    key = key.strip()
    if not METRIC_KEY_PATTERN.fullmatch(key):
        raise ValueError(
            "Metric key must be 1-128 characters using letters, numbers, '.', '_' or '-'."
        )
    return key


def set_metric(
    key: str,
    value: str,
    *,
    label: str | None = None,
    unit: str | None = None,
) -> dict[str, str | None]:
    key = _normalize_metric_key(key)
    value = value.strip()
    if not value:
        raise ValueError("Metric value must not be empty.")

    with database_session() as connection:
        existing = connection.execute(
            "SELECT label, unit FROM metrics WHERE key = ?",
            (key,),
        ).fetchone()

        if label is None:
            resolved_label = existing[0] if existing is not None else key
        else:
            resolved_label = label.strip()
            if not resolved_label:
                raise ValueError("Metric label must not be empty.")

        if unit is None:
            resolved_unit = existing[1] if existing is not None else None
        else:
            resolved_unit = unit.strip() or None

        updated_at = utc_now()
        connection.execute(
            """
            INSERT INTO metrics (key, label, value, unit, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                label = excluded.label,
                value = excluded.value,
                unit = excluded.unit,
                updated_at = excluded.updated_at
            """,
            (key, resolved_label, value, resolved_unit, updated_at),
        )

    return {
        "key": key,
        "label": resolved_label,
        "value": value,
        "unit": resolved_unit,
        "updated_at": updated_at,
    }


def list_metrics() -> list[dict[str, str | None]]:
    with database_session() as connection:
        rows = connection.execute(
            """
            SELECT key, label, value, unit, updated_at
            FROM metrics
            ORDER BY key COLLATE NOCASE ASC
            """
        ).fetchall()

    return [
        {
            "key": row[0],
            "label": row[1],
            "value": row[2],
            "unit": row[3],
            "updated_at": row[4],
        }
        for row in rows
    ]


def delete_metric(key: str) -> bool:
    key = _normalize_metric_key(key)
    with database_session() as connection:
        cursor = connection.execute("DELETE FROM metrics WHERE key = ?", (key,))
        return cursor.rowcount > 0


def project_id() -> str:
    return hashlib.sha256(str(PROJECT_ROOT).encode("utf-8")).hexdigest()[:16]


def project_info() -> dict[str, str]:
    return {
        "project_id": project_id(),
        "name": PROJECT_ROOT.name,
    }


def status_snapshot() -> dict[str, object]:
    empty = {
        "schema_version": STATUS_SCHEMA_VERSION,
        "project": project_info(),
        "current_task": None,
        "recent_progress": [],
        "open_questions": [],
        "recent_answers": [],
        "latest_artifact": None,
        "metrics": [],
    }

    if not DB_PATH.is_file():
        return empty

    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "project": project_info(),
        "current_task": get_current_task(),
        "recent_progress": list_progress(STATUS_PROGRESS_LIMIT),
        "open_questions": list_open_questions(None),
        "recent_answers": list_answered_questions(STATUS_ANSWER_LIMIT),
        "latest_artifact": get_latest_artifact(),
        "metrics": list_metrics(),
    }


def telemetry_snapshot() -> dict[str, object]:
    return project_telemetry.snapshot(DB_PATH, project_info())


def remote_runtime_dir() -> Path:
    return DATA_DIR / "runtime"


def remote_state_path() -> Path:
    return remote_runtime_dir() / "remote.json"


def _port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _tailscale_command(*args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["tailscale", *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Tailscale CLI was not found.") from exc
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(f"Tailscale command failed: {message}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Tailscale command timed out.") from exc


def _tailscale_status_json() -> dict[str, object]:
    result = _tailscale_command("serve", "status", "--json")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("Tailscale Serve returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Tailscale Serve returned unexpected JSON.")
    return payload


def _tailscale_used_ports(status: dict[str, object]) -> set[int]:
    ports: set[int] = set()

    tcp = status.get("TCP")
    if isinstance(tcp, dict):
        for key in tcp:
            try:
                ports.add(int(str(key)))
            except ValueError:
                continue

    web = status.get("Web")
    if isinstance(web, dict):
        for key in web:
            match = re.search(r":(\d+)$", str(key))
            if match:
                ports.add(int(match.group(1)))

    return ports


def _tailscale_proxy_ports(status: dict[str, object]) -> set[int]:
    ports: set[int] = set()
    web = status.get("Web")
    if not isinstance(web, dict):
        return ports

    for entry in web.values():
        if not isinstance(entry, dict):
            continue
        handlers = entry.get("Handlers")
        if not isinstance(handlers, dict):
            continue
        for handler in handlers.values():
            if not isinstance(handler, dict):
                continue
            proxy = handler.get("Proxy")
            if not isinstance(proxy, str):
                continue
            try:
                port = urllib.parse.urlsplit(proxy).port
            except ValueError:
                continue
            if port is not None:
                ports.add(port)
    return ports


def _find_free_backend_port(status: dict[str, object] | None = None) -> int:
    reserved = _tailscale_proxy_ports(status or {})
    for port in range(8765, 8800):
        if port not in reserved and _port_is_free(HOST, port):
            return port
    raise RuntimeError("No free backend port found in 8765-8799.")


def _find_free_tailscale_port(status: dict[str, object]) -> int:
    used = _tailscale_used_ports(status)
    candidates = [*range(9443, 9500), *range(10443, 10500)]
    for port in candidates:
        if port in used:
            continue
        if _port_is_free("0.0.0.0", port):
            return port
    raise RuntimeError("No free Tailscale HTTPS port found in the configured ranges.")


def _project_server_matches(url: str, expected_project_id: str) -> bool:
    endpoint = f"{url}/api/project"
    try:
        with urllib.request.urlopen(endpoint, timeout=1) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (
        OSError,
        urllib.error.URLError,
        json.JSONDecodeError,
    ):
        return False

    return (
        response.status == 200
        and isinstance(payload, dict)
        and payload.get("project_id") == expected_project_id
    )


def _wait_for_project_server(url: str, expected_project_id: str) -> None:
    for _ in range(30):
        if _project_server_matches(url, expected_project_id):
            return
        time.sleep(0.2)

    raise RuntimeError("Monitor backend did not become ready.")


def _tailscale_dns_name() -> str:
    result = _tailscale_command("status", "--json")
    try:
        payload = json.loads(result.stdout)
        self_info = payload.get("Self", {})
        dns_name = self_info.get("DNSName") if isinstance(self_info, dict) else None
    except json.JSONDecodeError as exc:
        raise RuntimeError("Tailscale status returned invalid JSON.") from exc

    if not isinstance(dns_name, str) or not dns_name.strip():
        raise RuntimeError("Could not determine this machine's Tailscale DNS name.")
    return dns_name.rstrip(".")


def _serve_handler_proxy(status: dict[str, object], host_name: str, port: int) -> str | None:
    web = status.get("Web")
    if not isinstance(web, dict):
        return None

    entry = web.get(f"{host_name}:{port}")
    if not isinstance(entry, dict):
        return None

    handlers = entry.get("Handlers")
    if not isinstance(handlers, dict):
        return None

    root_handler = handlers.get("/")
    if not isinstance(root_handler, dict):
        return None

    proxy = root_handler.get("Proxy")
    return proxy if isinstance(proxy, str) else None


def _process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        return str(pid) in result.stdout

    try:
        import os

        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _stop_process(pid: int) -> None:
    if pid <= 0:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
        return

    import os
    import signal

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return


def read_remote_state() -> dict[str, object] | None:
    path = remote_state_path()
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Remote state is invalid: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Remote state is invalid: {path}")
    return payload


def _write_remote_state(state: dict[str, object]) -> None:
    runtime = remote_runtime_dir()
    runtime.mkdir(parents=True, exist_ok=True)
    temporary = runtime / "remote.json.tmp"
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(remote_state_path())


def remote_start() -> dict[str, object]:
    init_project(False)

    existing = read_remote_state()
    if existing is not None:
        pid = existing.get("pid")
        backend_url = existing.get("backend_url")
        if isinstance(pid, int) and _process_is_alive(pid):
            if isinstance(backend_url, str) and _project_server_matches(
                backend_url,
                project_id(),
            ):
                raise RuntimeError(
                    f"Remote monitor is already running: {existing.get('tailnet_url', backend_url)}"
                )
            raise RuntimeError(
                "Remote state points to a live process that cannot be verified; refusing to overwrite it."
            )
        remote_state_path().unlink(missing_ok=True)

    status = _tailscale_status_json()
    backend_port = _find_free_backend_port(status)
    https_port = _find_free_tailscale_port(status)
    backend_url = f"http://{HOST}:{backend_port}"

    runtime = remote_runtime_dir()
    runtime.mkdir(parents=True, exist_ok=True)
    stdout_path = runtime / "remote.stdout.log"
    stderr_path = runtime / "remote.stderr.log"

    stdout_file = stdout_path.open("ab")
    stderr_file = stderr_path.open("ab")
    process: subprocess.Popen[bytes] | None = None
    serve_configured = False
    try:
        kwargs: dict[str, object] = {
            "cwd": str(PROJECT_ROOT),
            "stdin": subprocess.DEVNULL,
            "stdout": stdout_file,
            "stderr": stderr_file,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            )
        else:
            kwargs["start_new_session"] = True

        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "ai_agent_monitor",
                "serve",
                "--port",
                str(backend_port),
            ],
            **kwargs,
        )

        _wait_for_project_server(backend_url, project_id())
        _tailscale_command(
            "serve",
            "--bg",
            "--yes",
            f"--https={https_port}",
            backend_url,
        )
        serve_configured = True
        dns_name = _tailscale_dns_name()
        tailnet_url = f"https://{dns_name}:{https_port}/"

        state = {
            "project_root": str(PROJECT_ROOT),
            "backend_port": backend_port,
            "https_port": https_port,
            "pid": process.pid,
            "backend_url": backend_url,
            "tailnet_url": tailnet_url,
            "started_at": utc_now(),
        }
        _write_remote_state(state)
        return state
    except Exception:
        if serve_configured:
            try:
                _tailscale_command("serve", f"--https={https_port}", "off")
            except RuntimeError:
                pass
        if process is not None and process.poll() is None:
            _stop_process(process.pid)
        raise
    finally:
        stdout_file.close()
        stderr_file.close()


def remote_status() -> dict[str, object]:
    state = read_remote_state()
    if state is None:
        return {
            "configured": False,
            "backend_alive": False,
            "tailscale_active": False,
        }

    backend_alive = False
    backend_url = state.get("backend_url")
    if isinstance(backend_url, str):
        backend_alive = _project_server_matches(backend_url, project_id())

    tailscale_active = False
    https_port = state.get("https_port")
    backend_url = state.get("backend_url")
    if isinstance(https_port, int) and isinstance(backend_url, str):
        try:
            status = _tailscale_status_json()
            host_name = _tailscale_dns_name()
            tailscale_active = (
                _serve_handler_proxy(status, host_name, https_port) == backend_url
            )
        except RuntimeError:
            tailscale_active = False

    return {
        **state,
        "configured": True,
        "backend_alive": backend_alive,
        "tailscale_active": tailscale_active,
    }


def remote_stop() -> dict[str, object]:
    state = read_remote_state()
    if state is None:
        return {"stopped": False, "reason": "not configured"}

    expected_root = state.get("project_root")
    if expected_root != str(PROJECT_ROOT):
        raise RuntimeError(
            "Remote state belongs to a different project; refusing to stop it."
        )

    backend_url = state.get("backend_url")
    https_port = state.get("https_port")
    if not isinstance(backend_url, str) or not isinstance(https_port, int):
        raise RuntimeError("Remote state is missing backend or HTTPS port information.")

    pid = state.get("pid")
    if (
        isinstance(pid, int)
        and _process_is_alive(pid)
        and not _project_server_matches(backend_url, project_id())
    ):
        raise RuntimeError(
            "Saved backend PID is live but cannot be verified as this project; refusing to stop it."
        )

    status = _tailscale_status_json()
    host_name = _tailscale_dns_name()
    proxy = _serve_handler_proxy(status, host_name, https_port)
    if proxy is not None and proxy != backend_url:
        raise RuntimeError(
            f"Tailscale Serve port {https_port} no longer points to this project; refusing to change it."
        )

    if proxy == backend_url:
        _tailscale_command("serve", f"--https={https_port}", "off")

    if isinstance(pid, int) and _process_is_alive(pid):
        _stop_process(pid)

    remote_state_path().unlink(missing_ok=True)
    return {
        "stopped": True,
        "backend_url": backend_url,
        "https_port": https_port,
    }


def startup_state_path() -> Path:
    return remote_runtime_dir() / "startup.json"


def startup_script_path() -> Path:
    return remote_runtime_dir() / "startup.ps1"


def startup_launcher_name() -> str:
    return f"AI Agent Monitor {project_id()}.cmd"


def _powershell_single_quote(value: str) -> str:
    return value.replace("'", "''")


def _cmd_quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _require_windows_startup() -> None:
    if sys.platform != "win32":
        raise RuntimeError("Startup integration is currently supported only on Windows.")


def windows_startup_dir() -> Path:
    _require_windows_startup()
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA is not set; the Windows Startup folder cannot be located.")
    return (
        Path(appdata)
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
    )


def startup_launcher_path() -> Path:
    return windows_startup_dir() / startup_launcher_name()


def read_startup_state() -> dict[str, object] | None:
    path = startup_state_path()
    if not path.is_file():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Startup state is invalid: {path}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError(f"Startup state is invalid: {path}")
    return payload


def _write_startup_state(state: dict[str, object]) -> None:
    runtime = remote_runtime_dir()
    runtime.mkdir(parents=True, exist_ok=True)
    temporary = runtime / "startup.json.tmp"
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(startup_state_path())


def _startup_powershell_script(python_executable: str, project_root: str) -> str:
    project_path = _powershell_single_quote(project_root)
    python_path = _powershell_single_quote(python_executable)
    return (
        "$ErrorActionPreference = 'Continue'\n"
        f"Set-Location -LiteralPath '{project_path}'\n"
        f"$python = '{python_path}'\n"
        "$statusJson = & $python -m ai_agent_monitor remote status 2>$null\n"
        "if ($LASTEXITCODE -eq 0) {\n"
        "    try {\n"
        "        $status = $statusJson | ConvertFrom-Json\n"
        "        if ($status.backend_alive -and $status.tailscale_active) { exit 0 }\n"
        "    } catch {}\n"
        "}\n"
        "for ($attempt = 0; $attempt -lt 12; $attempt++) {\n"
        "    & $python -m ai_agent_monitor remote start\n"
        "    if ($LASTEXITCODE -eq 0) { exit 0 }\n"
        "    Start-Sleep -Seconds 5\n"
        "}\n"
        "exit 1\n"
    )


def _startup_cmd_launcher(script_path: Path) -> str:
    powershell = "powershell.exe"
    return (
        "@echo off\r\n"
        f'start "" /min {powershell} -NoProfile -NonInteractive '
        "-ExecutionPolicy Bypass -WindowStyle Hidden -File "
        f"{_cmd_quote(str(script_path))}\r\n"
    )


def startup_install() -> dict[str, object]:
    _require_windows_startup()
    init_project(False)

    runtime = remote_runtime_dir()
    runtime.mkdir(parents=True, exist_ok=True)

    python_executable = str(Path(sys.executable).resolve())
    script_path = startup_script_path()
    launcher_path = startup_launcher_path()
    launcher_path.parent.mkdir(parents=True, exist_ok=True)

    script_path.write_text(
        _startup_powershell_script(python_executable, str(PROJECT_ROOT)),
        encoding="utf-8",
    )

    launcher_created = False
    try:
        launcher_path.write_text(
            _startup_cmd_launcher(script_path),
            encoding="utf-8",
        )
        launcher_created = True

        state = {
            "project_root": str(PROJECT_ROOT),
            "project_id": project_id(),
            "mode": "startup-folder",
            "launcher_path": str(launcher_path),
            "script_path": str(script_path),
            "python_executable": python_executable,
            "installed_at": utc_now(),
        }
        _write_startup_state(state)
        return state
    except Exception:
        if launcher_created:
            launcher_path.unlink(missing_ok=True)
        script_path.unlink(missing_ok=True)
        raise


def startup_status() -> dict[str, object]:
    _require_windows_startup()
    state = read_startup_state()
    expected_launcher = startup_launcher_path()

    installed = expected_launcher.is_file()
    state_matches = False
    if state is not None:
        state_matches = (
            state.get("project_root") == str(PROJECT_ROOT)
            and state.get("project_id") == project_id()
            and state.get("mode") == "startup-folder"
            and state.get("launcher_path") == str(expected_launcher)
        )

    return {
        "installed": installed and state_matches,
        "launcher_path": str(expected_launcher),
        "state": state,
    }


def startup_remove() -> dict[str, object]:
    _require_windows_startup()
    state = read_startup_state()
    if state is None:
        return {"removed": False, "reason": "not installed"}

    if state.get("project_root") != str(PROJECT_ROOT):
        raise RuntimeError(
            "Startup state belongs to a different project; refusing to remove it."
        )

    if state.get("project_id") != project_id():
        raise RuntimeError(
            "Startup project ID does not match this project; refusing to remove it."
        )

    expected_launcher = startup_launcher_path()
    if (
        state.get("mode") != "startup-folder"
        or state.get("launcher_path") != str(expected_launcher)
    ):
        raise RuntimeError(
            "Startup launcher does not match this project; refusing to remove it."
        )

    expected_launcher.unlink(missing_ok=True)
    startup_state_path().unlink(missing_ok=True)
    startup_script_path().unlink(missing_ok=True)
    return {"removed": True, "launcher_path": str(expected_launcher)}


DOCTOR_SCHEMA_VERSION = 1
DOCTOR_LOG_WARNING_BYTES = 10 * 1024 * 1024
DOCTOR_REQUIRED_SCHEMA = {
    "progress": {"id", "message", "created_at"},
    "current_task": {"id", "title", "started_at"},
    "task_runs": {"id", "title", "started_at", "ended_at", "outcome"},
    "questions": {
        "id", "question", "status", "created_at", "answer", "answered_at", "runner_id"
    },
    "agent_runners": {
        "id", "adapter", "external_id", "state", "created_at", "updated_at", "last_error"
    },
    "answer_events": {"id", "question_id", "answer", "answered_at"},
    "resume_requests": {
        "id", "answer_event_id", "question_id", "runner_id", "status", "reason",
        "created_at", "claimed_at", "completed_at"
    },
    "resume_attempts": {
        "id", "request_id", "status", "pid", "started_at", "finished_at",
        "exit_code", "last_error"
    },
    "artifacts": {
        "id",
        "display_name",
        "original_name",
        "storage_name",
        "size_bytes",
        "sha256",
        "mime_type",
        "git_commit",
        "created_at",
    },
    "metrics": {"key", "label", "value", "unit", "updated_at"},
    "notification_outbox": {
        "id",
        "event_type",
        "entity_type",
        "entity_id",
        "payload_json",
        "status",
        "attempt_count",
        "created_at",
        "last_attempt_at",
        "delivered_at",
        "cancelled_at",
        "last_error",
    },
    "notification_deliveries": {
        "id",
        "notification_id",
        "channel",
        "target",
        "status",
        "attempt_count",
        "created_at",
        "last_attempt_at",
        "delivered_at",
        "cancelled_at",
        "last_error",
    },
}


def _doctor_result(
    name: str,
    status: str,
    message: str,
    *,
    details: dict[str, object] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "name": name,
        "status": status,
        "message": message,
    }
    if details:
        result["details"] = details
    return result


def _read_only_database() -> sqlite3.Connection:
    uri = DB_PATH.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=1)
    connection.execute("PRAGMA query_only=ON")
    return connection


def _doctor_database() -> dict[str, object]:
    if not DB_PATH.is_file():
        return _doctor_result(
            "database",
            "ok",
            "Monitor database is not initialized for this project.",
            details={"initialized": False},
        )

    try:
        with closing(_read_only_database()) as connection:
            quick_check = connection.execute("PRAGMA quick_check").fetchone()
            if quick_check is None or quick_check[0] != "ok":
                return _doctor_result(
                    "database",
                    "error",
                    "SQLite quick_check reported a problem.",
                    details={"quick_check": quick_check[0] if quick_check else None},
                )

            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            missing_tables = sorted(set(DOCTOR_REQUIRED_SCHEMA) - tables)
            missing_columns: dict[str, list[str]] = {}
            for table, required_columns in DOCTOR_REQUIRED_SCHEMA.items():
                if table not in tables:
                    continue
                columns = {
                    row[1]
                    for row in connection.execute(
                        f"PRAGMA table_info({table})"
                    ).fetchall()
                }
                missing = sorted(required_columns - columns)
                if missing:
                    missing_columns[table] = missing

            if missing_tables or missing_columns:
                return _doctor_result(
                    "database",
                    "error",
                    "Monitor database schema is incomplete.",
                    details={
                        "missing_tables": missing_tables,
                        "missing_columns": missing_columns,
                    },
                )

            task_row = connection.execute(
                "SELECT title FROM current_task WHERE id = 1"
            ).fetchone()
            open_questions = connection.execute(
                "SELECT COUNT(*) FROM questions WHERE status = 'open'"
            ).fetchone()[0]

            return _doctor_result(
                "database",
                "ok",
                "SQLite database is readable and the required schema is present.",
                details={
                    "initialized": True,
                    "current_task": task_row[0] if task_row else None,
                    "open_questions": open_questions,
                },
            )
    except (sqlite3.Error, OSError) as exc:
        return _doctor_result(
            "database",
            "error",
            "Monitor database could not be read safely.",
            details={"error": str(exc)},
        )


def _doctor_agent_integration() -> dict[str, object]:
    contract = agent_contract_target()
    skill = codex_skill_target()
    agents = agents_file_path()

    contract_exists = contract.is_file()
    skill_exists = skill.is_file()
    agents_text = agents.read_text(encoding="utf-8") if agents.is_file() else ""
    start_count = agents_text.count(AGENTS_BLOCK_START)
    end_count = agents_text.count(AGENTS_BLOCK_END)

    if start_count != end_count or start_count > 1:
        return _doctor_result(
            "agent_integration",
            "error",
            "AGENTS.md contains invalid AI Agent Monitor managed-block markers.",
            details={"start_markers": start_count, "end_markers": end_count},
        )

    managed_block = start_count == 1
    if not contract_exists and not skill_exists and not managed_block:
        return _doctor_result(
            "agent_integration",
            "ok",
            "Agent onboarding is not installed for this project.",
            details={"installed": False},
        )

    problems: list[str] = []
    warnings: list[str] = []

    if contract_exists:
        try:
            if contract.read_text(encoding="utf-8") != AGENT_CONTRACT_PATH.read_text(
                encoding="utf-8"
            ):
                warnings.append("AI_AGENT_MONITOR.md differs from the packaged contract.")
        except OSError as exc:
            problems.append(f"Could not read AI_AGENT_MONITOR.md: {exc}")
    else:
        problems.append("AI_AGENT_MONITOR.md is missing.")

    if managed_block or skill_exists:
        if not managed_block:
            problems.append("The Codex skill exists but the AGENTS.md managed block is missing.")
        if not skill_exists:
            problems.append("The AGENTS.md managed block exists but the Codex skill is missing.")
        else:
            try:
                if skill.read_text(encoding="utf-8") != CODEX_SKILL_PATH.read_text(
                    encoding="utf-8"
                ):
                    warnings.append("The Codex skill differs from the packaged skill.")
            except OSError as exc:
                problems.append(f"Could not read the Codex skill: {exc}")

    if problems:
        return _doctor_result(
            "agent_integration",
            "error",
            "Agent onboarding files are inconsistent.",
            details={"problems": problems, "warnings": warnings},
        )
    if warnings:
        return _doctor_result(
            "agent_integration",
            "warning",
            "Agent onboarding is installed but generated content was modified.",
            details={"warnings": warnings},
        )

    kind = "codex" if managed_block else "generic"
    return _doctor_result(
        "agent_integration",
        "ok",
        f"{kind.capitalize()} agent onboarding is consistent.",
        details={"installed": True, "kind": kind},
    )


def _serve_proxy_for_port(status: dict[str, object], port: int) -> str | None:
    web = status.get("Web")
    if not isinstance(web, dict):
        return None
    suffix = f":{port}"
    for key, entry in web.items():
        if not str(key).endswith(suffix) or not isinstance(entry, dict):
            continue
        handlers = entry.get("Handlers")
        if not isinstance(handlers, dict):
            continue
        root_handler = handlers.get("/")
        if not isinstance(root_handler, dict):
            continue
        proxy = root_handler.get("Proxy")
        if isinstance(proxy, str):
            return proxy
    return None


def _doctor_remote() -> dict[str, object]:
    if not remote_state_path().is_file():
        return _doctor_result(
            "remote",
            "ok",
            "Project remote access is not configured.",
            details={"configured": False},
        )

    try:
        state = read_remote_state()
    except (RuntimeError, OSError) as exc:
        return _doctor_result(
            "remote",
            "error",
            "Remote runtime state could not be read.",
            details={"error": str(exc)},
        )

    if state is None:
        return _doctor_result(
            "remote",
            "ok",
            "Project remote access is not configured.",
            details={"configured": False},
        )

    problems: list[str] = []
    if state.get("project_root") != str(PROJECT_ROOT):
        problems.append("Remote state belongs to a different project.")

    pid = state.get("pid")
    backend_url = state.get("backend_url")
    https_port = state.get("https_port")

    if not isinstance(pid, int):
        problems.append("Remote state has no valid PID.")
    elif not _process_is_alive(pid):
        problems.append("Saved remote PID is not running.")

    if not isinstance(backend_url, str):
        problems.append("Remote state has no valid backend URL.")
    elif isinstance(pid, int) and _process_is_alive(pid):
        if not _project_server_matches(backend_url, project_id()):
            problems.append("Live backend does not identify as this project.")

    if not isinstance(https_port, int):
        problems.append("Remote state has no valid Tailscale HTTPS port.")
    elif isinstance(backend_url, str):
        try:
            serve_status = _tailscale_status_json()
            proxy = _serve_proxy_for_port(serve_status, https_port)
            if proxy != backend_url:
                problems.append(
                    "Tailscale Serve mapping does not match the saved backend URL."
                )
        except RuntimeError as exc:
            problems.append(f"Tailscale Serve status is unavailable: {exc}")

    if problems:
        return _doctor_result(
            "remote",
            "error",
            "Remote runtime state is inconsistent.",
            details={"configured": True, "problems": problems},
        )

    return _doctor_result(
        "remote",
        "ok",
        "Remote backend and Tailscale Serve mapping are consistent.",
        details={
            "configured": True,
            "backend_url": backend_url,
            "https_port": https_port,
        },
    )


def _doctor_startup() -> dict[str, object]:
    if not startup_state_path().is_file():
        return _doctor_result(
            "startup",
            "ok",
            "Automatic logon startup is not configured for this project.",
            details={"configured": False},
        )

    try:
        state = read_startup_state()
    except (RuntimeError, OSError) as exc:
        return _doctor_result(
            "startup",
            "error",
            "Startup runtime state could not be read.",
            details={"error": str(exc)},
        )

    if state is None:
        return _doctor_result(
            "startup",
            "ok",
            "Automatic logon startup is not configured for this project.",
            details={"configured": False},
        )

    problems: list[str] = []
    if state.get("project_root") != str(PROJECT_ROOT):
        problems.append("Startup state belongs to a different project.")
    if state.get("project_id") != project_id():
        problems.append("Startup state has the wrong project ID.")
    if state.get("mode") != "startup-folder":
        problems.append("Startup state uses an unsupported mode.")

    launcher_value = state.get("launcher_path")
    script_value = state.get("script_path")
    if not isinstance(launcher_value, str) or not Path(launcher_value).is_file():
        problems.append("Startup launcher is missing.")
    if not isinstance(script_value, str) or not Path(script_value).is_file():
        problems.append("Startup PowerShell script is missing.")

    if sys.platform == "win32" and isinstance(launcher_value, str):
        try:
            if Path(launcher_value) != startup_launcher_path():
                problems.append("Startup launcher path does not match this project.")
        except RuntimeError as exc:
            problems.append(f"Windows Startup folder could not be resolved: {exc}")

    if problems:
        return _doctor_result(
            "startup",
            "error",
            "Automatic startup state is inconsistent.",
            details={"configured": True, "problems": problems},
        )

    return _doctor_result(
        "startup",
        "ok",
        "Automatic logon startup files are consistent.",
        details={"configured": True, "launcher_path": launcher_value},
    )


def _doctor_notifications() -> dict[str, object]:
    try:
        config = read_notification_config()
    except RuntimeError as exc:
        return _doctor_result(
            "notifications",
            "error",
            "Notification configuration could not be read.",
            details={"error": str(exc)},
        )

    problems: list[str] = []
    telegram = config["telegram"]
    email = config["email"]

    if telegram.get("enabled") is True:
        if not telegram.get("chat_id"):
            problems.append("Telegram is enabled without a chat ID.")
        if not os.environ.get(TELEGRAM_TOKEN_ENV):
            problems.append(f"Telegram is enabled but {TELEGRAM_TOKEN_ENV} is not set.")

    if email.get("enabled") is True:
        try:
            recipients = email_recipients()
        except ValueError as exc:
            recipients = []
            problems.append(f"Email recipient configuration is invalid: {exc}")
        if not recipients:
            problems.append("Email is enabled but no recipients are configured.")
        for key in ("from_address", "smtp_host", "smtp_port"):
            if not email.get(key):
                problems.append(f"Email is enabled but {key} is not configured.")
        if email.get("username") and not os.environ.get(SMTP_PASSWORD_ENV):
            problems.append(
                f"Authenticated email is enabled but {SMTP_PASSWORD_ENV} is not set."
            )

    if problems:
        return _doctor_result(
            "notifications",
            "error",
            "Notification delivery configuration is incomplete.",
            details={"problems": problems},
        )

    return _doctor_result(
        "notifications",
        "ok",
        "Notification delivery configuration is usable.",
        details={
            "telegram_enabled": telegram.get("enabled") is True,
            "email_enabled": email.get("enabled") is True,
        },
    )


def _doctor_runner() -> dict[str, object]:
    try:
        config = read_runner_config()
    except RuntimeError as exc:
        return _doctor_result(
            "runner",
            "error",
            "Runner configuration could not be read.",
            details={"error": str(exc)},
        )

    auto_resume = config.get("auto_resume") is True
    command = str(config.get("codex_command") or DEFAULT_CODEX_COMMAND)
    if Path(command).is_absolute():
        available = Path(command).is_file()
    else:
        available = shutil.which(command) is not None

    if auto_resume and not available:
        return _doctor_result(
            "runner",
            "error",
            "Automatic resume is enabled but the Codex executable is unavailable.",
            details={"auto_resume": True, "codex_command": command},
        )

    uncertain = 0
    if DB_PATH.is_file():
        try:
            with closing(_read_only_database()) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
                if "resume_requests" in tables:
                    uncertain = connection.execute(
                        "SELECT COUNT(*) FROM resume_requests WHERE status = 'uncertain'"
                    ).fetchone()[0]
        except (sqlite3.Error, OSError):
            pass

    if uncertain:
        return _doctor_result(
            "runner",
            "warning",
            "One or more resume requests require manual review.",
            details={"auto_resume": auto_resume, "uncertain_requests": uncertain},
        )

    return _doctor_result(
        "runner",
        "ok",
        "Runner configuration is usable.",
        details={"auto_resume": auto_resume, "codex_available": available},
    )


def _doctor_runtime() -> dict[str, object]:
    runtime = remote_runtime_dir()
    if not runtime.is_dir():
        return _doctor_result(
            "runtime",
            "ok",
            "No runtime directory is present.",
        )

    warnings: list[str] = []
    large_logs: dict[str, int] = {}
    for name in ("remote.stdout.log", "remote.stderr.log"):
        path = runtime / name
        if path.is_file():
            size = path.stat().st_size
            if size > DOCTOR_LOG_WARNING_BYTES:
                large_logs[name] = size

    if large_logs:
        warnings.append("Remote log files exceed the 10 MiB warning threshold.")

    temporary_files = sorted(path.name for path in runtime.glob("*.tmp") if path.is_file())
    if temporary_files:
        warnings.append("Temporary runtime files remain from an interrupted write.")

    if warnings:
        return _doctor_result(
            "runtime",
            "warning",
            "Runtime files need attention.",
            details={
                "warnings": warnings,
                "large_logs": large_logs,
                "temporary_files": temporary_files,
            },
        )

    return _doctor_result(
        "runtime",
        "ok",
        "Runtime files are within the current diagnostic thresholds.",
    )


def doctor_snapshot() -> dict[str, object]:
    checks = [
        _doctor_result(
            "package",
            "ok",
            f"AI Agent Monitor {__version__}",
            details={"version": __version__},
        ),
        _doctor_result(
            "project",
            "ok",
            f"Project: {PROJECT_ROOT.name}",
            details={
                "project_id": project_id(),
                "project_root": str(PROJECT_ROOT),
                "data_dir": str(DATA_DIR),
            },
        ),
        _doctor_database(),
        _doctor_agent_integration(),
        _doctor_remote(),
        _doctor_startup(),
        _doctor_notifications(),
        _doctor_runner(),
        _doctor_runtime(),
    ]

    statuses = {str(check["status"]) for check in checks}
    if "error" in statuses:
        overall = "error"
    elif "warning" in statuses:
        overall = "warning"
    else:
        overall = "ok"

    return {
        "schema_version": DOCTOR_SCHEMA_VERSION,
        "overall": overall,
        "checks": checks,
    }


def format_doctor_report(report: dict[str, object]) -> str:
    lines = [
        "AI Agent Monitor Doctor",
        f"Overall: {str(report['overall']).upper()}",
    ]
    for check in report["checks"]:
        status = str(check["status"]).upper()
        lines.append(f"[{status}] {check['name']}: {check['message']}")
    return "\n".join(lines) + "\n"


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]

        if path in ("/", "/dashboard.html"):
            self._serve_dashboard()
            return

        if path in ("/registry", "/registry.html"):
            self._serve_registry_page()
            return

        if path == "/api/registry":
            try:
                self._serve_json(project_registry.snapshot(registry_path()))
            except (ValueError, json.JSONDecodeError):
                self._serve_json({"error": "Registry configuration is invalid."}, status=500)
            return

        if path == "/api/status":
            self._serve_json(status_snapshot())
            return

        if path == "/api/telemetry":
            self._serve_json(telemetry_snapshot())
            return

        if path == "/api/project":
            self._serve_json(project_info())
            return

        if path == "/api/progress":
            self._serve_json({"progress": list_progress()})
            return

        if path == "/api/task":
            self._serve_json({"task": get_current_task()})
            return

        if path == "/api/questions":
            questions = list_open_questions()
            self._serve_json(
                {
                    "count": len(questions),
                    "questions": questions,
                }
            )
            return

        if path == "/api/answers":
            self._serve_json({"answers": list_answered_questions(limit=None)})
            return

        if path == "/api/artifacts/latest":
            self._serve_json({"artifact": get_latest_artifact()})
            return

        if path == "/api/metrics":
            self._serve_json({"metrics": list_metrics()})
            return

        if path == "/api/codex-cost":
            codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
            self._serve_json(CODEX_COST_MANAGER.snapshot(
                CODEX_COST_CONFIG_PATH,
                PROJECT_ROOT,
                codex_home / "sessions",
                CODEX_COST_CACHE_PATH,
            ))
            return

        if path == "/api/codex-usage":
            self._serve_json(CODEX_USAGE_MANAGER.snapshot(CODEX_USAGE_CONFIG_PATH))
            return

        artifact_match = re.fullmatch(r"/artifacts/(\d+)", path)
        if artifact_match is not None:
            self._serve_artifact(int(artifact_match.group(1)))
            return

        self.send_error(404, "Not Found")

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        match = re.fullmatch(r"/api/questions/(\d+)/answer", path)
        if match is None:
            self.send_error(404, "Not Found")
            return

        try:
            body = self._read_json_body()
            answer = body.get("answer")
            if not isinstance(answer, str):
                raise ValueError("JSON field 'answer' must be a string.")
            answered = answer_question(int(match.group(1)), answer)
        except QuestionNotFoundError as exc:
            self._serve_json({"error": str(exc)}, status=404)
            return
        except QuestionClosedError as exc:
            self._serve_json({"error": str(exc)}, status=409)
            return
        except (ValueError, json.JSONDecodeError) as exc:
            self._serve_json({"error": str(exc)}, status=400)
            return

        self._serve_json({"answered": answered})

    def _read_json_body(self) -> dict[str, object]:
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            raise ValueError("Content-Length is required.")

        try:
            length = int(length_header)
        except ValueError as exc:
            raise ValueError("Invalid Content-Length.") from exc

        if length < 0 or length > MAX_JSON_BODY:
            raise ValueError("Request body is too large.")

        raw = self.rfile.read(length)
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object.")
        return payload

    def _serve_artifact(self, artifact_id: int) -> None:
        artifact = get_artifact_record(artifact_id)
        if artifact is None:
            self.send_error(404, "Artifact not found")
            return

        file_path = artifact_dir() / str(artifact["storage_name"])
        if not file_path.is_file():
            self.send_error(410, "Artifact snapshot is missing")
            return

        self.send_response(200)
        self.send_header("Content-Type", str(artifact["mime_type"]))
        self.send_header("Content-Length", str(file_path.stat().st_size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if artifact["mime_type"] == "text/html":
            self.send_header("Content-Security-Policy", "sandbox allow-scripts")
        self.end_headers()

        with file_path.open("rb") as artifact_file:
            while chunk := artifact_file.read(1024 * 1024):
                self.wfile.write(chunk)

    def _serve_dashboard(self) -> None:
        try:
            content = rendered_dashboard_html()
        except FileNotFoundError:
            self.send_error(500, "dashboard.html is missing")
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def _serve_registry_page(self) -> None:
        try:
            content = REGISTRY_PAGE_PATH.read_bytes()
        except FileNotFoundError:
            self.send_error(500, "registry.html is missing")
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def _serve_json(self, data: object, status: int = 200) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


def parse_doctor_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="monitor doctor",
        description="Diagnose the current project without changing monitor state.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the diagnostic report as JSON.",
    )
    return parser.parse_args(argv)


def parse_agent_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="monitor agent",
        description="Install, remove, or emit agent integration instructions.",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    for action in ("install", "remove", "emit"):
        action_parser = subparsers.add_parser(action)
        action_parser.add_argument("kind", choices=("codex", "generic"))

    return parser.parse_args(argv)


def parse_startup_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="monitor startup",
        description="Manage Windows logon startup for this project's remote monitor.",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("install", help="Start this project's remote monitor at Windows logon")
    subparsers.add_parser("status", help="Show this project's startup task state")
    subparsers.add_parser("remove", help="Remove this project's startup task")
    return parser.parse_args(argv)


def parse_remote_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="monitor remote",
        description="Expose the current project to the Tailscale tailnet.",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("start", help="Start a tailnet-only HTTPS endpoint")
    subparsers.add_parser("status", help="Show the current remote endpoint state")
    subparsers.add_parser("stop", help="Stop this project's remote endpoint")
    return parser.parse_args(argv)


def parse_init_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="monitor init",
        description="Initialize monitor files for the current project.",
    )
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Copy the bundled dashboard into .agent-monitor/dashboard.html for project-specific editing.",
    )
    return parser.parse_args(argv)


def parse_server_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve the AI Agent Monitor dashboard on this computer."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Local port to use (default: {DEFAULT_PORT})",
    )
    return parser.parse_args(argv)


def parse_progress_args(argv: list[str]) -> argparse.Namespace:
    if argv and argv[0] == "translate":
        parser = argparse.ArgumentParser(
            prog="monitor progress translate",
            description="Add or update the Japanese display text of an existing progress record.",
        )
        parser.add_argument("id", type=int, help="Progress record ID")
        parser.add_argument("translation", nargs="+", help="Japanese display text")
        args = parser.parse_args(argv[1:])
        args.action = "translate"
        return args
    parser = argparse.ArgumentParser(
        prog="monitor progress",
        description="Record a progress message for the dashboard.",
    )
    parser.add_argument("--ja", help="Japanese display text; keeps the original message")
    parser.add_argument("message", nargs="+", help="Progress message to record")
    args = parser.parse_args(argv)
    args.action = "record"
    return args


def parse_translate_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="monitor translate",
        description="Add Japanese display text without changing the original record.",
    )
    actions = parser.add_subparsers(dest="entity_type", required=True)
    task = actions.add_parser("task", help="Translate the current task title")
    task.add_argument("translation", nargs="+")
    for entity_type in ("question", "artifact", "progress"):
        action = actions.add_parser(entity_type, help=f"Translate a {entity_type} record")
        action.add_argument("id", type=int)
        action.add_argument("translation", nargs="+")
    return parser.parse_args(argv)


def parse_task_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="monitor task",
        description="Start or complete the current task.",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    start_parser = subparsers.add_parser("start", help="Set the current task")
    start_parser.add_argument("title", nargs="+", help="Task title")

    subparsers.add_parser("done", help="Complete the current task")
    return parser.parse_args(argv)


def parse_ask_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="monitor ask",
        description="Record a question for the human.",
    )
    parser.add_argument("question", nargs="+", help="Question to record")
    return parser.parse_args(argv)


def parse_runner_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="monitor runner",
        description="Manage agent runner identity and automatic resume.",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    register = subparsers.add_parser(
        "register",
        help="Register the current agent session",
    )
    register.add_argument("adapter", choices=("codex",))
    register.add_argument(
        "--external-id",
        help="Explicit external runner ID; Codex normally uses CODEX_THREAD_ID.",
    )

    subparsers.add_parser("status", help="Show runner and resume state")
    subparsers.add_parser("complete", help="Mark the current registered runner completed")
    subparsers.add_parser("stop", help="Mark the current registered runner stopped")

    auto_resume = subparsers.add_parser(
        "auto-resume",
        help="Enable or disable automatic resume",
    )
    auto_resume.add_argument("enabled", choices=("on", "off"))

    codex = subparsers.add_parser("codex", help="Configure the Codex executable")
    codex_actions = codex.add_subparsers(dest="codex_action", required=True)
    codex_set = codex_actions.add_parser("set", help="Set Codex executable path or command")
    codex_set.add_argument("--command", required=True)

    subparsers.add_parser(
        "preflight",
        help="Run a harmless Codex command using the exact automatic-resume policy",
    )

    subparsers.add_parser("dispatch", help="Force one pending resume dispatch")

    retry = subparsers.add_parser(
        "retry",
        help="Manually retry an uncertain resume request",
    )
    retry.add_argument("request_id", type=int)

    worker = subparsers.add_parser("worker", help=argparse.SUPPRESS)
    worker.add_argument("attempt_id", type=int)

    return parser.parse_args(argv)


def parse_notify_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="monitor notify",
        description="Configure and deliver human notifications.",
    )
    subparsers = parser.add_subparsers(dest="section", required=True)
    subparsers.add_parser("status", help="Show notification configuration")
    subparsers.add_parser("send", help="Attempt pending notification deliveries now")

    telegram = subparsers.add_parser("telegram", help="Configure Telegram notifications")
    telegram_actions = telegram.add_subparsers(dest="action", required=True)
    telegram_set = telegram_actions.add_parser("set", help="Set Telegram chat ID")
    telegram_set.add_argument("--chat-id", required=True)
    telegram_actions.add_parser("discover", help="Discover the latest chat that messaged the bot")
    telegram_actions.add_parser("on", help="Enable Telegram for future notifications")
    telegram_actions.add_parser("off", help="Disable Telegram and cancel pending Telegram deliveries")
    replies = telegram_actions.add_parser("replies", help="Receive answers sent as Telegram replies")
    reply_actions = replies.add_subparsers(dest="replies_action", required=True)
    reply_actions.add_parser("on", help="Accept replies to this project's notifications")
    reply_actions.add_parser("off", help="Stop accepting Telegram replies")
    reply_actions.add_parser("poll", help="Check for Telegram replies once")

    email = subparsers.add_parser("email", help="Configure optional email notifications")
    email_actions = email.add_subparsers(dest="action", required=True)
    email_set = email_actions.add_parser("set", help="Set SMTP email settings")
    email_set.add_argument(
        "--to",
        required=True,
        action="append",
        dest="to_addresses",
        help="Notification recipient. Repeat --to to configure multiple recipients.",
    )
    email_set.add_argument("--from-address", required=True)
    email_set.add_argument("--smtp-host", required=True)
    email_set.add_argument("--smtp-port", type=int, default=587)
    email_set.add_argument("--username")
    email_set.add_argument(
        "--security",
        choices=("starttls", "ssl", "none"),
        default="starttls",
    )
    recipient = email_actions.add_parser(
        "recipient",
        help="Add, remove, or list email notification recipients",
    )
    recipient_actions = recipient.add_subparsers(dest="recipient_action", required=True)
    recipient_add = recipient_actions.add_parser("add", help="Add recipient addresses")
    recipient_add.add_argument("addresses", nargs="+")
    recipient_remove = recipient_actions.add_parser("remove", help="Remove recipient addresses")
    recipient_remove.add_argument("addresses", nargs="+")
    recipient_actions.add_parser("list", help="List recipient addresses")

    email_actions.add_parser("on", help="Enable email for future notifications")
    email_actions.add_parser("off", help="Disable email and cancel pending email deliveries")

    return parser.parse_args(argv)


def parse_notifications_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="monitor notifications",
        description="List durable notification outbox events.",
    )
    parser.add_argument(
        "--status",
        choices=("pending", "delivered", "cancelled"),
        help="Filter notification events by delivery status.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of events to return (default: 50).",
    )
    return parser.parse_args(argv)


def parse_metric_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="monitor metric",
        description="Set or delete a project-specific metric.",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    set_parser = subparsers.add_parser("set", help="Set or update a metric")
    set_parser.add_argument("key", help="Stable metric key")
    set_parser.add_argument("value", help="Metric value")
    set_parser.add_argument("--label", help="Human-readable dashboard label")
    set_parser.add_argument("--unit", help="Optional display unit")

    delete_parser = subparsers.add_parser("delete", help="Delete a metric")
    delete_parser.add_argument("key", help="Metric key")
    return parser.parse_args(argv)


def parse_artifact_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="monitor artifact",
        description="Register a file snapshot as the latest artifact.",
    )
    parser.add_argument("path", help="Artifact file path")
    parser.add_argument("--name", help="Display name shown on the dashboard")
    return parser.parse_args(argv)


def parse_cost_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="monitor cost",
        description="Show an optional Codex usage estimate at Standard API rates.",
    )
    parser.add_argument("action", choices=("enable", "disable", "status"))
    return parser.parse_args(argv)


def parse_usage_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="monitor usage",
        description="Show the signed-in Codex account's weekly remaining limit.",
    )
    parser.add_argument("action", choices=("enable", "disable", "status"))
    return parser.parse_args(argv)


def parse_registry_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="monitor registry",
        description="Manage dashboard links in this project's registry.",
    )
    actions = parser.add_subparsers(dest="action", required=True)
    add = actions.add_parser("add", help="Register a reachable Monitor dashboard URL")
    add.add_argument("url")
    remove = actions.add_parser("remove", help="Remove a registered project ID")
    remove.add_argument("project_id")
    actions.add_parser("list", help="List registered projects without probing them")
    return parser.parse_args(argv)


def serve(port: int) -> None:
    if not dashboard_path().is_file():
        raise SystemExit(f"dashboard.html was not found at {dashboard_path()}")

    connect_db().close()

    server = ThreadingHTTPServer((HOST, port), DashboardHandler)
    stop_notifications = threading.Event()
    notification_thread = threading.Thread(
        target=_notification_dispatch_loop,
        args=(stop_notifications,),
        name="ai-agent-monitor-notifications",
        daemon=True,
    )
    notification_thread.start()
    telegram_thread = threading.Thread(
        target=_telegram_reply_loop,
        args=(stop_notifications,),
        name="ai-agent-monitor-telegram-replies",
        daemon=True,
    )
    telegram_thread.start()
    stop_runners = threading.Event()
    runner_thread = threading.Thread(
        target=_runner_dispatch_loop,
        args=(stop_runners,),
        name="ai-agent-monitor-runners",
        daemon=True,
    )
    runner_thread.start()
    url = f"http://{HOST}:{port}"

    print(f"AI Agent Monitor is running at {url}")
    print("Press Ctrl+C to stop.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping AI Agent Monitor.")
    finally:
        stop_notifications.set()
        stop_runners.set()
        notification_thread.join(timeout=2)
        telegram_thread.join(timeout=2)
        runner_thread.join(timeout=2)
        server.server_close()


def main() -> None:
    # CLI JSON escapes non-ASCII text so Windows PowerShell can parse it even
    # when Python's native stdout encoding is CP932. HTTP JSON remains UTF-8.
    # Human-readable CLI output may contain characters outside CP932. Print it
    # as UTF-8 so recording a valid message cannot fail after the database commit.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="backslashreplace")
    if len(sys.argv) > 1 and sys.argv[1] == "registry":
        args = parse_registry_args(sys.argv[2:])
        path = registry_path()
        if args.action == "add":
            init_project()
            entry = project_registry.add_project(path, args.url)
            print(json.dumps(entry, ensure_ascii=True, indent=2))
            return
        if args.action == "remove":
            removed = project_registry.remove_project(path, args.project_id)
            print("Project removed." if removed else "Project not found.")
            return
        print(json.dumps({"projects": project_registry.load_entries(path)},
                         ensure_ascii=True, indent=2))
        return

    if len(sys.argv) > 1 and sys.argv[1] == "runner":
        args = parse_runner_args(sys.argv[2:])
        if args.action == "register":
            runner = register_runner(args.adapter, args.external_id)
            print(
                json.dumps(
                    {
                        "id": runner["id"],
                        "adapter": runner["adapter"],
                        "state": runner["state"],
                    },
                    ensure_ascii=True,
                    indent=2,
                )
            )
            return
        if args.action == "status":
            print(
                json.dumps(
                    {
                        "config": runner_config_public(),
                        **runner_snapshot(),
                    },
                    ensure_ascii=True,
                    indent=2,
                )
            )
            return
        if args.action == "complete":
            print(
                json.dumps(
                    set_current_runner_state("completed"),
                    ensure_ascii=True,
                    indent=2,
                )
            )
            return
        if args.action == "stop":
            print(
                json.dumps(
                    set_current_runner_state("stopped"),
                    ensure_ascii=True,
                    indent=2,
                )
            )
            return
        if args.action == "auto-resume":
            config = set_auto_resume_enabled(args.enabled == "on")
            print(
                f"Automatic resume {'enabled' if config['auto_resume'] else 'disabled'}."
            )
            return
        if args.action == "codex":
            command = configure_codex_command(args.command)
            print(f"Codex command configured: {command}")
            return
        if args.action == "preflight":
            result = run_codex_preflight()
            print(json.dumps(result, ensure_ascii=True, indent=2))
            if not result["ok"]:
                raise SystemExit(1)
            return
        if args.action == "dispatch":
            print(
                json.dumps(
                    dispatch_resume_requests(force=True),
                    ensure_ascii=True,
                    indent=2,
                )
            )
            return
        if args.action == "retry":
            print(
                json.dumps(
                    retry_resume_request(args.request_id),
                    ensure_ascii=True,
                    indent=2,
                )
            )
            return
        if args.action == "worker":
            raise SystemExit(run_resume_worker(args.attempt_id))

    if len(sys.argv) > 1 and sys.argv[1] == "notify":
        args = parse_notify_args(sys.argv[2:])
        if args.section == "status":
            print(json.dumps(notification_config_public(), ensure_ascii=True, indent=2))
            return
        if args.section == "send":
            print(
                json.dumps(
                    dispatch_pending_notifications(force=True),
                    ensure_ascii=True,
                    indent=2,
                )
            )
            return
        if args.section == "telegram":
            if args.action == "replies":
                if args.replies_action == "poll":
                    print(json.dumps(poll_telegram_replies_once(), ensure_ascii=True))
                else:
                    enabled = args.replies_action == "on"
                    set_telegram_replies_enabled(enabled)
                    print(f"Telegram replies {'enabled' if enabled else 'disabled'}.")
                return
            if args.action == "set":
                configure_telegram(args.chat_id)
                print("Telegram chat ID configured.")
                return
            if args.action == "discover":
                chat_id = discover_telegram_chat_id()
                configure_telegram(chat_id)
                print(f"Telegram chat ID discovered: {chat_id}")
                return
            enabled = args.action == "on"
            set_notification_channel_enabled("telegram", enabled)
            print(f"Telegram notifications {'enabled' if enabled else 'disabled'}.")
            return
        if args.section == "email":
            if args.action == "set":
                configure_email(
                    to_addresses=args.to_addresses,
                    from_address=args.from_address,
                    smtp_host=args.smtp_host,
                    smtp_port=args.smtp_port,
                    username=args.username,
                    security=args.security,
                )
                print("Email notification settings configured.")
                return
            if args.action == "recipient":
                if args.recipient_action == "add":
                    recipients = add_email_recipients(args.addresses)
                elif args.recipient_action == "remove":
                    recipients = remove_email_recipients(args.addresses)
                else:
                    recipients = email_recipients()
                print(
                    json.dumps(
                        {"recipients": recipients},
                        ensure_ascii=True,
                        indent=2,
                    )
                )
                return
            enabled = args.action == "on"
            set_notification_channel_enabled("email", enabled)
            print(f"Email notifications {'enabled' if enabled else 'disabled'}.")
            return

    if len(sys.argv) > 1 and sys.argv[1] == "doctor":
        args = parse_doctor_args(sys.argv[2:])
        report = doctor_snapshot()
        if args.json:
            print(json.dumps(report, ensure_ascii=True, indent=2))
        else:
            print(format_doctor_report(report), end="")
        if report["overall"] == "error":
            raise SystemExit(1)
        return

    if len(sys.argv) > 1 and sys.argv[1] == "agent":
        args = parse_agent_args(sys.argv[2:])
        if args.action == "install":
            result = install_agent_integration(args.kind)
            print(f"Agent integration installed: {result['kind']}")
            if result["agents_file"]:
                print(f"AGENTS.md: {result['agents_file']}")
            if result["skill"]:
                print(f"Skill: {result['skill']}")
            print(f"Contract: {result['contract']}")
            return
        if args.action == "remove":
            result = remove_agent_integration(args.kind)
            print(f"Agent integration removed: {result['kind']}")
            return

        print(emit_agent_integration(args.kind), end="")
        return

    if len(sys.argv) > 1 and sys.argv[1] == "startup":
        args = parse_startup_args(sys.argv[2:])
        if args.action == "install":
            state = startup_install()
            print(f"Startup launcher installed: {state['launcher_path']}")
            return
        if args.action == "status":
            print(json.dumps(startup_status(), ensure_ascii=True, indent=2))
            return

        result = startup_remove()
        print("Startup task removed." if result["removed"] else "Startup task is not installed.")
        return

    if len(sys.argv) > 1 and sys.argv[1] == "remote":
        args = parse_remote_args(sys.argv[2:])
        if args.action == "start":
            state = remote_start()
            print(f"Remote monitor: {state['tailnet_url']}")
            print(f"Backend: {state['backend_url']}")
            return
        if args.action == "status":
            print(json.dumps(remote_status(), ensure_ascii=True, indent=2))
            return

        result = remote_stop()
        print("Remote monitor stopped." if result["stopped"] else "Remote monitor is not configured.")
        return

    if len(sys.argv) > 1 and sys.argv[1] == "init":
        args = parse_init_args(sys.argv[2:])
        result = init_project(args.dashboard)
        print(f"Project monitor initialized: {result['data_dir']}")
        if result["dashboard"]:
            print(f"Project dashboard: {result['dashboard']}")
        return

    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        args = parse_server_args(sys.argv[2:])
        serve(args.port)
        return

    if len(sys.argv) > 1 and sys.argv[1] == "progress":
        args = parse_progress_args(sys.argv[2:])
        if args.action == "translate":
            set_progress_translation(args.id, " ".join(args.translation))
            print(f"Japanese display text saved for progress {args.id}.")
        else:
            event = record_progress(" ".join(args.message), args.ja)
            print(f"Progress recorded: {event['message']}")
        return

    if len(sys.argv) > 1 and sys.argv[1] == "translate":
        args = parse_translate_args(sys.argv[2:])
        translated = " ".join(args.translation)
        if args.entity_type == "progress":
            set_progress_translation(args.id, translated)
        else:
            set_display_translation(
                args.entity_type, 1 if args.entity_type == "task" else args.id, translated
            )
        print("Japanese display text saved.")
        return

    if len(sys.argv) > 1 and sys.argv[1] == "task":
        args = parse_task_args(sys.argv[2:])
        if args.action == "start":
            task = start_task(" ".join(args.title))
            print(f"Current task: {task['title']}")
            return

        completed = complete_task()
        if completed:
            print("Current task completed.")
        else:
            print("No active task.")
        return

    if len(sys.argv) > 1 and sys.argv[1] == "ask":
        args = parse_ask_args(sys.argv[2:])
        question = ask_question(" ".join(args.question))
        print(f"Question #{question['id']}: {question['question']}")
        return

    if len(sys.argv) > 1 and sys.argv[1] == "status":
        print(json.dumps(status_snapshot(), ensure_ascii=True, indent=2))
        return

    if len(sys.argv) > 1 and sys.argv[1] == "telemetry":
        print(json.dumps(telemetry_snapshot(), ensure_ascii=True, indent=2))
        return

    if len(sys.argv) > 1 and sys.argv[1] == "answers":
        print(json.dumps({"answers": list_answered_questions()}, ensure_ascii=True, indent=2))
        return

    if len(sys.argv) > 1 and sys.argv[1] == "notifications":
        args = parse_notifications_args(sys.argv[2:])
        print(
            json.dumps(
                {"notifications": list_notification_outbox(status=args.status, limit=args.limit)},
                ensure_ascii=True,
                indent=2,
            )
        )
        return

    if len(sys.argv) > 1 and sys.argv[1] == "artifact":
        args = parse_artifact_args(sys.argv[2:])
        artifact = register_artifact(args.path, args.name)
        print(
            f"Artifact #{artifact['id']}: {artifact['display_name']} "
            f"({artifact['sha256']})"
        )
        return

    if len(sys.argv) > 1 and sys.argv[1] == "metric":
        args = parse_metric_args(sys.argv[2:])
        if args.action == "set":
            metric = set_metric(
                args.key,
                args.value,
                label=args.label,
                unit=args.unit,
            )
            display_value = metric["value"]
            if metric["unit"]:
                display_value = f"{display_value} {metric['unit']}"
            print(f"{metric['label']}: {display_value}")
            return

        deleted = delete_metric(args.key)
        print("Metric deleted." if deleted else "Metric not found.")
        return

    if len(sys.argv) > 1 and sys.argv[1] == "metrics":
        print(json.dumps({"metrics": list_metrics()}, ensure_ascii=True, indent=2))
        return

    if len(sys.argv) > 1 and sys.argv[1] == "cost":
        args = parse_cost_args(sys.argv[2:])
        if args.action == "enable":
            codex_cost.set_enabled(CODEX_COST_CONFIG_PATH, True)
        elif args.action == "disable":
            codex_cost.set_enabled(CODEX_COST_CONFIG_PATH, False)
        print(json.dumps({"enabled": codex_cost.enabled(CODEX_COST_CONFIG_PATH)},
                         ensure_ascii=True, indent=2))
        return

    if len(sys.argv) > 1 and sys.argv[1] == "usage":
        args = parse_usage_args(sys.argv[2:])
        if args.action == "enable":
            codex_usage.set_enabled(CODEX_USAGE_CONFIG_PATH, True)
        elif args.action == "disable":
            codex_usage.set_enabled(CODEX_USAGE_CONFIG_PATH, False)
        print(json.dumps({"enabled": codex_usage.enabled(CODEX_USAGE_CONFIG_PATH)},
                         ensure_ascii=True, indent=2))
        return

    args = parse_server_args(sys.argv[1:])
    serve(args.port)


if __name__ == "__main__":
    main()
