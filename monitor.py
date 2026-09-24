#!/usr/bin/env python3
"""Serve the AI Agent Monitor dashboard and record agent state."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_JSON_BODY = 64 * 1024
ROOT_PATH = Path(__file__).resolve().parent
DASHBOARD_PATH = ROOT_PATH / "dashboard.html"
DATA_DIR = ROOT_PATH / ".agent-monitor"
DB_PATH = DATA_DIR / "monitor.db"


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
    connection.commit()
    return connection


def record_progress(message: str) -> dict[str, object]:
    message = message.strip()
    if not message:
        raise ValueError("Progress message must not be empty.")

    created_at = utc_now()

    with connect_db() as connection:
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
    with connect_db() as connection:
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

    with connect_db() as connection:
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
    with connect_db() as connection:
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
    with connect_db() as connection:
        cursor = connection.execute("DELETE FROM current_task WHERE id = 1")
        return cursor.rowcount > 0


def ask_question(question: str) -> dict[str, object]:
    question = question.strip()
    if not question:
        raise ValueError("Question must not be empty.")

    created_at = utc_now()

    with connect_db() as connection:
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
    with connect_db() as connection:
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

    with connect_db() as connection:
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
    with connect_db() as connection:
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


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]

        if path in ("/", "/dashboard.html"):
            self._serve_dashboard()
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

    def _serve_dashboard(self) -> None:
        try:
            content = DASHBOARD_PATH.read_bytes()
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
        prog="monitor.py progress",
        description="Record a progress message for the dashboard.",
    )
    parser.add_argument("message", nargs="+", help="Progress message to record")
    return parser.parse_args(argv)


def parse_task_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="monitor.py task",
        description="Start or complete the current task.",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    start_parser = subparsers.add_parser("start", help="Set the current task")
    start_parser.add_argument("title", nargs="+", help="Task title")

    subparsers.add_parser("done", help="Complete the current task")
    return parser.parse_args(argv)


def parse_ask_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="monitor.py ask",
        description="Record a question for the human.",
    )
    parser.add_argument("question", nargs="+", help="Question to record")
    return parser.parse_args(argv)


def serve(port: int) -> None:
    if not DASHBOARD_PATH.is_file():
        raise SystemExit(f"dashboard.html was not found at {DASHBOARD_PATH}")

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

    args = parse_server_args(sys.argv[1:])
    serve(args.port)


if __name__ == "__main__":
    main()
