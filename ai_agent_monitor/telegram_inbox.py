"""Durable, bot-wide Telegram inbox shared by projects on one host."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path


BASE_DIR = Path(
    os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share"
) / "ai-agent-monitor" / "telegram"
POLL_LEASE_SECONDS = 20
RETAIN_RECENT_UPDATES = 1000


class SharedTelegramInbox:
    def __init__(self, token: str):
        fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()[:24]
        self.path = BASE_DIR / f"{fingerprint}.db"

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS poll_state ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), "
            "next_offset INTEGER NOT NULL, owner TEXT, lease_until REAL NOT NULL)"
        )
        connection.execute(
            "INSERT OR IGNORE INTO poll_state VALUES (1, 0, NULL, 0)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS updates ("
            "update_id INTEGER PRIMARY KEY, payload_json TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS consumers ("
            "project_id TEXT PRIMARY KEY, next_offset INTEGER NOT NULL)"
        )
        connection.commit()
        return connection

    def register(self, project_id: str, *, start_at_current: bool, legacy_offset: int = 0) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            current = int(connection.execute(
                "SELECT next_offset FROM poll_state WHERE id = 1"
            ).fetchone()[0])
            offset = current if start_at_current else legacy_offset
            connection.execute(
                "INSERT OR IGNORE INTO consumers VALUES (?, ?)", (project_id, offset)
            )

    def unregister(self, project_id: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM consumers WHERE project_id = ?", (project_id,))
            self._prune(connection)

    def claim_poll(self) -> tuple[str, int] | None:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            offset, owner, lease_until = connection.execute(
                "SELECT next_offset, owner, lease_until FROM poll_state WHERE id = 1"
            ).fetchone()
            if owner and lease_until > time.time():
                return None
            claim = uuid.uuid4().hex
            connection.execute(
                "UPDATE poll_state SET owner = ?, lease_until = ? WHERE id = 1",
                (claim, time.time() + POLL_LEASE_SECONDS),
            )
            return claim, int(offset)

    def save_poll(self, claim: str, updates: list[object]) -> int:
        saved = 0
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT owner, next_offset FROM poll_state WHERE id = 1"
            ).fetchone()
            if row[0] != claim:
                return 0
            starting_offset = int(row[1])
            next_offset = starting_offset
            for update in updates:
                if not isinstance(update, dict):
                    continue
                update_id = update.get("update_id")
                if isinstance(update_id, bool) or not isinstance(update_id, int) or update_id < 0:
                    continue
                if update_id < starting_offset:
                    continue
                next_offset = max(next_offset, update_id + 1)
                message = update.get("message")
                if (
                    not isinstance(message, dict)
                    or not isinstance(message.get("text"), str)
                    or not isinstance(message.get("reply_to_message"), dict)
                ):
                    continue
                connection.execute(
                    "INSERT OR IGNORE INTO updates VALUES (?, ?)",
                    (update_id, json.dumps(update, ensure_ascii=False)),
                )
                saved += 1
            connection.execute(
                "UPDATE poll_state SET next_offset = ?, owner = NULL, lease_until = 0 "
                "WHERE id = 1",
                (next_offset,),
            )
        return saved

    def release_poll(self, claim: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "UPDATE poll_state SET owner = NULL, lease_until = 0 "
                "WHERE id = 1 AND owner = ?",
                (claim,),
            )

    def pending(self, project_id: str, limit: int = 100) -> list[dict[str, object]]:
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                "SELECT u.payload_json FROM updates AS u "
                "JOIN consumers AS c ON c.project_id = ? "
                "WHERE u.update_id >= c.next_offset "
                "ORDER BY u.update_id LIMIT ?",
                (project_id, limit),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def advance(self, project_id: str, next_offset: int) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE consumers SET next_offset = MAX(next_offset, ?) "
                "WHERE project_id = ?",
                (next_offset, project_id),
            )
            self._prune(connection)

    @staticmethod
    def _prune(connection: sqlite3.Connection) -> None:
        minimum = connection.execute(
            "SELECT MIN(next_offset) FROM consumers"
        ).fetchone()[0]
        # Keep recent acknowledgements for a project upgrading from its old
        # local cursor after another project began using the shared inbox.
        old_boundary = connection.execute(
            "SELECT update_id FROM updates ORDER BY update_id DESC LIMIT 1 OFFSET ?",
            (RETAIN_RECENT_UPDATES - 1,),
        ).fetchone()
        if old_boundary is not None:
            if minimum is None:
                connection.execute(
                    "DELETE FROM updates WHERE update_id < ?", (old_boundary[0],)
                )
            else:
                connection.execute(
                    "DELETE FROM updates WHERE update_id < ? AND update_id < ?",
                    (old_boundary[0], minimum),
                )
