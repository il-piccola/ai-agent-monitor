import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import monitor


class MonitorStorageTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.root = root
        self.data_dir_patch = patch.object(monitor, "DATA_DIR", root / ".agent-monitor")
        self.db_path_patch = patch.object(
            monitor, "DB_PATH", root / ".agent-monitor" / "monitor.db"
        )
        self.data_dir_patch.start()
        self.db_path_patch.start()

    def tearDown(self) -> None:
        self.db_path_patch.stop()
        self.data_dir_patch.stop()
        self.temp_dir.cleanup()


class ProgressStorageTests(MonitorStorageTestCase):
    def test_progress_is_persisted_and_newest_is_first(self) -> None:
        first = monitor.record_progress("first")
        second = monitor.record_progress("second")

        items = monitor.list_progress()

        self.assertEqual([item["message"] for item in items], ["second", "first"])
        self.assertEqual(items[0]["id"], second["id"])
        self.assertEqual(items[1]["id"], first["id"])
        self.assertTrue(items[0]["created_at"].endswith("Z"))

    def test_progress_message_is_trimmed(self) -> None:
        event = monitor.record_progress("  finished benchmark  ")
        self.assertEqual(event["message"], "finished benchmark")

    def test_empty_progress_message_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            monitor.record_progress("   ")


class CurrentTaskStorageTests(MonitorStorageTestCase):
    def test_task_can_be_started_and_read(self) -> None:
        started = monitor.start_task("Build login page")
        self.assertEqual(monitor.get_current_task(), started)

    def test_starting_another_task_replaces_current_task(self) -> None:
        monitor.start_task("First task")
        second = monitor.start_task("Second task")
        self.assertEqual(monitor.get_current_task(), second)

    def test_completing_task_clears_current_task(self) -> None:
        monitor.start_task("Temporary task")
        self.assertTrue(monitor.complete_task())
        self.assertIsNone(monitor.get_current_task())
        self.assertFalse(monitor.complete_task())

    def test_empty_task_title_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            monitor.start_task("   ")


class QuestionStorageTests(MonitorStorageTestCase):
    def test_question_is_persisted(self) -> None:
        question = monitor.ask_question("Use option A or option B?")
        self.assertEqual(monitor.list_open_questions(), [question])

    def test_questions_are_newest_first(self) -> None:
        first = monitor.ask_question("First question?")
        second = monitor.ask_question("Second question?")
        items = monitor.list_open_questions()

        self.assertEqual(
            [item["question"] for item in items],
            ["Second question?", "First question?"],
        )
        self.assertEqual(items[0]["id"], second["id"])
        self.assertEqual(items[1]["id"], first["id"])

    def test_question_is_trimmed(self) -> None:
        question = monitor.ask_question("  Continue with this benchmark?  ")
        self.assertEqual(question["question"], "Continue with this benchmark?")

    def test_empty_question_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            monitor.ask_question("   ")


class AnswerStorageTests(MonitorStorageTestCase):
    def test_answer_closes_question_and_is_readable(self) -> None:
        question = monitor.ask_question("Use option A or option B?")
        answered = monitor.answer_question(question["id"], "Use option A.")

        self.assertEqual(monitor.list_open_questions(), [])
        answers = monitor.list_answered_questions()
        self.assertEqual(len(answers), 1)
        self.assertEqual(answers[0]["id"], question["id"])
        self.assertEqual(answers[0]["question"], question["question"])
        self.assertEqual(answers[0]["answer"], "Use option A.")
        self.assertEqual(answers[0]["answered_at"], answered["answered_at"])

    def test_answer_is_trimmed(self) -> None:
        question = monitor.ask_question("Continue?")
        answered = monitor.answer_question(question["id"], "  Yes, continue.  ")
        self.assertEqual(answered["answer"], "Yes, continue.")

    def test_empty_answer_is_rejected(self) -> None:
        question = monitor.ask_question("Continue?")
        with self.assertRaises(ValueError):
            monitor.answer_question(question["id"], "   ")
        self.assertEqual(len(monitor.list_open_questions()), 1)

    def test_unknown_question_is_rejected(self) -> None:
        with self.assertRaises(monitor.QuestionNotFoundError):
            monitor.answer_question(999, "Answer")

    def test_answered_question_cannot_be_answered_again(self) -> None:
        question = monitor.ask_question("Continue?")
        monitor.answer_question(question["id"], "First answer")
        with self.assertRaises(monitor.QuestionClosedError):
            monitor.answer_question(question["id"], "Second answer")

    def test_phase4_database_is_migrated_for_answers(self) -> None:
        monitor.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(monitor.DB_PATH)) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE questions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        question TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'open',
                        created_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO questions (question, status, created_at)
                    VALUES ('Existing question?', 'open', '2026-09-25T00:00:00Z')
                    """
                )

        with monitor.database_session() as connection:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(questions)").fetchall()
            }

        self.assertIn("answer", columns)
        self.assertIn("answered_at", columns)

        answered = monitor.answer_question(1, "Migrated answer")
        self.assertEqual(answered["answer"], "Migrated answer")


class ArtifactStorageTests(MonitorStorageTestCase):
    def test_artifact_snapshot_is_immutable_and_hashed(self) -> None:
        source = self.root / "report.txt"
        source.write_text("original report", encoding="utf-8")

        artifact = monitor.register_artifact(
            source,
            "  Benchmark report  ",
            allowed_root=self.root,
        )
        record = monitor.get_artifact_record(artifact["id"])
        self.assertIsNotNone(record)

        snapshot = monitor.artifact_dir() / record["storage_name"]
        self.assertEqual(snapshot.read_text(encoding="utf-8"), "original report")
        self.assertEqual(artifact["display_name"], "Benchmark report")
        self.assertEqual(artifact["original_name"], "report.txt")
        self.assertEqual(artifact["size_bytes"], len(b"original report"))
        self.assertEqual(
            artifact["sha256"],
            hashlib.sha256(b"original report").hexdigest(),
        )
        self.assertEqual(artifact["url"], f"/artifacts/{artifact['id']}")

        source.write_text("changed later", encoding="utf-8")
        self.assertEqual(snapshot.read_text(encoding="utf-8"), "original report")

    def test_latest_artifact_is_newest(self) -> None:
        first_path = self.root / "first.txt"
        second_path = self.root / "second.txt"
        first_path.write_text("first", encoding="utf-8")
        second_path.write_text("second", encoding="utf-8")

        monitor.register_artifact(first_path, allowed_root=self.root)
        second = monitor.register_artifact(second_path, allowed_root=self.root)

        self.assertEqual(monitor.get_latest_artifact(), second)

    def test_artifact_outside_allowed_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as other_dir:
            outside = Path(other_dir) / "secret.txt"
            outside.write_text("secret", encoding="utf-8")
            with self.assertRaises(ValueError):
                monitor.register_artifact(outside, allowed_root=self.root)

    def test_artifact_directory_is_rejected(self) -> None:
        directory = self.root / "folder"
        directory.mkdir()
        with self.assertRaises(ValueError):
            monitor.register_artifact(directory, allowed_root=self.root)

    def test_missing_artifact_record_returns_none(self) -> None:
        self.assertIsNone(monitor.get_artifact_record(999))
        self.assertIsNone(monitor.get_latest_artifact())


if __name__ == "__main__":
    unittest.main()
