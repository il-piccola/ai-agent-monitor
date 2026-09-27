"""Read-only measurements derived from authoritative project records.

No provider token, cost, or tool-error numbers are inferred here. Each value
names its database source and the time at which it was observed.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_VERSION = 1


def _timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


def _seconds(start: str, end: str) -> int | None:
    start_time = _timestamp(start)
    end_time = _timestamp(end)
    if start_time is None or end_time is None or end_time < start_time:
        return None
    return int((end_time - start_time).total_seconds())


def snapshot(
    db_path: Path,
    project: dict[str, str],
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    observed = now or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        raise ValueError("Telemetry observation time must include a timezone.")
    observed_at = observed.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )
    measurements: list[dict[str, object]] = []

    def add(
        key: str, label: str, value: int | None, unit: str, source: str,
        source_at: str | None = None,
    ) -> None:
        if value is not None:
            measurements.append({
                "key": key, "label": label, "value": value, "unit": unit,
                "source": source, "source_at": source_at,
                "observed_at": observed_at,
            })

    result: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "project": project,
        "observed_at": observed_at,
        "measurements": measurements,
    }
    if not db_path.is_file():
        return result

    # mode=ro ensures a telemetry read cannot create or migrate a database.
    with closing(sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)) as db:
        tables = {
            row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

        if "current_task" in tables:
            row = db.execute(
                "SELECT started_at FROM current_task WHERE id = 1"
            ).fetchone()
            if row:
                add(
                    "task.active_seconds", "現在のタスクの経過時間",
                    _seconds(row[0], observed_at), "秒",
                    "current_task.started_at", row[0],
                )

        if "task_runs" in tables:
            add(
                "task.completed_count", "完了したタスク",
                db.execute(
                    "SELECT COUNT(*) FROM task_runs WHERE outcome = 'completed'"
                ).fetchone()[0],
                "件", "task_runs.outcome",
            )
            row = db.execute(
                """
                SELECT started_at, ended_at FROM task_runs
                WHERE outcome = 'completed' ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
            if row:
                add(
                    "task.last_completed_seconds", "直近の完了タスクの所要時間",
                    _seconds(row[0], row[1]), "秒",
                    "task_runs.started_at,ended_at", row[1],
                )

        if "questions" in tables:
            for status, key, label in (
                ("open", "question.open_count", "未回答の質問"),
                ("answered", "question.answered_count", "回答済みの質問"),
            ):
                add(
                    key, label,
                    db.execute(
                        "SELECT COUNT(*) FROM questions WHERE status = ?", (status,)
                    ).fetchone()[0],
                    "件", "questions.status",
                )
            row = db.execute(
                """
                SELECT created_at FROM questions WHERE status = 'open'
                ORDER BY created_at ASC, id ASC LIMIT 1
                """
            ).fetchone()
            if row:
                add(
                    "question.oldest_open_seconds", "最も古い未回答の待機時間",
                    _seconds(row[0], observed_at), "秒",
                    "questions.created_at", row[0],
                )
            row = db.execute(
                """
                SELECT created_at, answered_at FROM questions
                WHERE status = 'answered' AND answered_at IS NOT NULL
                ORDER BY answered_at DESC, id DESC LIMIT 1
                """
            ).fetchone()
            if row:
                add(
                    "question.last_answer_wait_seconds", "直近の回答までの待機時間",
                    _seconds(row[0], row[1]), "秒",
                    "questions.created_at,answered_at", row[1],
                )

        if "resume_attempts" in tables:
            for status, key, label in (
                ("completed", "resume.completed_count", "成功した自動再開"),
                ("failed", "resume.failed_count", "失敗した自動再開"),
            ):
                add(
                    key, label,
                    db.execute(
                        "SELECT COUNT(*) FROM resume_attempts WHERE status = ?",
                        (status,),
                    ).fetchone()[0],
                    "件", "resume_attempts.status",
                )
            row = db.execute(
                """
                SELECT started_at, finished_at FROM resume_attempts
                WHERE status IN ('completed', 'failed') AND finished_at IS NOT NULL
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
            if row:
                add(
                    "resume.last_attempt_seconds", "直近の自動再開の所要時間",
                    _seconds(row[0], row[1]), "秒",
                    "resume_attempts.started_at,finished_at", row[1],
                )

    return result
