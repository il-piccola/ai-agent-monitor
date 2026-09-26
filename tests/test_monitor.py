import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
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

    def test_tailscale_proxy_backend_ports_are_reserved(self) -> None:
        status = {
            "Web": {
                "host.example.ts.net:9443": {
                    "Handlers": {
                        "/": {"Proxy": "http://127.0.0.1:8765"},
                        "/api": {"Proxy": "http://localhost:8766"},
                    }
                }
            }
        }

        self.assertEqual(monitor._tailscale_proxy_ports(status), {8765, 8766})

    def test_find_free_backend_port_skips_existing_serve_proxy_targets(self) -> None:
        status = {
            "Web": {
                "host.example.ts.net:9443": {
                    "Handlers": {
                        "/": {"Proxy": "http://127.0.0.1:8765"},
                    }
                }
            }
        }

        with patch.object(monitor, "_port_is_free", return_value=True):
            self.assertEqual(monitor._find_free_backend_port(status), 8766)

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
    def setUp(self) -> None:
        super().setUp()
        self.startup_dir = self.root / "Startup"

    def test_startup_launcher_name_is_project_specific(self) -> None:
        with patch.object(monitor, "PROJECT_ROOT", self.root):
            first = monitor.startup_launcher_name()

        other = self.root / "other"
        with patch.object(monitor, "PROJECT_ROOT", other):
            second = monitor.startup_launcher_name()

        self.assertNotEqual(first, second)
        self.assertTrue(first.endswith(".cmd"))

    def test_powershell_single_quote_escapes_project_paths(self) -> None:
        self.assertEqual(
            monitor._powershell_single_quote("C:/Users/O'Brien/project"),
            "C:/Users/O''Brien/project",
        )

    def test_startup_install_creates_launcher_and_state(self) -> None:
        with (
            patch.object(monitor.sys, "platform", "win32"),
            patch.object(monitor, "windows_startup_dir", return_value=self.startup_dir),
        ):
            state = monitor.startup_install()

        launcher_path = Path(state["launcher_path"])
        script_path = monitor.startup_script_path()

        self.assertTrue(launcher_path.is_file())
        self.assertTrue(script_path.is_file())
        self.assertEqual(state["mode"], "startup-folder")
        self.assertIn("-m ai_agent_monitor remote start", script_path.read_text(encoding="utf-8"))
        self.assertIn("powershell.exe", launcher_path.read_text(encoding="utf-8"))
        self.assertEqual(monitor.read_startup_state(), state)

    def test_startup_install_cli_reports_launcher_path(self) -> None:
        launcher_path = r"C:\Users\test\Startup\AI Agent Monitor abc123.cmd"
        output = io.StringIO()

        with (
            patch.object(monitor.sys, "argv", ["monitor", "startup", "install"]),
            patch.object(
                monitor,
                "startup_install",
                return_value={"launcher_path": launcher_path},
            ),
            redirect_stdout(output),
        ):
            monitor.main()

        self.assertIn(launcher_path, output.getvalue())

    def test_startup_status_reports_missing_launcher(self) -> None:
        with (
            patch.object(monitor.sys, "platform", "win32"),
            patch.object(monitor, "windows_startup_dir", return_value=self.startup_dir),
        ):
            status = monitor.startup_status()

        self.assertFalse(status["installed"])

    def test_startup_remove_refuses_state_from_other_project(self) -> None:
        monitor._write_startup_state(
            {
                "project_root": "C:/different-project",
                "project_id": "different",
                "mode": "startup-folder",
                "launcher_path": "C:/different-launcher.cmd",
            }
        )

        with (
            patch.object(monitor.sys, "platform", "win32"),
            patch.object(monitor, "windows_startup_dir", return_value=self.startup_dir),
        ):
            with self.assertRaises(RuntimeError):
                monitor.startup_remove()

    def test_startup_remove_refuses_mismatched_launcher(self) -> None:
        monitor._write_startup_state(
            {
                "project_root": str(monitor.PROJECT_ROOT),
                "project_id": monitor.project_id(),
                "mode": "startup-folder",
                "launcher_path": "C:/different-launcher.cmd",
            }
        )

        with (
            patch.object(monitor.sys, "platform", "win32"),
            patch.object(monitor, "windows_startup_dir", return_value=self.startup_dir),
        ):
            with self.assertRaises(RuntimeError):
                monitor.startup_remove()

    def test_startup_remove_deletes_only_expected_launcher(self) -> None:
        with (
            patch.object(monitor.sys, "platform", "win32"),
            patch.object(monitor, "windows_startup_dir", return_value=self.startup_dir),
        ):
            state = monitor.startup_install()
            launcher_path = Path(state["launcher_path"])
            result = monitor.startup_remove()

        self.assertTrue(result["removed"])
        self.assertFalse(launcher_path.exists())
        self.assertFalse(monitor.startup_state_path().exists())
        self.assertFalse(monitor.startup_script_path().exists())

    def test_startup_is_rejected_outside_windows(self) -> None:
        with patch.object(monitor.sys, "platform", "linux"):
            with self.assertRaises(RuntimeError):
                monitor.startup_status()


