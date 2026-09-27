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
from unittest.mock import MagicMock, patch

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
    def test_progress_cli_accepts_japanese_display_text(self) -> None:
        args = monitor.parse_progress_args(["--ja", "日本語の進捗", "Original progress"])
        self.assertEqual(args.action, "record")
        self.assertEqual(args.ja, "日本語の進捗")
        self.assertEqual(args.message, ["Original progress"])

    def test_translate_cli_targets_task_and_question(self) -> None:
        task = monitor.parse_translate_args(["task", "現在のタスク"])
        question = monitor.parse_translate_args(["question", "3", "日本語の質問"])
        self.assertEqual(task.entity_type, "task")
        self.assertEqual(task.translation, ["現在のタスク"])
        self.assertEqual(question.entity_type, "question")
        self.assertEqual(question.id, 3)

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

    def test_japanese_display_text_preserves_original_progress(self) -> None:
        event = monitor.record_progress("Original English", "最初の日本語訳")
        monitor.set_progress_translation(event["id"], "更新した日本語訳")

        item = monitor.list_progress()[0]
        self.assertEqual(item["message"], "Original English")
        self.assertEqual(item["message_ja"], "更新した日本語訳")
        with self.assertRaises(ValueError):
            monitor.set_progress_translation(event["id"], "  ")
        with self.assertRaises(ValueError):
            monitor.set_progress_translation(event["id"] + 1, "存在しない記録")

    def test_existing_progress_database_gains_translation_column(self) -> None:
        self.root.joinpath(".agent-monitor").mkdir()
        with closing(sqlite3.connect(self.root / ".agent-monitor" / "monitor.db")) as connection:
            with connection:
                connection.execute(
                    "CREATE TABLE progress (id INTEGER PRIMARY KEY, message TEXT NOT NULL, created_at TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO progress VALUES (1, 'Legacy record', '2026-09-27T00:00:00Z')"
                )

        item = monitor.list_progress()[0]
        self.assertEqual(item["message"], "Legacy record")
        self.assertIsNone(item["message_ja"])
        monitor.set_progress_translation(1, "以前の記録")
        self.assertEqual(monitor.list_progress()[0]["message_ja"], "以前の記録")


