#!/usr/bin/env python3
"""Serve the AI Agent Monitor dashboard and record progress events."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
ROOT_PATH = Path(__file__).resolve().parent
DASHBOARD_PATH = ROOT_PATH / "dashboard.html"
DATA_DIR = ROOT_PATH / ".agent-monitor"
DB_PATH = DATA_DIR / "monitor.db"


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
    connection.commit()
    return connection


def record_progress(message: str) -> dict[str, object]:
    message = message.strip()
    if not message:
        raise ValueError("Progress message must not be empty.")

    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )

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


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]

        if path in ("/", "/dashboard.html"):
            self._serve_dashboard()
            return

        if path == "/api/progress":
            self._serve_progress()
            return

        self.send_error(404, "Not Found")

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

    def _serve_progress(self) -> None:
        payload = json.dumps(
            {"progress": list_progress()},
            ensure_ascii=False,
        ).encode("utf-8")

        self.send_response(200)
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

    args = parse_server_args(sys.argv[1:])
    serve(args.port)


if __name__ == "__main__":
    main()