class AgentStatusTests(MonitorStorageTestCase):
    def test_empty_status_has_exact_schema_without_creating_database(self) -> None:
        with patch.object(monitor, "PROJECT_ROOT", self.root):
            status = monitor.status_snapshot()
            expected_project_id = monitor.project_id()

        self.assertEqual(
            set(status),
            {
                "schema_version",
                "project",
                "current_task",
                "recent_progress",
                "open_questions",
                "recent_answers",
                "latest_artifact",
                "metrics",
            },
        )
        self.assertEqual(status["schema_version"], 1)
        self.assertEqual(status["project"]["name"], self.root.name)
        self.assertEqual(status["project"]["project_id"], expected_project_id)
        self.assertIsNone(status["current_task"])
        self.assertEqual(status["recent_progress"], [])
        self.assertEqual(status["open_questions"], [])
        self.assertEqual(status["recent_answers"], [])
        self.assertIsNone(status["latest_artifact"])
        self.assertEqual(status["metrics"], [])
        self.assertFalse(monitor.DB_PATH.exists())

    def test_partial_status_reports_only_existing_state(self) -> None:
        monitor.set_metric("build.status", "passing", label="Build")

        with patch.object(monitor, "PROJECT_ROOT", self.root):
            status = monitor.status_snapshot()

        self.assertIsNone(status["current_task"])
        self.assertEqual(status["recent_progress"], [])
        self.assertEqual(status["open_questions"], [])
        self.assertEqual(status["recent_answers"], [])
        self.assertIsNone(status["latest_artifact"])
        self.assertEqual(status["metrics"][0]["key"], "build.status")

    def test_populated_status_is_bounded_but_keeps_all_open_questions(self) -> None:
        monitor.start_task("Phase 11 status test")
        source = self.root / "result.txt"
        source.write_text("review me", encoding="utf-8")
        artifact = monitor.register_artifact(source, allowed_root=self.root)
        monitor.set_metric("tests", "61", label="Tests")

        with monitor.database_session() as connection:
            for index in range(12):
                connection.execute(
                    "INSERT INTO progress (message, created_at) VALUES (?, ?)",
                    (f"progress-{index}", f"2026-09-25T00:00:{index:02d}Z"),
                )
            for index in range(12):
                connection.execute(
                    """
                    INSERT INTO questions (
                        question, status, created_at, answer, answered_at
                    )
                    VALUES (?, 'answered', ?, ?, ?)
                    """,
                    (
                        f"answered-{index}?",
                        f"2026-09-25T00:01:{index:02d}Z",
                        f"answer-{index}",
                        f"2026-09-25T00:02:{index:02d}Z",
                    ),
                )
            for index in range(55):
                connection.execute(
                    """
                    INSERT INTO questions (question, status, created_at)
                    VALUES (?, 'open', ?)
                    """,
                    (f"open-{index}?", f"2026-09-25T00:03:{index:02d}Z"),
                )

        with patch.object(monitor, "PROJECT_ROOT", self.root):
            status = monitor.status_snapshot()

        self.assertEqual(status["current_task"]["title"], "Phase 11 status test")
        self.assertEqual(len(status["recent_progress"]), monitor.STATUS_PROGRESS_LIMIT)
        self.assertEqual(status["recent_progress"][0]["message"], "progress-11")
        self.assertEqual(len(status["recent_answers"]), monitor.STATUS_ANSWER_LIMIT)
        self.assertEqual(status["recent_answers"][0]["answer"], "answer-11")
        self.assertEqual(len(status["open_questions"]), 55)
        self.assertEqual(status["open_questions"][0]["question"], "open-54?")
        self.assertEqual(status["latest_artifact"]["id"], artifact["id"])
        self.assertEqual(status["metrics"][0]["key"], "tests")

    def test_status_read_does_not_change_question_or_task_state(self) -> None:
        task = monitor.start_task("Keep this task")
        open_question = monitor.ask_question("Still open?")
        answered_question = monitor.ask_question("Answered?")
        monitor.answer_question(answered_question["id"], "Yes")

        before_open = monitor.list_open_questions(None)
        before_answers = monitor.list_answered_questions()
        before_task = monitor.get_current_task()

        monitor.status_snapshot()

        self.assertEqual(monitor.list_open_questions(None), before_open)
        self.assertEqual(monitor.list_answered_questions(), before_answers)
        self.assertEqual(monitor.get_current_task(), before_task)
        self.assertEqual(before_open[0]["id"], open_question["id"])
        self.assertEqual(before_task, task)

    def test_status_cli_outputs_json_without_extra_text(self) -> None:
        monitor.record_progress("CLI status")
        output = io.StringIO()

        with (
            patch.object(monitor, "PROJECT_ROOT", self.root),
            patch.object(sys, "argv", ["monitor", "status"]),
            redirect_stdout(output),
        ):
            monitor.main()

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["recent_progress"][0]["message"], "CLI status")


