import io
import json
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from ai_agent_monitor import app as monitor
from ai_agent_monitor import registry


PROJECT_A = "a" * 16
PROJECT_B = "b" * 16


class RegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / ".agent-monitor" / "registry.json"

    def test_url_validation_limits_plain_http_to_loopback(self) -> None:
        self.assertEqual(
            registry.normalize_dashboard_url(" https://one.ts.net:9443/ "),
            "https://one.ts.net:9443",
        )
        self.assertEqual(
            registry.normalize_dashboard_url("http://127.0.0.1:8766/"),
            "http://127.0.0.1:8766",
        )
        for url in (
            "http://example.com", "https://user:pass@example.com",
            "https://example.com/private", "https://example.com/?token=x",
            "file:///etc/passwd",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                registry.normalize_dashboard_url(url)

    def test_add_is_idempotent_and_remove_never_creates_a_database(self) -> None:
        with patch.object(registry, "_fetch_json", return_value={
            "project_id": PROJECT_A, "name": "Project A",
        }) as fetch:
            first = registry.add_project(self.path, "https://one.ts.net:9443/")
            second = registry.add_project(self.path, "https://one.ts.net:9443")

        self.assertEqual(first, second)
        self.assertEqual(len(registry.load_entries(self.path)), 1)
        self.assertEqual(fetch.call_count, 2)
        self.assertFalse((self.root / ".agent-monitor" / "monitor.db").exists())
        self.assertTrue(registry.remove_project(self.path, PROJECT_A))
        self.assertFalse(registry.remove_project(self.path, PROJECT_A))
        self.assertEqual(registry.load_entries(self.path), [])

    def test_snapshot_uses_status_api_and_reports_last_activity(self) -> None:
        url = "https://one.ts.net:9443"
        with patch.object(registry, "_fetch_json", return_value={
            "project_id": PROJECT_A, "name": "Project A",
        }):
            registry.add_project(self.path, url)

        status = {
            "schema_version": 1,
            "project": {"project_id": PROJECT_A, "name": "Project A"},
            "current_task": {"title": "Build page", "started_at": "2026-09-27T10:00:00Z"},
            "open_questions": [{"created_at": "2026-09-27T10:04:00Z"}],
            "recent_progress": [{"created_at": "2026-09-27T10:02:00Z"}],
            "recent_answers": [],
            "latest_artifact": None,
            "metrics": [],
        }
        with patch.object(registry, "_fetch_json", return_value=status) as fetch:
            item = registry.snapshot(self.path)["projects"][0]

        fetch.assert_called_once_with(url, "/api/status")
        self.assertTrue(item["available"])
        self.assertEqual(item["current_task"], "Build page")
        self.assertEqual(item["unanswered_count"], 1)
        self.assertEqual(item["last_activity"], "2026-09-27T10:04:00Z")

    def test_legacy_api_fallback_does_not_read_project_files(self) -> None:
        url = "https://old.ts.net:9444"
        with patch.object(registry, "_fetch_json", return_value={
            "project_id": PROJECT_B, "name": "Older Monitor",
        }):
            registry.add_project(self.path, url)

        def fetch(base: str, endpoint: str) -> dict:
            self.assertEqual(base, url)
            if endpoint == "/api/status":
                raise urllib.error.HTTPError(url + endpoint, 404, "Not Found", {}, None)
            return {
                "/api/project": {"project_id": PROJECT_B, "name": "Older Monitor"},
                "/api/task": {"task": None},
                "/api/questions": {"questions": []},
                "/api/progress": {"progress": [{"created_at": "2026-09-27T11:00:00Z"}]},
                "/api/answers": {"answers": []},
            }[endpoint]

        with patch.object(registry, "_fetch_json", side_effect=fetch):
            item = registry.snapshot(self.path)["projects"][0]
        self.assertTrue(item["available"])
        self.assertEqual(item["unanswered_count"], 0)
        self.assertEqual(item["last_activity"], "2026-09-27T11:00:00Z")
        self.assertFalse((self.root / ".agent-monitor" / "monitor.db").exists())

    def test_unavailable_and_mismatched_projects_are_not_shown_as_healthy(self) -> None:
        with patch.object(registry, "_fetch_json", return_value={
            "project_id": PROJECT_A, "name": "Project A",
        }):
            registry.add_project(self.path, "https://one.ts.net:9443")
        with patch.object(registry, "_fetch_json", side_effect=OSError("offline")):
            offline = registry.snapshot(self.path)["projects"][0]
        with patch.object(registry, "_fetch_json", return_value={
            "schema_version": 1,
            "project": {"project_id": PROJECT_B, "name": "Wrong project"},
            "current_task": None,
            "open_questions": [],
        }):
            mismatched = registry.snapshot(self.path)["projects"][0]
        self.assertEqual(offline["health"], "unavailable")
        self.assertFalse(offline["available"])
        self.assertEqual(mismatched["health"], "identity_mismatch")
        self.assertFalse(mismatched["available"])

    def test_cli_and_http_routes_use_the_project_local_registry(self) -> None:
        with (
            patch.object(monitor, "PROJECT_ROOT", self.root),
            patch.object(monitor, "DATA_DIR", self.root / ".agent-monitor"),
            patch.object(monitor, "DB_PATH", self.root / ".agent-monitor" / "monitor.db"),
            patch.object(registry, "_fetch_json", return_value={
                "project_id": PROJECT_A, "name": "Project A",
            }),
            patch.object(sys, "argv", ["monitor", "registry", "add", "https://one.ts.net:9443"]),
            redirect_stdout(io.StringIO()) as output,
        ):
            monitor.main()
        self.assertEqual(json.loads(output.getvalue())["project_id"], PROJECT_A)
        self.assertIn("registry.json", (self.root / ".agent-monitor" / ".gitignore").read_text())
        self.assertFalse((self.root / ".agent-monitor" / "monitor.db").exists())

        fake = MagicMock()
        fake.path = "/registry"
        monitor.DashboardHandler.do_GET(fake)
        fake._serve_registry_page.assert_called_once()

        fake = MagicMock()
        fake.path = "/api/status"
        with patch.object(monitor, "DB_PATH", self.root / "missing.db"):
            monitor.DashboardHandler.do_GET(fake)
        self.assertEqual(fake._serve_json.call_args.args[0]["schema_version"], 1)

        fake = MagicMock()
        fake.path = "/api/registry"
        with patch.object(monitor, "DATA_DIR", self.root / ".agent-monitor"), \
             patch.object(registry, "snapshot", return_value={"projects": []}):
            monitor.DashboardHandler.do_GET(fake)
        fake._serve_json.assert_called_once_with({"projects": []})

    def test_bundled_registry_page_has_japanese_navigation(self) -> None:
        html = monitor.REGISTRY_PAGE_PATH.read_text(encoding="utf-8")
        self.assertIn('<html lang="ja">', html)
        self.assertIn("案件一覧", html)
        self.assertIn("ダッシュボードを開く", html)


if __name__ == "__main__":
    unittest.main()
