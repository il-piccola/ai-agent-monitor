#!/usr/bin/env python3
"""Serve the AI Agent Monitor dashboard and record agent state."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_JSON_BODY = 64 * 1024
PACKAGE_PATH = Path(__file__).resolve().parent
PROJECT_ROOT = Path.cwd().resolve()
DEFAULT_DASHBOARD_PATH = PACKAGE_PATH / "dashboard.html"
DATA_DIR = PROJECT_ROOT / ".agent-monitor"
DB_PATH = DATA_DIR / "monitor.db"


PROJECT_GITIGNORE = """monitor.db
monitor.db-shm
monitor.db-wal
monitor.db-journal
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
        question_id = cursor.lastrowid

    return {
        "id": question_id,
        "question": question,
        "created_at": created_at,
    }


def list_open_questions(limit: int = 50) -> list[dict[str, object]]:
    with database_session() as connection:
        rows = connection.execute(
            """
            SELECT id, question, created_at
            FROM questions
            WHERE status = 'open'
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

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


def _find_free_backend_port() -> int:
    for port in range(8765, 8800):
        if _port_is_free(HOST, port):
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
    backend_port = _find_free_backend_port()
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
    url = f"http://{HOST}:{port}"

    print(f"AI Agent Monitor is running at {url}")
    print("Press Ctrl+C to stop.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping AI Agent Monitor.")
    finally:
        server.server_close()


def main() -> None:
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

    if len(sys.argv) > 1 and sys.argv[1] == "answers":
        print(json.dumps({"answers": list_answered_questions()}, ensure_ascii=False, indent=2))
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