class AgentOnboardingTests(MonitorStorageTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.project_root_patch = patch.object(monitor, "PROJECT_ROOT", self.root)
        self.project_root_patch.start()

    def tearDown(self) -> None:
        self.project_root_patch.stop()
        super().tearDown()

    def test_codex_install_preserves_existing_agents_content(self) -> None:
        agents = self.root / "AGENTS.md"
        agents.write_text("# Existing instructions\n\nKeep this text.\n", encoding="utf-8")

        result = monitor.install_agent_integration("codex")

        content = agents.read_text(encoding="utf-8")
        self.assertIn("# Existing instructions", content)
        self.assertIn("Keep this text.", content)
        self.assertEqual(content.count(monitor.AGENTS_BLOCK_START), 1)
        self.assertEqual(content.count(monitor.AGENTS_BLOCK_END), 1)
        self.assertTrue(Path(result["contract"]).is_file())
        self.assertTrue(Path(result["skill"]).is_file())

    def test_codex_install_is_idempotent_and_updates_managed_files(self) -> None:
        monitor.install_agent_integration("codex")
        monitor.agent_contract_target().write_text("old contract", encoding="utf-8")
        monitor.codex_skill_target().write_text("old skill", encoding="utf-8")

        monitor.install_agent_integration("codex")

        agents = monitor.agents_file_path().read_text(encoding="utf-8")
        self.assertEqual(agents.count(monitor.AGENTS_BLOCK_START), 1)
        self.assertEqual(
            monitor.agent_contract_target().read_text(encoding="utf-8"),
            monitor.AGENT_CONTRACT_PATH.read_text(encoding="utf-8"),
        )
        self.assertEqual(
            monitor.codex_skill_target().read_text(encoding="utf-8"),
            monitor.CODEX_SKILL_PATH.read_text(encoding="utf-8"),
        )

    def test_codex_install_refuses_invalid_markers_before_writing_files(self) -> None:
        monitor.agents_file_path().write_text(
            monitor.AGENTS_BLOCK_START + "\nmissing end\n",
            encoding="utf-8",
        )

        with self.assertRaises(RuntimeError):
            monitor.install_agent_integration("codex")

        self.assertFalse(monitor.agent_contract_target().exists())
        self.assertFalse(monitor.codex_skill_target().exists())

    def test_codex_remove_preserves_unrelated_agents_content(self) -> None:
        agents = self.root / "AGENTS.md"
        agents.write_text("# Existing\n", encoding="utf-8")
        monitor.install_agent_integration("codex")

        result = monitor.remove_agent_integration("codex")

        self.assertIn(str(agents), result["removed"])
        self.assertEqual(agents.read_text(encoding="utf-8"), "# Existing\n")
        self.assertFalse(monitor.agent_contract_target().exists())
        self.assertFalse(monitor.codex_skill_target().exists())

    def test_codex_remove_refuses_modified_skill_without_partial_removal(self) -> None:
        monitor.install_agent_integration("codex")
        monitor.codex_skill_target().write_text("user edit", encoding="utf-8")
        agents_before = monitor.agents_file_path().read_text(encoding="utf-8")

        with self.assertRaises(RuntimeError):
            monitor.remove_agent_integration("codex")

        self.assertEqual(
            monitor.agents_file_path().read_text(encoding="utf-8"),
            agents_before,
        )
        self.assertTrue(monitor.agent_contract_target().exists())
        self.assertTrue(monitor.codex_skill_target().exists())

    def test_generic_install_and_remove_do_not_create_codex_files(self) -> None:
        monitor.install_agent_integration("generic")

        self.assertTrue(monitor.agent_contract_target().is_file())
        self.assertFalse(monitor.agents_file_path().exists())
        self.assertFalse(monitor.codex_skill_target().exists())

        monitor.remove_agent_integration("generic")
        self.assertFalse(monitor.agent_contract_target().exists())

    def test_emit_generic_returns_agent_neutral_contract(self) -> None:
        emitted = monitor.emit_agent_integration("generic")
        self.assertEqual(
            emitted,
            monitor.AGENT_CONTRACT_PATH.read_text(encoding="utf-8"),
        )

    def test_emit_codex_contains_managed_block_and_skill(self) -> None:
        emitted = monitor.emit_agent_integration("codex")
        self.assertIn(monitor.AGENTS_BLOCK_START, emitted)
        self.assertIn("name: ai-agent-monitor", emitted)

    def test_codex_skill_documents_windows_cli_path_fallback(self) -> None:
        skill = monitor.CODEX_SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("uv tool dir --bin", skill)
        self.assertIn("monitor.exe", skill)

    def test_agent_contract_documents_windows_cli_path_fallback(self) -> None:
        contract = monitor.AGENT_CONTRACT_PATH.read_text(encoding="utf-8")
        self.assertIn("uv tool dir --bin", contract)
        self.assertIn("monitor.exe", contract)

    def test_bundled_contract_matches_repository_contract(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        self.assertEqual(
            monitor.AGENT_CONTRACT_PATH.read_bytes(),
            (repo_root / "AGENT_INTEGRATION.md").read_bytes(),
        )


class NotificationOutboxTests(MonitorStorageTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.project_root_patch = patch.object(monitor, "PROJECT_ROOT", self.root)
        self.project_root_patch.start()

    def tearDown(self) -> None:
        self.project_root_patch.stop()
        super().tearDown()

    def test_question_creation_enqueues_one_durable_notification(self) -> None:
        question = monitor.ask_question("Which option should I use?")

        events = monitor.list_notification_outbox()

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["event_type"], "question.created")
        self.assertEqual(event["entity_type"], "question")
        self.assertEqual(event["entity_id"], question["id"])
        self.assertEqual(event["status"], "pending")
        self.assertEqual(event["attempt_count"], 0)
        self.assertEqual(event["payload"]["question"], question["question"])
        self.assertEqual(event["payload"]["project"]["project_id"], monitor.project_id())

    def test_question_and_notification_are_one_transaction(self) -> None:
        with patch.object(
            monitor,
            "_enqueue_notification_event",
            side_effect=RuntimeError("outbox unavailable"),
        ):
            with self.assertRaises(RuntimeError):
                monitor.ask_question("Should roll back?")

        self.assertEqual(monitor.list_open_questions(), [])
        self.assertEqual(monitor.list_notification_outbox(), [])

    def test_logical_question_event_is_unique(self) -> None:
        question = monitor.ask_question("Only once?")
        event = monitor.list_notification_outbox()[0]

        with monitor.database_session() as connection:
            duplicate_id = monitor._enqueue_notification_event(
                connection,
                event_type="question.created",
                entity_type="question",
                entity_id=question["id"],
                payload=event["payload"],
                created_at=question["created_at"],
            )

        self.assertEqual(duplicate_id, event["id"])
        self.assertEqual(len(monitor.list_notification_outbox()), 1)

    def test_failed_delivery_stays_pending_and_records_error(self) -> None:
        monitor.ask_question("Retry me")
        event = monitor.list_notification_outbox()[0]

        result = monitor.record_notification_attempt(
            event["id"],
            delivered=False,
            error="network unavailable",
        )

        self.assertEqual(result["status"], "pending")
        stored = monitor.list_notification_outbox(status="pending")[0]
        self.assertEqual(stored["attempt_count"], 1)
        self.assertEqual(stored["last_error"], "network unavailable")
        self.assertIsNotNone(stored["last_attempt_at"])
        self.assertIsNone(stored["delivered_at"])

    def test_successful_delivery_is_recorded_and_not_recounted(self) -> None:
        monitor.ask_question("Deliver me")
        event = monitor.list_notification_outbox()[0]

        first = monitor.record_notification_attempt(event["id"], delivered=True)
        second = monitor.record_notification_attempt(event["id"], delivered=True)

        self.assertEqual(first["status"], "delivered")
        self.assertEqual(second["status"], "delivered")
        stored = monitor.list_notification_outbox(status="delivered")[0]
        self.assertEqual(stored["attempt_count"], 1)
        self.assertIsNotNone(stored["delivered_at"])
        self.assertIsNone(stored["last_error"])

    def test_pending_event_survives_new_database_connection(self) -> None:
        monitor.ask_question("Persist me")

        first = monitor.list_notification_outbox(status="pending")
        second = monitor.list_notification_outbox(status="pending")

        self.assertEqual(first, second)
        self.assertEqual(len(second), 1)

    def test_notifications_cli_outputs_json_and_filters_status(self) -> None:
        monitor.ask_question("CLI event")
        event = monitor.list_notification_outbox()[0]
        monitor.record_notification_attempt(event["id"], delivered=True)
        output = io.StringIO()

        with (
            patch.object(sys, "argv", ["monitor", "notifications", "--status", "delivered"]),
            redirect_stdout(output),
        ):
            monitor.main()

        payload = json.loads(output.getvalue())
        self.assertEqual(len(payload["notifications"]), 1)
        self.assertEqual(payload["notifications"][0]["status"], "delivered")


class DoctorTests(MonitorStorageTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.project_root_patch = patch.object(monitor, "PROJECT_ROOT", self.root)
        self.project_root_patch.start()

    def tearDown(self) -> None:
        self.project_root_patch.stop()
        super().tearDown()

    def _check(self, report: dict[str, object], name: str) -> dict[str, object]:
        return next(check for check in report["checks"] if check["name"] == name)

    def test_doctor_on_uninitialized_project_is_ok_and_does_not_create_database(self) -> None:
        report = monitor.doctor_snapshot()

        self.assertEqual(report["overall"], "ok")
        self.assertEqual(self._check(report, "database")["status"], "ok")
        self.assertFalse(monitor.DB_PATH.exists())

    def test_doctor_reads_healthy_database_without_changing_it(self) -> None:
        monitor.record_progress("doctor test")
        before = monitor.DB_PATH.read_bytes()

        report = monitor.doctor_snapshot()

        self.assertEqual(self._check(report, "database")["status"], "ok")
        self.assertEqual(monitor.DB_PATH.read_bytes(), before)

    def test_doctor_reports_corrupt_database(self) -> None:
        monitor.DATA_DIR.mkdir(parents=True, exist_ok=True)
        monitor.DB_PATH.write_bytes(b"not a sqlite database")

        report = monitor.doctor_snapshot()

        self.assertEqual(report["overall"], "error")
        self.assertEqual(self._check(report, "database")["status"], "error")

    def test_doctor_reports_incomplete_database_schema(self) -> None:
        monitor.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(monitor.DB_PATH)) as connection:
            connection.execute(
                "CREATE TABLE progress (id INTEGER PRIMARY KEY, message TEXT, created_at TEXT)"
            )
            connection.commit()

        report = monitor.doctor_snapshot()
        database = self._check(report, "database")

        self.assertEqual(database["status"], "error")
        self.assertIn("current_task", database["details"]["missing_tables"])

    def test_doctor_reports_invalid_agent_markers(self) -> None:
        monitor.agents_file_path().write_text(
            monitor.AGENTS_BLOCK_START + "\nmissing end\n",
            encoding="utf-8",
        )

        report = monitor.doctor_snapshot()

        self.assertEqual(report["overall"], "error")
        self.assertEqual(self._check(report, "agent_integration")["status"], "error")

    def test_doctor_warns_when_generated_contract_was_modified(self) -> None:
        monitor.agent_contract_target().write_text("modified", encoding="utf-8")

        report = monitor.doctor_snapshot()

        self.assertEqual(report["overall"], "warning")
        self.assertEqual(self._check(report, "agent_integration")["status"], "warning")

    def test_doctor_reports_stale_remote_pid_without_changing_state(self) -> None:
        state = {
            "project_root": str(self.root),
            "backend_port": 8766,
            "https_port": 9444,
            "pid": 1234,
            "backend_url": "http://127.0.0.1:8766",
            "tailnet_url": "https://host.example.ts.net:9444/",
        }
        monitor._write_remote_state(state)
        before = monitor.remote_state_path().read_text(encoding="utf-8")

        with (
            patch.object(monitor, "_process_is_alive", return_value=False),
            patch.object(monitor, "_tailscale_status_json", return_value={}),
        ):
            report = monitor.doctor_snapshot()

        self.assertEqual(self._check(report, "remote")["status"], "error")
        self.assertEqual(
            monitor.remote_state_path().read_text(encoding="utf-8"),
            before,
        )

    def test_doctor_accepts_matching_remote_backend_and_serve_mapping(self) -> None:
        backend_url = "http://127.0.0.1:8766"
        monitor._write_remote_state(
            {
                "project_root": str(self.root),
                "backend_port": 8766,
                "https_port": 9444,
                "pid": 1234,
                "backend_url": backend_url,
                "tailnet_url": "https://host.example.ts.net:9444/",
            }
        )
        serve_status = {
            "Web": {
                "host.example.ts.net:9444": {
                    "Handlers": {"/": {"Proxy": backend_url}}
                }
            }
        }

        with (
            patch.object(monitor, "_process_is_alive", return_value=True),
            patch.object(monitor, "_project_server_matches", return_value=True),
            patch.object(monitor, "_tailscale_status_json", return_value=serve_status),
        ):
            report = monitor.doctor_snapshot()

        self.assertEqual(self._check(report, "remote")["status"], "ok")

    def test_doctor_reports_missing_startup_files(self) -> None:
        monitor._write_startup_state(
            {
                "project_root": str(self.root),
                "project_id": monitor.project_id(),
                "mode": "startup-folder",
                "launcher_path": str(self.root / "missing.cmd"),
                "script_path": str(self.root / "missing.ps1"),
            }
        )

        report = monitor.doctor_snapshot()

        self.assertEqual(self._check(report, "startup")["status"], "error")

    def test_doctor_warns_about_large_logs_and_temporary_runtime_files(self) -> None:
        runtime = monitor.remote_runtime_dir()
        runtime.mkdir(parents=True, exist_ok=True)
        with (runtime / "remote.stdout.log").open("wb") as handle:
            handle.truncate(monitor.DOCTOR_LOG_WARNING_BYTES + 1)
        (runtime / "remote.json.tmp").write_text("{}", encoding="utf-8")

        report = monitor.doctor_snapshot()
        runtime_check = self._check(report, "runtime")

        self.assertEqual(report["overall"], "warning")
        self.assertEqual(runtime_check["status"], "warning")
        self.assertIn("remote.json.tmp", runtime_check["details"]["temporary_files"])

    def test_doctor_json_cli_prints_machine_readable_report(self) -> None:
        output = io.StringIO()

        with (
            patch.object(sys, "argv", ["monitor", "doctor", "--json"]),
            redirect_stdout(output),
        ):
            monitor.main()

        report = json.loads(output.getvalue())
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["overall"], "ok")

    def test_doctor_cli_exits_nonzero_on_error(self) -> None:
        monitor.DATA_DIR.mkdir(parents=True, exist_ok=True)
        monitor.DB_PATH.write_bytes(b"broken")
        output = io.StringIO()

        with (
            patch.object(sys, "argv", ["monitor", "doctor"]),
            redirect_stdout(output),
        ):
            with self.assertRaises(SystemExit) as raised:
                monitor.main()

        self.assertEqual(raised.exception.code, 1)
        self.assertIn("[ERROR] database", output.getvalue())


if __name__ == "__main__":
    unittest.main()
