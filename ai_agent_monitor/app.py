#!/usr/bin/env python3
"""Serve the AI Agent Monitor dashboard and record agent state."""

from __future__ import annotations

import argparse
import hashlib
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
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parseaddr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

from . import __version__

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_JSON_BODY = 64 * 1024
STATUS_SCHEMA_VERSION = 1
STATUS_PROGRESS_LIMIT = 10
STATUS_ANSWER_LIMIT = 10
PACKAGE_PATH = Path(__file__).resolve().parent
PROJECT_ROOT = Path.cwd().resolve()
DEFAULT_DASHBOARD_PATH = PACKAGE_PATH / "dashboard.html"
AGENT_CONTRACT_PATH = PACKAGE_PATH / "agent_integration.md"
CODEX_SKILL_PATH = PACKAGE_PATH / "codex_skill.md"
AGENTS_BLOCK_START = "<!-- ai-agent-monitor:start -->"
AGENTS_BLOCK_END = "<!-- ai-agent-monitor:end -->"
AGENTS_BLOCK = """<!-- ai-agent-monitor:start -->
## AI Agent Monitor

For development work in this repository, use the `ai-agent-monitor` repository skill in `.agents/skills/ai-agent-monitor/SKILL.md`. At the start of a new run or when resuming work, run `monitor status` before reporting new monitor state.
<!-- ai-agent-monitor:end -->"""
DATA_DIR = PROJECT_ROOT / ".agent-monitor"
DB_PATH = DATA_DIR / "monitor.db"


PROJECT_GITIGNORE = """monitor.db
monitor.db-shm
monitor.db-wal
monitor.db-journal
notifications.json
artifacts/
runtime/
"""


def dashboard_path() -> Path:
    project_dashboard = DATA_DIR / "dashboard.html"
    if project_dashboard.is_file():
        return project_dashboard
    return DEFAULT_DASHBOARD_PATH


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
            created_at TEXT NOT NULL
        )
        """
    )
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


def record_progress(message: str) -> dict[str, object]:
    message = message.strip()
    if not message:
        raise ValueError("Progress message must not be empty.")

    created_at = utc_now()

    with database_session() as connection:
        cursor = connection.execute(
            "INSERT INTO progress (message, created_at) VALUES (?, ?)",
            (message, created_at),
        )
        progress_id = cursor.lastrowid

    return {
        "id": progress_id,
        "message": message,
        "created_at": created_at,
    }


def list_progress(limit: int = 50) -> list[dict[str, object]]:
    with database_session() as connection:
        rows = connection.execute(
            """
            SELECT id, message, created_at
            FROM progress
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [
        {"id": row[0], "message": row[1], "created_at": row[2]}
        for row in rows
    ]


def start_task(title: str) -> dict[str, str]:
    title = title.strip()
    if not title:
        raise ValueError("Task title must not be empty.")

    started_at = utc_now()

    with database_session() as connection:
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
    }


def get_current_task() -> dict[str, str] | None:
    with database_session() as connection:
        row = connection.execute(
            "SELECT title, started_at FROM current_task WHERE id = 1"
        ).fetchone()

    if row is None:
        return None

    return {
        "title": row[0],
        "started_at": row[1],
    }


def complete_task() -> bool:
    with database_session() as connection:
        cursor = connection.execute("DELETE FROM current_task WHERE id = 1")
        return cursor.rowcount > 0


TELEGRAM_TOKEN_ENV = "AI_AGENT_MONITOR_TELEGRAM_BOT_TOKEN"
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

    if DB_PATH.is_file():
        cancelled_at = utc_now()
        with database_session() as connection:
            notification_ids = [
                int(row[0])
                for row in connection.execute(
                    """
                    SELECT DISTINCT notification_id
                    FROM notification_deliveries
                    WHERE channel = 'email' AND status = 'pending'
                    AND lower(target) IN ({})
                    """.format(",".join("?" for _ in removals)),
                    tuple(removals),
                ).fetchall()
            ] if removals else []
            if removals:
                connection.execute(
                    """
                    UPDATE notification_deliveries
                    SET status = 'cancelled', cancelled_at = ?
                    WHERE channel = 'email' AND status = 'pending'
                    AND lower(target) IN ({})
                    """.format(",".join("?" for _ in removals)),
                    (cancelled_at, *removals),
                )
            for notification_id in notification_ids:
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

    settings["enabled"] = enabled
    write_notification_config(config)

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
            last_error
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
        }
        for row in rows
    ]


