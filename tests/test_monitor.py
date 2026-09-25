import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import ai_agent_monitor.app as monitor


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


class MetricStorageTests(MonitorStorageTestCase):
    def test_metric_can_be_set_and_listed(self) -> None:
        metric = monitor.set_metric(
            "cost.total",
            "90.71",
            label="Total cost",
            unit="USD",
        )

        self.assertEqual(monitor.list_metrics(), [metric])
        self.assertEqual(metric["key"], "cost.total")
        self.assertEqual(metric["label"], "Total cost")
        self.assertEqual(metric["value"], "90.71")
        self.assertEqual(metric["unit"], "USD")
        self.assertTrue(metric["updated_at"].endswith("Z"))

    def test_updating_value_preserves_existing_label_and_unit(self) -> None:
        monitor.set_metric(
            "cost.total",
            "90.71",
            label="Total cost",
            unit="USD",
        )

        updated = monitor.set_metric("cost.total", "91.20")

        self.assertEqual(updated["label"], "Total cost")
        self.assertEqual(updated["unit"], "USD")
        self.assertEqual(updated["value"], "91.20")
        self.assertEqual(len(monitor.list_metrics()), 1)

    def test_empty_unit_clears_existing_unit(self) -> None:
        monitor.set_metric("errors", "2", label="Errors", unit="count")

        updated = monitor.set_metric("errors", "3", unit="")

        self.assertIsNone(updated["unit"])

    def test_default_label_is_metric_key(self) -> None:
        metric = monitor.set_metric("benchmark.score", "0.87")

        self.assertEqual(metric["label"], "benchmark.score")
        self.assertIsNone(metric["unit"])

    def test_metrics_are_sorted_by_key(self) -> None:
        monitor.set_metric("zeta", "2")
        monitor.set_metric("Alpha", "1")

        self.assertEqual(
            [metric["key"] for metric in monitor.list_metrics()],
            ["Alpha", "zeta"],
        )

    def test_metric_can_be_deleted(self) -> None:
        monitor.set_metric("errors", "2")

        self.assertTrue(monitor.delete_metric("errors"))
        self.assertFalse(monitor.delete_metric("errors"))
        self.assertEqual(monitor.list_metrics(), [])

    def test_invalid_metric_input_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            monitor.set_metric("bad key", "1")

        with self.assertRaises(ValueError):
            monitor.set_metric("good.key", "   ")

        with self.assertRaises(ValueError):
            monitor.set_metric("good.key", "1", label="   ")


