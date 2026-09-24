import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import monitor


class ProgressStorageTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
