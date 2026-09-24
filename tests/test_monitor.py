import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import monitor


class MonitorStorageTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
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

        current = monitor.get_current_task()

        self.assertEqual(current, started)
        self.assertEqual(current["title"], "Build login page")
        self.assertTrue(current["started_at"].endswith("Z"))

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

        items = monitor.list_open_questions()

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0], question)
        self.assertTrue(items[0]["created_at"].endswith("Z"))

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


if __name__ == "__main__":
    unittest.main()
