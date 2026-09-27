import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from ai_agent_monitor import app as monitor
from ai_agent_monitor import telemetry


UTC = timezone.utc


def measurement(data: dict, key: str) -> dict:
    return next(item for item in data["measurements"] if item["key"] == key)


class TelemetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / ".agent-monitor" / "monitor.db"
        for name, value in (
            ("PROJECT_ROOT", self.root),
            ("DATA_DIR", self.db.parent),
            ("DB_PATH", self.db),
        ):
            patcher = patch.object(monitor, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def snapshot(self, timestamp: str = "2026-09-27T00:01:00Z") -> dict:
        now = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return telemetry.snapshot(self.db, monitor.project_info(), now=now)

    def test_empty_project_does_not_create_database(self) -> None:
        data = self.snapshot()
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["observed_at"], "2026-09-27T00:01:00Z")
        self.assertEqual(data["measurements"], [])
        self.assertFalse(self.db.exists())

    def test_task_replacement_and_completion_have_exact_durations(self) -> None:
        with patch.object(monitor, "utc_now", side_effect=[
            "2026-09-27T00:00:00Z",
            "2026-09-27T00:00:10Z",
            "2026-09-27T00:00:50Z",
        ]):
            monitor.start_task("First")
            monitor.start_task("Second")
            self.assertEqual(
                measurement(self.snapshot("2026-09-27T00:00:30Z"),
                            "task.active_seconds")["value"], 20,
            )
            self.assertTrue(monitor.complete_task())
        self.assertFalse(monitor.complete_task())

        with monitor.database_session() as db:
            history = db.execute(
                "SELECT title, outcome FROM task_runs ORDER BY id"
            ).fetchall()
        self.assertEqual(history, [("First", "replaced"), ("Second", "completed")])
        data = self.snapshot()
        self.assertEqual(measurement(data, "task.completed_count")["value"], 1)
        self.assertEqual(measurement(data, "task.last_completed_seconds")["value"], 40)
        self.assertNotIn("task.active_seconds", [x["key"] for x in data["measurements"]])

    def test_preexisting_current_task_is_captured_when_completed(self) -> None:
        with monitor.database_session() as db:
            db.execute(
                "INSERT INTO current_task (id,title,started_at) VALUES (1,?,?)",
                ("Old task", "2026-09-27T00:00:00Z"),
            )
        with patch.object(monitor, "utc_now", return_value="2026-09-27T00:00:15Z"):
            self.assertTrue(monitor.complete_task())
        self.assertEqual(
            measurement(self.snapshot(), "task.last_completed_seconds")["value"],
            15,
        )

    def test_question_counts_and_human_wait_use_recorded_timestamps(self) -> None:
        with patch.object(monitor, "utc_now", side_effect=[
            "2026-09-27T00:00:00Z", "2026-09-27T00:00:12Z",
            "2026-09-27T00:00:20Z",
        ]):
            answered = monitor.ask_question("Answer this?")
            monitor.answer_question(answered["id"], "Yes")
            monitor.ask_question("Still open?")
        data = self.snapshot("2026-09-27T00:00:35Z")
        self.assertEqual(measurement(data, "question.open_count")["value"], 1)
        self.assertEqual(measurement(data, "question.answered_count")["value"], 1)
        self.assertEqual(measurement(data, "question.last_answer_wait_seconds")["value"], 12)
        self.assertEqual(measurement(data, "question.oldest_open_seconds")["value"], 15)
        for item in data["measurements"]:
            self.assertEqual(item["observed_at"], data["observed_at"])
            self.assertTrue(item["source"])

    def test_resume_counts_and_duration_exclude_uncertain_attempt(self) -> None:
        with monitor.database_session() as db:
            db.executemany(
                """
                INSERT INTO resume_attempts
                    (request_id,status,started_at,finished_at)
                VALUES (?,?,?,?)
                """,
                [
                    (1, "completed", "2026-09-27T00:00:00Z", "2026-09-27T00:00:08Z"),
                    (2, "failed", "2026-09-27T00:00:10Z", "2026-09-27T00:00:17Z"),
                    (3, "uncertain", "2026-09-27T00:00:20Z", "2026-09-27T00:00:25Z"),
                ],
            )
        data = self.snapshot()
        self.assertEqual(measurement(data, "resume.completed_count")["value"], 1)
        self.assertEqual(measurement(data, "resume.failed_count")["value"], 1)
        self.assertEqual(measurement(data, "resume.last_attempt_seconds")["value"], 7)
        self.assertFalse(any("tool" in x["key"] for x in data["measurements"]))

    def test_manual_metrics_stay_separate_from_automatic_measurements(self) -> None:
        monitor.set_metric("cost.total", "90.71", label="Total cost", unit="USD")
        data = self.snapshot()
        self.assertFalse(any("cost" in x["key"] for x in data["measurements"]))
        self.assertEqual(monitor.list_metrics()[0]["value"], "90.71")

    def test_api_and_cli_return_the_same_read_only_snapshot(self) -> None:
        fake = MagicMock()
        fake.path = "/api/telemetry"
        monitor.DashboardHandler.do_GET(fake)
        api_data = fake._serve_json.call_args.args[0]
        self.assertEqual(api_data["schema_version"], 1)
        self.assertFalse(self.db.exists())

        with patch.object(sys, "argv", ["monitor", "telemetry"]), \
             redirect_stdout(io.StringIO()) as output:
            monitor.main()
        self.assertEqual(json.loads(output.getvalue())["measurements"], [])
        self.assertFalse(self.db.exists())

    def test_invalid_timestamps_are_not_reported_as_durations(self) -> None:
        self.assertIsNone(telemetry._seconds("invalid", "2026-09-27T00:00:00Z"))
        self.assertIsNone(telemetry._seconds(
            "2026-09-27T00:00:10Z", "2026-09-27T00:00:00Z"
        ))


if __name__ == "__main__":
    unittest.main()