class ProjectBoundaryTests(MonitorStorageTestCase):
    def test_init_project_creates_runtime_gitignore(self) -> None:
        with patch.object(monitor, "PROJECT_ROOT", self.root):
            result = monitor.init_project(False)

        ignore_path = self.root / ".agent-monitor" / ".gitignore"
        self.assertTrue(ignore_path.is_file())
        self.assertIn("monitor.db", ignore_path.read_text(encoding="utf-8"))
        self.assertEqual(result["data_dir"], str(self.root / ".agent-monitor"))

    def test_init_project_can_copy_project_dashboard(self) -> None:
        bundled = self.root / "bundled.html"
        bundled.write_text("<h1>default</h1>", encoding="utf-8")

        with (
            patch.object(monitor, "PROJECT_ROOT", self.root),
            patch.object(monitor, "DEFAULT_DASHBOARD_PATH", bundled),
        ):
            result = monitor.init_project(True)

        project_dashboard = self.root / ".agent-monitor" / "dashboard.html"
        self.assertTrue(result["dashboard_created"])
        self.assertEqual(
            project_dashboard.read_text(encoding="utf-8"),
            "<h1>default</h1>",
        )

    def test_project_dashboard_overrides_bundled_dashboard(self) -> None:
        bundled = self.root / "bundled.html"
        bundled.write_text("bundled", encoding="utf-8")
        project_dashboard = self.root / ".agent-monitor" / "dashboard.html"
        project_dashboard.parent.mkdir(parents=True, exist_ok=True)
        project_dashboard.write_text("project", encoding="utf-8")

        with patch.object(monitor, "DEFAULT_DASHBOARD_PATH", bundled):
            self.assertEqual(monitor.dashboard_path(), project_dashboard)

    def test_bundled_dashboard_matches_source_dashboard(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        source_dashboard = repo_root / "dashboard.html"

        self.assertEqual(
            monitor.DEFAULT_DASHBOARD_PATH.read_bytes(),
            source_dashboard.read_bytes(),
        )


class InstalledStyleCliIsolationTests(unittest.TestCase):
    def test_module_cli_keeps_two_projects_separate(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            str(repo_root)
            if not existing_pythonpath
            else os.pathsep.join([str(repo_root), existing_pythonpath])
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            project_a = base / "project-a"
            project_b = base / "project-b"
            project_a.mkdir()
            project_b.mkdir()

            subprocess.run(
                [sys.executable, "-m", "ai_agent_monitor", "metric", "set", "project.name", "A"],
                cwd=project_a,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [sys.executable, "-m", "ai_agent_monitor", "metric", "set", "project.name", "B"],
                cwd=project_b,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

            output_a = subprocess.run(
                [sys.executable, "-m", "ai_agent_monitor", "metrics"],
                cwd=project_a,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            output_b = subprocess.run(
                [sys.executable, "-m", "ai_agent_monitor", "metrics"],
                cwd=project_b,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            ).stdout

            metrics_a = json.loads(output_a)["metrics"]
            metrics_b = json.loads(output_b)["metrics"]

            self.assertEqual(metrics_a[0]["value"], "A")
            self.assertEqual(metrics_b[0]["value"], "B")
            self.assertTrue((project_a / ".agent-monitor" / "monitor.db").is_file())
            self.assertTrue((project_b / ".agent-monitor" / "monitor.db").is_file())
            self.assertNotEqual(
                project_a / ".agent-monitor" / "monitor.db",
                project_b / ".agent-monitor" / "monitor.db",
            )


class RemoteAccessTests(MonitorStorageTestCase):
    def test_tailscale_used_ports_reads_tcp_and_web_entries(self) -> None:
        status = {
            "TCP": {"443": {}, "8443": {}},
            "Web": {
                "host.example.ts.net:9443": {},
                "host.example.ts.net:10443": {},
            },
        }

        self.assertEqual(
            monitor._tailscale_used_ports(status),
            {443, 8443, 9443, 10443},
        )

    def test_serve_handler_proxy_reads_root_proxy(self) -> None:
        status = {
            "Web": {
                "host.example.ts.net:9443": {
                    "Handlers": {
                        "/": {"Proxy": "http://127.0.0.1:8765"}
                    }
                }
            }
        }

        self.assertEqual(
            monitor._serve_handler_proxy(
                status,
                "host.example.ts.net",
                9443,
            ),
            "http://127.0.0.1:8765",
        )

    def test_remote_state_round_trip_is_project_local(self) -> None:
        state = {
            "project_root": str(self.root),
            "backend_port": 8766,
            "https_port": 9444,
            "pid": 1234,
            "backend_url": "http://127.0.0.1:8766",
            "tailnet_url": "https://host.example.ts.net:9444/",
        }

        monitor._write_remote_state(state)

        self.assertEqual(monitor.read_remote_state(), state)
        self.assertTrue(
            (self.root / ".agent-monitor" / "runtime" / "remote.json").is_file()
        )

    def test_remote_status_without_state_is_inactive(self) -> None:
        self.assertEqual(
            monitor.remote_status(),
            {
                "configured": False,
                "backend_alive": False,
                "tailscale_active": False,
            },
        )

    def test_remote_stop_refuses_state_from_other_project(self) -> None:
        monitor._write_remote_state(
            {
                "project_root": "C:/different-project",
                "backend_port": 8766,
                "https_port": 9444,
                "pid": 1234,
                "backend_url": "http://127.0.0.1:8766",
                "tailnet_url": "https://host.example.ts.net:9444/",
            }
        )

        with self.assertRaises(RuntimeError):
            monitor.remote_stop()

    def test_remote_stop_refuses_reassigned_tailscale_port(self) -> None:
        monitor._write_remote_state(
            {
                "project_root": str(monitor.PROJECT_ROOT),
                "backend_port": 8766,
                "https_port": 9444,
                "pid": 1234,
                "backend_url": "http://127.0.0.1:8766",
                "tailnet_url": "https://host.example.ts.net:9444/",
            }
        )

        status = {
            "Web": {
                "host.example.ts.net:9444": {
                    "Handlers": {
                        "/": {"Proxy": "http://127.0.0.1:9999"}
                    }
                }
            }
        }

        with (
            patch.object(monitor, "_tailscale_status_json", return_value=status),
            patch.object(monitor, "_tailscale_dns_name", return_value="host.example.ts.net"),
        ):
            with self.assertRaises(RuntimeError):
                monitor.remote_stop()

    def test_find_free_tailscale_port_skips_existing_and_busy_ports(self) -> None:
        status = {
            "Web": {
                "host.example.ts.net:9443": {}
            }
        }

        def fake_port_is_free(host: str, port: int) -> bool:
            return port != 9444

        with patch.object(monitor, "_port_is_free", side_effect=fake_port_is_free):
            self.assertEqual(monitor._find_free_tailscale_port(status), 9445)


class RemoteProjectSecurityTests(MonitorStorageTestCase):
    def test_project_info_does_not_expose_local_paths(self) -> None:
        info = monitor.project_info()

        self.assertIn("project_id", info)
        self.assertIn("name", info)
        self.assertNotIn("project_root", info)
        self.assertNotIn("data_dir", info)

    def test_init_project_updates_existing_gitignore_without_overwriting(self) -> None:
        monitor.DATA_DIR.mkdir(parents=True, exist_ok=True)
        ignore_path = monitor.DATA_DIR / ".gitignore"
        ignore_path.write_text("custom-entry/\nmonitor.db\n", encoding="utf-8")

        monitor.init_project(False)

        lines = ignore_path.read_text(encoding="utf-8").splitlines()
        self.assertIn("custom-entry/", lines)
        self.assertIn("monitor.db", lines)
        self.assertIn("runtime/", lines)
        self.assertEqual(lines.count("monitor.db"), 1)

    def test_remote_start_refuses_unverified_live_process(self) -> None:
        monitor._write_remote_state(
            {
                "project_root": str(monitor.PROJECT_ROOT),
                "backend_port": 8766,
                "https_port": 9444,
                "pid": 1234,
                "backend_url": "http://127.0.0.1:8766",
                "tailnet_url": "https://host.example.ts.net:9444/",
            }
        )

        with (
            patch.object(monitor, "_process_is_alive", return_value=True),
            patch.object(monitor, "_project_server_matches", return_value=False),
        ):
            with self.assertRaises(RuntimeError):
                monitor.remote_start()

    def test_remote_stop_refuses_unverified_live_pid(self) -> None:
        monitor._write_remote_state(
            {
                "project_root": str(monitor.PROJECT_ROOT),
                "backend_port": 8766,
                "https_port": 9444,
                "pid": 1234,
                "backend_url": "http://127.0.0.1:8766",
                "tailnet_url": "https://host.example.ts.net:9444/",
            }
        )

        with (
            patch.object(monitor, "_process_is_alive", return_value=True),
            patch.object(monitor, "_project_server_matches", return_value=False),
        ):
            with self.assertRaises(RuntimeError):
                monitor.remote_stop()


class WindowsStartupTests(MonitorStorageTestCase):
    def test_startup_task_name_is_project_specific(self) -> None:
        with patch.object(monitor, "PROJECT_ROOT", self.root):
            first = monitor.startup_task_name()

        other = self.root / "other"
        with patch.object(monitor, "PROJECT_ROOT", other):
            second = monitor.startup_task_name()

        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("AI Agent Monitor "))

    def test_powershell_single_quote_escapes_project_paths(self) -> None:
        self.assertEqual(
            monitor._powershell_single_quote("C:/Users/O'Brien/project"),
            "C:/Users/O''Brien/project",
        )

    def test_startup_install_creates_onlogon_task_and_state(self) -> None:
        calls = []

        def fake_schtasks(*args: str, check: bool = True):
            calls.append((args, check))
            return subprocess.CompletedProcess(["schtasks"], 0, "", "")

        with (
            patch.object(monitor.sys, "platform", "win32"),
            patch.object(monitor, "_schtasks_command", side_effect=fake_schtasks),
        ):
            state = monitor.startup_install()

        script_path = monitor.startup_script_path()
        self.assertTrue(script_path.is_file())
        script = script_path.read_text(encoding="utf-8")
        self.assertIn("-m ai_agent_monitor remote start", script)
        self.assertIn(str(monitor.PROJECT_ROOT), script)

        create_args = calls[0][0]
        self.assertIn("/Create", create_args)
        self.assertIn("ONLOGON", create_args)
        self.assertIn(monitor.startup_task_name(), create_args)
        self.assertEqual(state["task_name"], monitor.startup_task_name())
        self.assertEqual(monitor.read_startup_state(), state)

    def test_startup_status_reports_missing_task(self) -> None:
        result = subprocess.CompletedProcess(["schtasks"], 1, "", "not found")

        with (
            patch.object(monitor.sys, "platform", "win32"),
            patch.object(monitor, "_schtasks_command", return_value=result),
        ):
            status = monitor.startup_status()

        self.assertFalse(status["installed"])
        self.assertEqual(status["task_name"], monitor.startup_task_name())

    def test_startup_remove_refuses_state_from_other_project(self) -> None:
        monitor._write_startup_state(
            {
                "project_root": "C:/different-project",
                "project_id": "different",
                "task_name": monitor.startup_task_name(),
            }
        )

        with patch.object(monitor.sys, "platform", "win32"):
            with self.assertRaises(RuntimeError):
                monitor.startup_remove()

    def test_startup_remove_deletes_only_expected_task(self) -> None:
        task_name = monitor.startup_task_name()
        monitor.remote_runtime_dir().mkdir(parents=True, exist_ok=True)
        monitor.startup_script_path().write_text("test", encoding="utf-8")
        monitor._write_startup_state(
            {
                "project_root": str(monitor.PROJECT_ROOT),
                "project_id": monitor.project_id(),
                "task_name": task_name,
            }
        )

        calls = []

        def fake_schtasks(*args: str, check: bool = True):
            calls.append((args, check))
            return subprocess.CompletedProcess(["schtasks"], 0, "", "")

        with (
            patch.object(monitor.sys, "platform", "win32"),
            patch.object(monitor, "_schtasks_command", side_effect=fake_schtasks),
        ):
            result = monitor.startup_remove()

        self.assertTrue(result["removed"])
        self.assertEqual(calls[0][0][0], "/Query")
        self.assertEqual(calls[1][0][0], "/Delete")
        self.assertIn(task_name, calls[1][0])
        self.assertFalse(monitor.startup_state_path().exists())
        self.assertFalse(monitor.startup_script_path().exists())

    def test_startup_is_rejected_outside_windows(self) -> None:
        with patch.object(monitor.sys, "platform", "linux"):
            with self.assertRaises(RuntimeError):
                monitor.startup_status()


if __name__ == "__main__":
    unittest.main()