class CurrentTaskStorageTests(MonitorStorageTestCase):
    def test_task_translation_preserves_title_and_does_not_follow_replacement(self) -> None:
        monitor.start_task("Original task")
        monitor.set_display_translation("task", 1, "元のタスク")
        self.assertEqual(monitor.get_current_task()["title"], "Original task")
        self.assertEqual(monitor.get_current_task()["title_ja"], "元のタスク")
        monitor.start_task("Different task")
        self.assertIsNone(monitor.get_current_task()["title_ja"])

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
    def test_question_translation_preserves_original(self) -> None:
        item = monitor.ask_question("Original question?")
        monitor.set_display_translation("question", item["id"], "元の質問ですか？")
        self.assertEqual(monitor.list_open_questions()[0]["question"], "Original question?")
        self.assertEqual(monitor.list_open_questions()[0]["question_ja"], "元の質問ですか？")

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
    def test_artifact_translation_preserves_name_and_snapshot(self) -> None:
        source = self.root / "report.txt"
        source.write_text("original report", encoding="utf-8")
        artifact = monitor.register_artifact(source, "Original report", allowed_root=self.root)
        monitor.set_display_translation("artifact", artifact["id"], "元の報告書")
        displayed = monitor.get_latest_artifact()
        self.assertEqual(displayed["display_name"], "Original report")
        self.assertEqual(displayed["display_name_ja"], "元の報告書")
        self.assertEqual(
            (monitor.artifact_dir() / monitor.get_artifact_record(artifact["id"])["storage_name"]).read_text(encoding="utf-8"),
            "original report",
        )

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

    def test_bundled_dashboard_uses_escaped_project_name(self) -> None:
        project = self.root / "repo & sample"
        project.mkdir()
        with patch.object(monitor, "PROJECT_ROOT", project):
            content = monitor.rendered_dashboard_html().decode("utf-8")

        self.assertIn("<title>repo &amp; sample</title>", content)
        self.assertIn(
            '<h1 id="monitor-project-heading">repo &amp; sample</h1>',
            content,
        )

    def test_custom_dashboard_keeps_its_heading_but_uses_project_title(self) -> None:
        custom = self.root / ".agent-monitor" / "dashboard.html"
        custom.parent.mkdir(parents=True, exist_ok=True)
        original = "<html><head><title>Custom title</title></head><body><h1>Custom heading</h1></body></html>"
        custom.write_text(original, encoding="utf-8")

        with patch.object(monitor, "PROJECT_ROOT", self.root):
            content = monitor.rendered_dashboard_html().decode("utf-8")

        self.assertIn(f"<title>{self.root.name}</title>", content)
        self.assertIn("<h1>Custom heading</h1>", content)
        self.assertEqual(custom.read_text(encoding="utf-8"), original)


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
        self.notification_env_patch = patch.dict(
            os.environ,
            {monitor.TELEGRAM_TOKEN_ENV: "test-token"},
        )
        self.notification_env_patch.start()
        monitor.configure_telegram("123456")
        monitor.set_notification_channel_enabled("telegram", True)

    def tearDown(self) -> None:
        self.notification_env_patch.stop()
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
        deliveries = monitor.list_notification_deliveries(notification_id=event["id"])
        self.assertEqual(len(deliveries), 1)
        self.assertEqual(deliveries[0]["channel"], "telegram")
        self.assertEqual(deliveries[0]["status"], "pending")

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
        delivery = monitor.list_notification_deliveries(notification_id=event["id"])[0]

        result = monitor.record_notification_delivery_attempt(
            delivery["id"],
            delivered=False,
            error="network unavailable",
        )

        self.assertEqual(result["status"], "pending")
        stored = monitor.list_notification_outbox(status="pending")[0]
        stored_delivery = monitor.list_notification_deliveries(
            notification_id=event["id"]
        )[0]
        self.assertEqual(stored["attempt_count"], 1)
        self.assertIn("network unavailable", stored["last_error"])
        self.assertEqual(stored_delivery["last_error"], "network unavailable")
        self.assertIsNotNone(stored["last_attempt_at"])
        self.assertIsNone(stored["delivered_at"])

    def test_successful_delivery_is_recorded_and_not_recounted(self) -> None:
        monitor.ask_question("Deliver me")
        event = monitor.list_notification_outbox()[0]
        delivery = monitor.list_notification_deliveries(notification_id=event["id"])[0]

        first = monitor.record_notification_delivery_attempt(
            delivery["id"], delivered=True
        )
        second = monitor.record_notification_delivery_attempt(
            delivery["id"], delivered=True
        )

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

    def test_answer_cancels_pending_question_notification(self) -> None:
        question = monitor.ask_question("Answer before delivery?")

        monitor.answer_question(question["id"], "Yes")

        self.assertEqual(monitor.list_notification_outbox(status="pending"), [])
        cancelled = monitor.list_notification_outbox(status="cancelled")
        self.assertEqual(len(cancelled), 1)
        self.assertEqual(cancelled[0]["entity_id"], question["id"])
        self.assertIsNotNone(cancelled[0]["cancelled_at"])
        self.assertEqual(cancelled[0]["attempt_count"], 0)

    def test_answer_does_not_cancel_already_delivered_notification(self) -> None:
        question = monitor.ask_question("Already delivered?")
        event = monitor.list_notification_outbox()[0]
        delivery = monitor.list_notification_deliveries(notification_id=event["id"])[0]
        monitor.record_notification_delivery_attempt(delivery["id"], delivered=True)

        monitor.answer_question(question["id"], "Yes")

        delivered = monitor.list_notification_outbox(status="delivered")
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0]["entity_id"], question["id"])
        self.assertIsNone(delivered[0]["cancelled_at"])

    def test_question_without_enabled_channels_remains_usable(self) -> None:
        monitor.set_notification_channel_enabled("telegram", False)

        question = monitor.ask_question("No notifier configured")

        self.assertEqual(monitor.list_open_questions()[0]["id"], question["id"])
        self.assertEqual(monitor.list_notification_deliveries(), [])
        event = monitor.list_notification_outbox()[0]
        self.assertEqual(event["status"], "cancelled")

    def test_email_supports_multiple_arbitrary_recipients(self) -> None:
        monitor.configure_email(
            to_addresses=["first@example.com", "second@example.net"],
            from_address="sender@example.org",
            smtp_host="smtp.example.com",
            smtp_port=587,
            username=None,
            security="starttls",
        )

        self.assertEqual(
            monitor.email_recipients(),
            ["first@example.com", "second@example.net"],
        )

    def test_email_recipient_add_remove_and_deduplicate(self) -> None:
        monitor.configure_email(
            to_address="first@example.com",
            from_address="sender@example.org",
            smtp_host="smtp.example.com",
            smtp_port=587,
            username=None,
            security="starttls",
        )

        added = monitor.add_email_recipients(
            ["SECOND@example.net", "first@example.com"]
        )
        self.assertEqual(added, ["first@example.com", "SECOND@example.net"])

        remaining = monitor.remove_email_recipients(["second@example.net"])
        self.assertEqual(remaining, ["first@example.com"])

    def test_legacy_single_to_config_migrates_to_recipients(self) -> None:
        monitor.DATA_DIR.mkdir(parents=True, exist_ok=True)
        monitor.notification_config_path().write_text(
            json.dumps(
                {
                    "telegram": {"enabled": False, "chat_id": None},
                    "email": {
                        "enabled": False,
                        "to": "legacy@example.com",
                        "from_address": "sender@example.com",
                        "smtp_host": "smtp.example.com",
                        "smtp_port": 587,
                        "username": None,
                        "security": "starttls",
                    },
                }
            ),
            encoding="utf-8",
        )

        config = monitor.read_notification_config()

        self.assertEqual(config["email"]["recipients"], ["legacy@example.com"])
        self.assertNotIn("to", config["email"])

    def test_multiple_email_recipients_have_independent_delivery_state(self) -> None:
        monitor.set_notification_channel_enabled("telegram", False)
        monitor.configure_email(
            to_addresses=["first@example.com", "second@example.net"],
            from_address="sender@example.org",
            smtp_host="smtp.example.com",
            smtp_port=587,
            username=None,
            security="starttls",
        )
        monitor.set_notification_channel_enabled("email", True)
        monitor.ask_question("Notify both")

        deliveries = monitor.list_notification_deliveries()
        self.assertEqual(
            [(item["channel"], item["target"]) for item in deliveries],
            [
                ("email", "first@example.com"),
                ("email", "second@example.net"),
            ],
        )

        def first_attempt(payload, recipient):
            if recipient == "second@example.net":
                raise RuntimeError("second recipient unavailable")

        with patch.object(
            monitor,
            "_send_email_notification",
            side_effect=first_attempt,
        ) as sender:
            first = monitor.dispatch_pending_notifications(force=True)

        self.assertEqual(first, {"attempted": 2, "delivered": 1, "failed": 1})
        self.assertEqual(sender.call_count, 2)

        pending = monitor.list_notification_deliveries(status="pending")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["target"], "second@example.net")

        with patch.object(monitor, "_send_email_notification") as retry:
            second = monitor.dispatch_pending_notifications(force=True)

        self.assertEqual(second, {"attempted": 1, "delivered": 1, "failed": 0})
        retry.assert_called_once()
        self.assertEqual(
            retry.call_args.args[1],
            "second@example.net",
        )

    def test_removing_recipient_cancels_only_that_pending_delivery(self) -> None:
        monitor.set_notification_channel_enabled("telegram", False)
        monitor.configure_email(
            to_addresses=["first@example.com", "second@example.net"],
            from_address="sender@example.org",
            smtp_host="smtp.example.com",
            smtp_port=587,
            username=None,
            security="starttls",
        )
        monitor.set_notification_channel_enabled("email", True)
        monitor.ask_question("Remove one")

        monitor.remove_email_recipients(["second@example.net"])

        deliveries = {
            item["target"]: item["status"]
            for item in monitor.list_notification_deliveries()
        }
        self.assertEqual(deliveries["first@example.com"], "pending")
        self.assertEqual(deliveries["second@example.net"], "cancelled")
        self.assertTrue(monitor.read_notification_config()["email"]["enabled"])

    def test_email_transport_sends_one_message_per_target_without_address_leak(self) -> None:
        monitor.configure_email(
            to_addresses=["first@example.com", "second@example.net"],
            from_address="sender@example.org",
            smtp_host="smtp.example.com",
            smtp_port=587,
            username=None,
            security="starttls",
        )
        monitor.set_notification_channel_enabled("email", True)
        client = MagicMock()
        smtp = MagicMock()
        smtp.return_value.__enter__.return_value = client
        payload = {
            "project": {"project_id": "abc", "name": "demo"},
            "question_id": 9,
            "question": "Multiple recipients?",
        }

        with patch.object(monitor.smtplib, "SMTP", smtp):
            monitor._send_email_notification(payload, "second@example.net")

        message = client.send_message.call_args.args[0]
        self.assertEqual(message["To"], "second@example.net")
        self.assertNotIn("first@example.com", str(message))

    def test_notify_parser_accepts_repeated_to_and_recipient_commands(self) -> None:
        parsed = monitor.parse_notify_args(
            [
                "email",
                "set",
                "--to",
                "first@example.com",
                "--to",
                "second@example.net",
                "--from-address",
                "sender@example.org",
                "--smtp-host",
                "smtp.example.com",
            ]
        )
        self.assertEqual(
            parsed.to_addresses,
            ["first@example.com", "second@example.net"],
        )

        add = monitor.parse_notify_args(
            ["email", "recipient", "add", "third@example.com"]
        )
        self.assertEqual(add.recipient_action, "add")
        self.assertEqual(add.addresses, ["third@example.com"])

    def test_legacy_delivery_table_migrates_email_target(self) -> None:
        monitor.DATA_DIR.mkdir(parents=True, exist_ok=True)
        monitor.notification_config_path().write_text(
            json.dumps(
                {
                    "email": {
                        "enabled": True,
                        "to": "legacy@example.com",
                        "from_address": "sender@example.com",
                        "smtp_host": "smtp.example.com",
                        "smtp_port": 587,
                        "username": None,
                        "security": "starttls",
                    }
                }
            ),
            encoding="utf-8",
        )
        with closing(sqlite3.connect(monitor.DB_PATH)) as connection:
            connection.execute(
                """
                CREATE TABLE notification_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    last_attempt_at TEXT,
                    delivered_at TEXT,
                    cancelled_at TEXT,
                    last_error TEXT,
                    UNIQUE(event_type, entity_type, entity_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE notification_deliveries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    notification_id INTEGER NOT NULL,
                    channel TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    last_attempt_at TEXT,
                    delivered_at TEXT,
                    cancelled_at TEXT,
                    last_error TEXT,
                    UNIQUE(notification_id, channel)
                )
                """
            )
            connection.execute(
                """
                INSERT INTO notification_outbox (
                    id, event_type, entity_type, entity_id, payload_json,
                    status, attempt_count, created_at
                )
                VALUES (1, 'question.created', 'question', 1, '{}', 'pending', 0, '2026-01-01T00:00:00Z')
                """
            )
            connection.execute(
                """
                INSERT INTO notification_deliveries (
                    notification_id, channel, status, attempt_count, created_at
                )
                VALUES (1, 'email', 'pending', 0, '2026-01-01T00:00:00Z')
                """
            )
            connection.commit()

        monitor.connect_db().close()

        deliveries = monitor.list_notification_deliveries()
        self.assertEqual(deliveries[0]["target"], "legacy@example.com")

    def test_invalid_email_recipient_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            monitor.configure_email(
                to_address="not-an-email",
                from_address="sender@example.org",
                smtp_host="smtp.example.com",
                smtp_port=587,
                username=None,
                security="starttls",
            )

    def test_email_is_optional_and_off_by_default(self) -> None:
        monitor.configure_email(
            to_address="to@example.com",
            from_address="from@example.com",
            smtp_host="smtp.example.com",
            smtp_port=587,
            username=None,
            security="starttls",
        )

        monitor.ask_question("Telegram only")

        deliveries = monitor.list_notification_deliveries()
        self.assertEqual([item["channel"] for item in deliveries], ["telegram"])

    def test_email_on_creates_independent_delivery(self) -> None:
        monitor.configure_email(
            to_address="to@example.com",
            from_address="from@example.com",
            smtp_host="smtp.example.com",
            smtp_port=587,
            username=None,
            security="starttls",
        )
        monitor.set_notification_channel_enabled("email", True)

        monitor.ask_question("Two channels")

        deliveries = monitor.list_notification_deliveries()
        self.assertEqual(
            [item["channel"] for item in deliveries],
            ["telegram", "email"],
        )

    def test_disabling_email_cancels_only_pending_email_delivery(self) -> None:
        monitor.configure_email(
            to_address="to@example.com",
            from_address="from@example.com",
            smtp_host="smtp.example.com",
            smtp_port=587,
            username=None,
            security="starttls",
        )
        monitor.set_notification_channel_enabled("email", True)
        monitor.ask_question("Disable email")

        monitor.set_notification_channel_enabled("email", False)

        deliveries = monitor.list_notification_deliveries()
        statuses = {item["channel"]: item["status"] for item in deliveries}
        self.assertEqual(statuses["telegram"], "pending")
        self.assertEqual(statuses["email"], "cancelled")
        self.assertEqual(monitor.list_notification_outbox()[0]["status"], "pending")

    def test_dispatch_retries_only_failed_channel(self) -> None:
        monitor.configure_email(
            to_address="to@example.com",
            from_address="from@example.com",
            smtp_host="smtp.example.com",
            smtp_port=587,
            username=None,
            security="starttls",
        )
        monitor.set_notification_channel_enabled("email", True)
        monitor.ask_question("Retry email only")

        with (
            patch.object(monitor, "_send_telegram_notification") as telegram,
            patch.object(
                monitor,
                "_send_email_notification",
                side_effect=RuntimeError("smtp down"),
            ) as email,
        ):
            first = monitor.dispatch_pending_notifications()

        self.assertEqual(first, {"attempted": 2, "delivered": 1, "failed": 1})
        telegram.assert_called_once()
        email.assert_called_once()

        with (
            patch.object(monitor, "_send_telegram_notification") as telegram_again,
            patch.object(monitor, "_send_email_notification") as email_again,
        ):
            second = monitor.dispatch_pending_notifications(force=True)

        self.assertEqual(second, {"attempted": 1, "delivered": 1, "failed": 0})
        telegram_again.assert_not_called()
        email_again.assert_called_once()
        self.assertEqual(monitor.list_notification_outbox()[0]["status"], "delivered")

    def test_automatic_retry_respects_backoff_but_force_bypasses_it(self) -> None:
        monitor.ask_question("Back off")
        delivery = monitor.list_notification_deliveries()[0]
        monitor.record_notification_delivery_attempt(
            delivery["id"],
            delivered=False,
            error="temporary",
        )

        with patch.object(monitor, "_send_telegram_notification") as sender:
            automatic = monitor.dispatch_pending_notifications()
            forced = monitor.dispatch_pending_notifications(force=True)

        self.assertEqual(automatic["attempted"], 0)
        self.assertEqual(forced["attempted"], 1)
        sender.assert_called_once()

    def test_telegram_transport_posts_chat_id_and_question(self) -> None:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok":true,"result":{}}'
        payload = {
            "project": {"project_id": "abc", "name": "demo"},
            "question_id": 7,
            "question": "Choose A or B?",
        }

        with patch.object(
            monitor.urllib.request,
            "urlopen",
            return_value=response,
        ) as urlopen:
            monitor._send_telegram_notification(payload)

        request = urlopen.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["chat_id"], "123456")
        self.assertIn("質問 #7: Choose A or B?", body["text"])
        self.assertIn("test-token", request.full_url)

    def test_telegram_reply_answers_only_matching_private_chat_notification(self) -> None:
        monitor.set_telegram_replies_enabled(True)
        question = monitor.ask_question("Choose A or B?")
        with patch.object(monitor, "_send_telegram_notification", return_value=77):
            self.assertEqual(
                monitor.dispatch_pending_notifications(force=True),
                {"attempted": 1, "delivered": 1, "failed": 0},
            )
        delivery = monitor.list_notification_deliveries()[0]
        self.assertEqual(delivery["telegram_message_id"], 77)
        self.assertEqual(delivery["telegram_chat_id"], "123456")
        update = {
            "message": {
                "chat": {"id": 123456, "type": "private"},
                "from": {"id": 123456, "is_bot": False},
                "text": "A",
                "reply_to_message": {"message_id": 77},
            }
        }
        with patch.object(monitor, "_telegram_api_request") as acknowledge:
            self.assertFalse(monitor._telegram_answer_from_update(
                {**update, "message": {**update["message"], "chat": {"id": 999, "type": "private"}}},
                "123456",
            ))
            self.assertFalse(monitor._telegram_answer_from_update(
                {**update, "message": {**update["message"], "reply_to_message": {"message_id": 78}}},
                "123456",
            ))
            self.assertFalse(monitor._telegram_answer_from_update(
                {**update, "message": {**update["message"], "text": "/start"}},
                "123456",
            ))
            self.assertTrue(monitor._telegram_answer_from_update(update, "123456"))
            self.assertTrue(monitor._telegram_answer_from_update(update, "123456"))
        self.assertEqual(monitor.list_open_questions(), [])
        self.assertEqual(monitor.list_answered_questions()[0]["answer"], "A")
        self.assertEqual(len(monitor.list_answered_questions()), 1)
        self.assertEqual(acknowledge.call_count, 2)

    def test_telegram_ack_failure_keeps_saved_answer(self) -> None:
        monitor.set_telegram_replies_enabled(True)
        question = monitor.ask_question("A or B?")
        with patch.object(monitor, "_send_telegram_notification", return_value=91):
            monitor.dispatch_pending_notifications(force=True)
        update = {
            "message": {
                "chat": {"id": 123456, "type": "private"},
                "from": {"id": 123456, "is_bot": False},
                "text": "B",
                "reply_to_message": {"message_id": 91},
            }
        }
        with patch.object(monitor, "_telegram_api_request", side_effect=RuntimeError("offline")):
            self.assertTrue(monitor._telegram_answer_from_update(update, "123456"))
        self.assertEqual(monitor.list_answered_questions()[0]["answer"], "B")
        self.assertEqual(monitor.list_open_questions(), [])

    def test_telegram_poll_cursor_prevents_reprocessing_update(self) -> None:
        monitor.set_telegram_replies_enabled(True)
        update = {"update_id": 42, "message": {"text": "/start"}}
        with (
            patch.object(monitor, "_telegram_api_request", return_value=[update]) as request,
            patch.object(monitor, "_telegram_answer_from_update", return_value=False) as answer,
        ):
            first = monitor.poll_telegram_replies_once()
            second = monitor.poll_telegram_replies_once()
        self.assertEqual(first, {"updates": 1, "answers": 0})
        self.assertEqual(second, {"updates": 0, "answers": 0})
        self.assertEqual(request.call_args_list[1].args[1]["offset"], 43)
        answer.assert_called_once()

    def test_telegram_notification_requests_reply_and_records_message_id(self) -> None:
        monitor.set_telegram_replies_enabled(True)
        response = MagicMock()
        response.__enter__.return_value.read.return_value = (
            b'{"ok":true,"result":{"message_id":77}}'
        )
        payload = {
            "project": {"project_id": "abc", "name": "demo"},
            "question_id": 7,
            "question": "Which value?",
        }
        with patch.object(monitor.urllib.request, "urlopen", return_value=response) as urlopen:
            self.assertEqual(monitor._send_telegram_notification(payload), 77)
        body = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertTrue(body["reply_markup"]["force_reply"])
        self.assertIn("この通知に返信", body["text"])

    def test_email_transport_uses_starttls_and_send_message(self) -> None:
        monitor.configure_email(
            to_address="to@example.com",
            from_address="from@example.com",
            smtp_host="smtp.example.com",
            smtp_port=587,
            username=None,
            security="starttls",
        )
        monitor.set_notification_channel_enabled("email", True)
        client = MagicMock()
        smtp = MagicMock()
        smtp.return_value.__enter__.return_value = client
        payload = {
            "project": {"project_id": "abc", "name": "demo"},
            "question_id": 8,
            "question": "Approve this?",
        }

        with patch.object(monitor.smtplib, "SMTP", smtp):
            monitor._send_email_notification(payload)

        smtp.assert_called_once()
        client.starttls.assert_called_once()
        client.send_message.assert_called_once()
        message = client.send_message.call_args.args[0]
        self.assertEqual(message["To"], "to@example.com")
        self.assertIn("demo needs your answer", message["Subject"])

    def test_notification_config_is_ignored_by_project_gitignore(self) -> None:
        ignore_text = (monitor.DATA_DIR / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("notifications.json", ignore_text.splitlines())

    def test_telegram_config_does_not_store_token(self) -> None:
        config_text = monitor.notification_config_path().read_text(encoding="utf-8")
        self.assertNotIn("test-token", config_text)
        public = monitor.notification_config_public()
        self.assertTrue(public["telegram"]["token_available"])
        self.assertEqual(public["telegram"]["token_env"], monitor.TELEGRAM_TOKEN_ENV)

    def test_notifications_cli_outputs_json_and_filters_status(self) -> None:
        monitor.ask_question("CLI event")
        event = monitor.list_notification_outbox()[0]
        delivery = monitor.list_notification_deliveries(notification_id=event["id"])[0]
        monitor.record_notification_delivery_attempt(delivery["id"], delivered=True)
        output = io.StringIO()

        with (
            patch.object(sys, "argv", ["monitor", "notifications", "--status", "delivered"]),
            redirect_stdout(output),
        ):
            monitor.main()

        payload = json.loads(output.getvalue())
        self.assertEqual(len(payload["notifications"]), 1)
        self.assertEqual(payload["notifications"][0]["status"], "delivered")


class RunnerLifecycleTests(MonitorStorageTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.project_root_patch = patch.object(monitor, "PROJECT_ROOT", self.root)
        self.project_root_patch.start()
        self.thread_env_patch = patch.dict(
            os.environ,
            {monitor.CODEX_THREAD_ID_ENV: "019f-test-thread"},
        )
        self.thread_env_patch.start()

    def tearDown(self) -> None:
        self.thread_env_patch.stop()
        self.project_root_patch.stop()
        super().tearDown()

    def _register_waiting_question(self) -> tuple[dict[str, object], dict[str, object]]:
        runner = monitor.register_runner("codex")
        monitor.start_task("Continue after answer")
        question = monitor.ask_question("Use A or B?")
        return runner, question

    def test_codex_runner_registers_from_thread_environment(self) -> None:
        runner = monitor.register_runner("codex")

        self.assertEqual(runner["external_id"], "019f-test-thread")
        self.assertEqual(runner["state"], "running")
        self.assertEqual(len(monitor.list_runners()), 1)

    def test_runner_registration_requires_codex_thread_id(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                monitor.register_runner("codex")

    def test_question_links_registered_runner_and_marks_waiting(self) -> None:
        runner = monitor.register_runner("codex")

        question = monitor.ask_question("Need a decision")

        stored_runner = monitor.list_runners()[0]
        self.assertEqual(stored_runner["id"], runner["id"])
        self.assertEqual(stored_runner["state"], "waiting_for_human")
        with monitor.database_session() as connection:
            row = connection.execute(
                "SELECT runner_id FROM questions WHERE id = ?",
                (question["id"],),
            ).fetchone()
        self.assertEqual(row[0], runner["id"])

    def test_answer_creates_separate_event_and_pending_resume_request(self) -> None:
        runner, question = self._register_waiting_question()

        monitor.answer_question(question["id"], "A")

        with monitor.database_session() as connection:
            answer = connection.execute(
                "SELECT id, answer FROM answer_events WHERE question_id = ?",
                (question["id"],),
            ).fetchone()
        requests = monitor.list_resume_requests()
        self.assertEqual(answer[1], "A")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["answer_event_id"], answer[0])
        self.assertEqual(requests[0]["runner_id"], runner["id"])
        self.assertEqual(requests[0]["status"], "pending")

    def test_answer_without_active_task_does_not_schedule_resume(self) -> None:
        monitor.register_runner("codex")
        question = monitor.ask_question("Need a decision")

        monitor.answer_question(question["id"], "A")

        request = monitor.list_resume_requests()[0]
        self.assertEqual(request["status"], "cancelled")
        self.assertEqual(request["reason"], "no_active_task")

    def test_answer_without_registered_runner_keeps_legacy_behavior(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            question = monitor.ask_question("Legacy question")
            monitor.answer_question(question["id"], "Answer")

        self.assertEqual(monitor.list_resume_requests(), [])
        with monitor.database_session() as connection:
            answer_count = connection.execute(
                "SELECT COUNT(*) FROM answer_events"
            ).fetchone()[0]
        self.assertEqual(answer_count, 1)

    def test_same_answer_cannot_create_duplicate_resume_request(self) -> None:
        _, question = self._register_waiting_question()
        monitor.answer_question(question["id"], "A")

        with self.assertRaises(monitor.QuestionClosedError):
            monitor.answer_question(question["id"], "A again")

        self.assertEqual(len(monitor.list_resume_requests()), 1)

    def test_claim_is_atomic_and_creates_one_attempt(self) -> None:
        _, question = self._register_waiting_question()
        monitor.answer_question(question["id"], "A")

        first = monitor._claim_resume_request()
        second = monitor._claim_resume_request()

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(len(monitor.list_resume_attempts()), 1)

    def test_task_completed_after_answer_is_not_resumed(self) -> None:
        _, question = self._register_waiting_question()
        monitor.answer_question(question["id"], "A")
        monitor.complete_task()

        with patch.object(monitor, "_spawn_resume_worker") as spawn:
            result = monitor.dispatch_resume_requests(force=True)

        self.assertEqual(result, {"claimed": 0, "launched": 0})
        spawn.assert_not_called()
        request = monitor.list_resume_requests()[0]
        self.assertEqual(request["status"], "cancelled")
        self.assertEqual(request["reason"], "no_active_task_before_dispatch")

    def test_runner_completed_after_answer_is_not_resumed(self) -> None:
        _, question = self._register_waiting_question()
        monitor.answer_question(question["id"], "A")
        monitor.set_current_runner_state("completed")

        with patch.object(monitor, "_spawn_resume_worker") as spawn:
            result = monitor.dispatch_resume_requests(force=True)

        self.assertEqual(result, {"claimed": 0, "launched": 0})
        spawn.assert_not_called()
        request = monitor.list_resume_requests()[0]
        self.assertEqual(request["status"], "cancelled")
        self.assertEqual(request["reason"], "runner_state_completed_before_dispatch")

    def test_auto_resume_disabled_does_not_claim_request(self) -> None:
        _, question = self._register_waiting_question()
        monitor.answer_question(question["id"], "A")

        result = monitor.dispatch_resume_requests()

        self.assertEqual(result, {"claimed": 0, "launched": 0})
        self.assertEqual(monitor.list_resume_requests()[0]["status"], "pending")

    def test_second_answer_waits_while_same_runner_resume_is_active(self) -> None:
        _, first_question = self._register_waiting_question()
        monitor.answer_question(first_question["id"], "A")
        second_question = monitor.ask_question("Second decision?")
        monitor.answer_question(second_question["id"], "B")

        first_claim = monitor._claim_resume_request()
        second_claim = monitor._claim_resume_request()

        self.assertIsNotNone(first_claim)
        self.assertIsNone(second_claim)
        requests = monitor.list_resume_requests()
        statuses = {item["question_id"]: item["status"] for item in requests}
        self.assertEqual(statuses[first_question["id"]], "claimed")
        self.assertEqual(statuses[second_question["id"]], "pending")

    def test_dispatch_launches_only_one_worker(self) -> None:
        _, question = self._register_waiting_question()
        monitor.answer_question(question["id"], "A")
        with patch.object(monitor, "_spawn_resume_worker", return_value=4321) as spawn:
            first = monitor.dispatch_resume_requests(force=True)
            second = monitor.dispatch_resume_requests(force=True)

        self.assertEqual(first, {"claimed": 1, "launched": 1})
        self.assertEqual(second, {"claimed": 0, "launched": 0})
        spawn.assert_called_once()

    def test_restart_recovery_marks_missing_worker_uncertain_without_retry(self) -> None:
        _, question = self._register_waiting_question()
        monitor.answer_question(question["id"], "A")
        request_id, attempt_id = monitor._claim_resume_request()
        with monitor.database_session() as connection:
            connection.execute(
                "UPDATE resume_requests SET status = 'running' WHERE id = ?",
                (request_id,),
            )
            connection.execute(
                """
                UPDATE resume_attempts
                SET status = 'running', pid = 999999
                WHERE id = ?
                """,
                (attempt_id,),
            )

        with patch.object(monitor, "_process_is_alive", return_value=False):
            recovered = monitor.recover_resume_requests()
            dispatch = monitor.dispatch_resume_requests(force=True)

        self.assertEqual(recovered["uncertain"], 1)
        self.assertEqual(dispatch, {"claimed": 0, "launched": 0})
        self.assertEqual(monitor.list_resume_requests()[0]["status"], "uncertain")

    def test_uncertain_request_requires_explicit_manual_retry(self) -> None:
        _, question = self._register_waiting_question()
        monitor.answer_question(question["id"], "A")
        request_id, attempt_id = monitor._claim_resume_request()
        with monitor.database_session() as connection:
            connection.execute(
                "UPDATE resume_requests SET status = 'running' WHERE id = ?",
                (request_id,),
            )
            connection.execute(
                "UPDATE resume_attempts SET status = 'running', pid = 999999 WHERE id = ?",
                (attempt_id,),
            )
        with patch.object(monitor, "_process_is_alive", return_value=False):
            monitor.recover_resume_requests()

        retried = monitor.retry_resume_request(request_id)

        self.assertEqual(retried["status"], "pending")

    def test_codex_preflight_uses_same_noninteractive_policy_as_worker(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="AI_AGENT_MONITOR_CODEX_OK\n",
            stderr="",
        )

        with patch.object(monitor.subprocess, "run", return_value=completed) as run:
            result = monitor.run_codex_preflight()

        self.assertTrue(result["ok"])
        argv = run.call_args.args[0]
        self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")
        self.assertEqual(run.call_args.kwargs["errors"], "replace")
        self.assertEqual(
            argv[:6],
            [
                "codex",
                "-c",
                'sandbox_mode="workspace-write"',
                "-c",
                'approval_policy="on-request"',
                "exec",
            ],
        )
        self.assertNotIn("--ask-for-approval", argv)
        self.assertNotIn("--sandbox", argv)
        self.assertIn("AI_AGENT_MONITOR_CODEX_OK", argv[-1])

    def test_codex_preflight_failure_is_non_destructive_and_reported(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=2,
            stdout="",
            stderr="unexpected argument",
        )

        with patch.object(monitor.subprocess, "run", return_value=completed):
            result = monitor.run_codex_preflight()

        self.assertFalse(result["ok"])
        self.assertEqual(result["exit_code"], 2)
        self.assertEqual(result["error"], "unexpected argument")
        self.assertEqual(monitor.list_resume_requests(), [])
        self.assertEqual(monitor.list_resume_attempts(), [])

    def test_worker_resumes_exact_registered_codex_thread(self) -> None:
        _, question = self._register_waiting_question()
        monitor.answer_question(question["id"], "A")
        request_id, attempt_id = monitor._claim_resume_request()
        with monitor.database_session() as connection:
            connection.execute(
                "UPDATE resume_requests SET status = 'running' WHERE id = ?",
                (request_id,),
            )
            connection.execute(
                "UPDATE resume_attempts SET status = 'running', pid = 1234 WHERE id = ?",
                (attempt_id,),
            )

        completed = subprocess.CompletedProcess(args=[], returncode=0)
        with patch.object(monitor.subprocess, "run", return_value=completed) as run:
            exit_code = monitor.run_resume_worker(attempt_id)

        self.assertEqual(exit_code, 0)
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], "codex")
        self.assertEqual(
            argv[1:6],
            [
                "-c",
                'sandbox_mode="workspace-write"',
                "-c",
                'approval_policy="on-request"',
                "exec",
            ],
        )
        self.assertNotIn("--ask-for-approval", argv)
        self.assertIn("resume", argv)
        resume_index = argv.index("resume")
        self.assertEqual(argv[resume_index + 1], "019f-test-thread")
        self.assertEqual(monitor.list_resume_requests()[0]["status"], "completed")
        self.assertEqual(monitor.list_resume_attempts()[0]["status"], "completed")

    def test_worker_failure_is_recorded_without_automatic_retry(self) -> None:
        runner, question = self._register_waiting_question()
        monitor.answer_question(question["id"], "A")
        request_id, attempt_id = monitor._claim_resume_request()
        with monitor.database_session() as connection:
            connection.execute(
                "UPDATE resume_requests SET status = 'running' WHERE id = ?",
                (request_id,),
            )
            connection.execute(
                "UPDATE resume_attempts SET status = 'running', pid = 1234 WHERE id = ?",
                (attempt_id,),
            )

        failed = subprocess.CompletedProcess(args=[], returncode=7)
        with patch.object(monitor.subprocess, "run", return_value=failed):
            exit_code = monitor.run_resume_worker(attempt_id)

        self.assertEqual(exit_code, 7)
        request = monitor.list_resume_requests()[0]
        attempt = monitor.list_resume_attempts()[0]
        current_runner = monitor.list_runners()[0]
        self.assertEqual(request["status"], "cancelled")
        self.assertEqual(attempt["status"], "failed")
        self.assertEqual(current_runner["id"], runner["id"])
        self.assertEqual(current_runner["state"], "failed")

    def test_runner_complete_closes_running_resume_request(self) -> None:
        _, question = self._register_waiting_question()
        monitor.answer_question(question["id"], "A")
        request_id, _ = monitor._claim_resume_request()
        with monitor.database_session() as connection:
            connection.execute(
                "UPDATE resume_requests SET status = 'running' WHERE id = ?",
                (request_id,),
            )

        result = monitor.set_current_runner_state("completed")

        self.assertEqual(result["state"], "completed")
        self.assertEqual(monitor.list_resume_requests()[0]["status"], "completed")

    def test_runner_config_is_ignored_and_auto_resume_preflights_codex(self) -> None:
        with patch.object(monitor.shutil, "which", return_value="C:/codex.exe"):
            config = monitor.set_auto_resume_enabled(True)

        self.assertTrue(config["auto_resume"])
        ignore_text = (monitor.DATA_DIR / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("runner.json", ignore_text.splitlines())



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