def _refresh_notification_status(
    connection: sqlite3.Connection,
    notification_id: int,
) -> None:
    rows = connection.execute(
        """
        SELECT channel, status, attempt_count, last_attempt_at, delivered_at, last_error
        FROM notification_deliveries
        WHERE notification_id = ?
        """,
        (notification_id,),
    ).fetchall()
    if not rows:
        return

    statuses = {row[1] for row in rows}
    attempt_count = sum(int(row[2]) for row in rows)
    last_attempts = [row[3] for row in rows if row[3]]
    delivered_times = [row[4] for row in rows if row[4]]
    errors = [f"{row[0]}: {row[5]}" for row in rows if row[5]]

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
                    last_error = NULL
                WHERE id = ?
                """,
                (attempt_count, attempted_at, attempted_at, delivery_id),
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


def _notification_text(payload: dict[str, object]) -> str:
    project = payload.get("project")
    project_name = (
        project.get("name")
        if isinstance(project, dict) and isinstance(project.get("name"), str)
        else PROJECT_ROOT.name
    )
    question_id = payload.get("question_id")
    question = payload.get("question")
    lines = [
        "AI Agent Monitor",
        f"Project: {project_name}",
        f"Question #{question_id}: {question}",
    ]
    dashboard_url = _notification_dashboard_url()
    if dashboard_url:
        lines.extend(["", f"Dashboard: {dashboard_url}"])
    return "\n".join(lines)


def _send_telegram_notification(payload: dict[str, object]) -> None:
    config = read_notification_config()["telegram"]
    if config.get("enabled") is not True:
        raise RuntimeError("Telegram notifications are disabled.")

    chat_id = config.get("chat_id")
    if not isinstance(chat_id, str) or not chat_id:
        raise RuntimeError("Telegram chat ID is not configured.")

    token = os.environ.get(TELEGRAM_TOKEN_ENV)
    if not token:
        raise RuntimeError(f"{TELEGRAM_TOKEN_ENV} is not set.")

    text = _notification_text(payload)
    if len(text) > 4096:
        text = text[:4093] + "..."

    body = json.dumps(
        {
            "chat_id": chat_id,
            "text": text,
        },
        ensure_ascii=False,
    ).encode("utf-8")
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
        try:
            if channel == "telegram":
                _send_telegram_notification(payload)
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
            record_notification_delivery_attempt(delivery_id, delivered=True)
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


def ask_question(question: str) -> dict[str, object]:
    question = question.strip()
    if not question:
        raise ValueError("Question must not be empty.")

    created_at = utc_now()

    with database_session() as connection:
        cursor = connection.execute(
            """
            INSERT INTO questions (question, status, created_at)
            VALUES (?, 'open', ?)
            """,
            (question, created_at),
        )
        question_id = int(cursor.lastrowid)
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
    }


def list_open_questions(limit: int | None = 50) -> list[dict[str, object]]:
    query = """
        SELECT id, question, created_at
        FROM questions
        WHERE status = 'open'
        ORDER BY id DESC
    """
    parameters: tuple[object, ...] = ()
    if limit is not None:
        query += " LIMIT ?"
        parameters = (limit,)

    with database_session() as connection:
        rows = connection.execute(query, parameters).fetchall()

    return [
        {"id": row[0], "question": row[1], "created_at": row[2]}
        for row in rows
    ]


def answer_question(question_id: int, answer: str) -> dict[str, object]:
    answer = answer.strip()
    if not answer:
        raise ValueError("Answer must not be empty.")

    answered_at = utc_now()

    with database_session() as connection:
        row = connection.execute(
            "SELECT question, status FROM questions WHERE id = ?",
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


def list_answered_questions(limit: int = 50) -> list[dict[str, object]]:
    with database_session() as connection:
        rows = connection.execute(
            """
            SELECT id, question, answer, created_at, answered_at
            FROM questions
            WHERE status = 'answered'
            ORDER BY answered_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [
        {
            "id": row[0],
            "question": row[1],
            "answer": row[2],
            "created_at": row[3],
            "answered_at": row[4],
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
                id,
                display_name,
                original_name,
                storage_name,
                size_bytes,
                sha256,
                mime_type,
                git_commit,
                created_at
            FROM artifacts
            ORDER BY id DESC
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
                id,
                display_name,
                original_name,
                storage_name,
                size_bytes,
                sha256,
                mime_type,
                git_commit,
                created_at
            FROM artifacts
            WHERE id = ?
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
    "questions": {"id", "question", "status", "created_at", "answer", "answered_at"},
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
        for key in ("to", "from_address", "smtp_host", "smtp_port"):
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
            self._serve_json({"answers": list_answered_questions()})
            return

        if path == "/api/artifacts/latest":
            self._serve_json({"artifact": get_latest_artifact()})
            return

        if path == "/api/metrics":
            self._serve_json({"metrics": list_metrics()})
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
            content = dashboard_path().read_bytes()
        except FileNotFoundError:
            self.send_error(500, "dashboard.html is missing")
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
    parser = argparse.ArgumentParser(
        prog="monitor progress",
        description="Record a progress message for the dashboard.",
    )
    parser.add_argument("message", nargs="+", help="Progress message to record")
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

    email = subparsers.add_parser("email", help="Configure optional email notifications")
    email_actions = email.add_subparsers(dest="action", required=True)
    email_set = email_actions.add_parser("set", help="Set SMTP email settings")
    email_set.add_argument("--to", required=True, dest="to_address")
    email_set.add_argument("--from-address", required=True)
    email_set.add_argument("--smtp-host", required=True)
    email_set.add_argument("--smtp-port", type=int, default=587)
    email_set.add_argument("--username")
    email_set.add_argument(
        "--security",
        choices=("starttls", "ssl", "none"),
        default="starttls",
    )
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
    url = f"http://{HOST}:{port}"

    print(f"AI Agent Monitor is running at {url}")
    print("Press Ctrl+C to stop.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping AI Agent Monitor.")
    finally:
        stop_notifications.set()
        notification_thread.join(timeout=2)
        server.server_close()


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "notify":
        args = parse_notify_args(sys.argv[2:])
        if args.section == "status":
            print(json.dumps(notification_config_public(), ensure_ascii=False, indent=2))
            return
        if args.section == "send":
            print(
                json.dumps(
                    dispatch_pending_notifications(force=True),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        if args.section == "telegram":
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
                    to_address=args.to_address,
                    from_address=args.from_address,
                    smtp_host=args.smtp_host,
                    smtp_port=args.smtp_port,
                    username=args.username,
                    security=args.security,
                )
                print("Email notification settings configured.")
                return
            enabled = args.action == "on"
            set_notification_channel_enabled("email", enabled)
            print(f"Email notifications {'enabled' if enabled else 'disabled'}.")
            return

    if len(sys.argv) > 1 and sys.argv[1] == "doctor":
        args = parse_doctor_args(sys.argv[2:])
        report = doctor_snapshot()
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
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
            print(json.dumps(startup_status(), ensure_ascii=False, indent=2))
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
            print(json.dumps(remote_status(), ensure_ascii=False, indent=2))
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
        event = record_progress(" ".join(args.message))
        print(f"Progress recorded: {event['message']}")
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
        print(json.dumps(status_snapshot(), ensure_ascii=False, indent=2))
        return

    if len(sys.argv) > 1 and sys.argv[1] == "answers":
        print(json.dumps({"answers": list_answered_questions()}, ensure_ascii=False, indent=2))
        return

    if len(sys.argv) > 1 and sys.argv[1] == "notifications":
        args = parse_notifications_args(sys.argv[2:])
        print(
            json.dumps(
                {"notifications": list_notification_outbox(status=args.status, limit=args.limit)},
                ensure_ascii=False,
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
        print(json.dumps({"metrics": list_metrics()}, ensure_ascii=False, indent=2))
        return

    args = parse_server_args(sys.argv[1:])
    serve(args.port)


if __name__ == "__main__":
    main()
