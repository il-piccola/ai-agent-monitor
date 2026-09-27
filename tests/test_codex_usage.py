import io
import json
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from ai_agent_monitor import app, codex_usage


class CodexUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name) / "runtime" / "usage.json"

    def test_weekly_window_is_account_wide_and_can_be_secondary(self):
        result = {"rateLimitsByLimitId": {"codex": {
            "primary": {"usedPercent": 10, "windowDurationMins": 300},
            "secondary": {
                "usedPercent": 56, "windowDurationMins": 10080,
                "resetsAt": 1791054513,
            },
        }}}
        weekly = codex_usage.weekly_limit(result)
        self.assertEqual(weekly["remaining_percent"], 44.0)
        self.assertEqual(weekly["scope"], "account")
        self.assertEqual(weekly["window_minutes"], 10080)
        self.assertTrue(weekly["resets_at"].endswith("Z"))

    def test_missing_weekly_limit_does_not_invent_a_percentage(self):
        self.assertIsNone(codex_usage.weekly_limit({"rateLimits": {
            "primary": {"usedPercent": 56, "windowDurationMins": 300}
        }}))
        self.assertIsNone(codex_usage.weekly_limit({"rateLimits": {
            "primary": {"usedPercent": None, "windowDurationMins": 10080}
        }}))
        self.assertIsNone(codex_usage.weekly_limit({}))

    def test_manager_is_opt_in_and_exposes_no_account_identifier(self):
        manager = codex_usage.WeeklyUsageManager(refresh_seconds=60)
        self.assertEqual(manager.snapshot(self.config), {"enabled": False})
        codex_usage.set_enabled(self.config, True)
        with patch.object(codex_usage.shutil, "which", return_value="codex.exe"), \
             patch.object(codex_usage, "read_weekly_limit", return_value={
                 "remaining_percent": 44.0, "scope": "account", "resets_at": None,
             }):
            first = manager.snapshot(self.config)
            self.assertIn(first["state"], ("calculating", "ready"))
            for _ in range(100):
                with manager._lock:
                    if manager._result is not None:
                        break
                time.sleep(0.01)
        state = manager.snapshot(self.config)
        self.assertEqual(state["remaining_percent"], 44.0)
        self.assertNotIn("accountId", state)
        codex_usage.set_enabled(self.config, False)
        self.assertEqual(manager.snapshot(self.config), {"enabled": False})

    def test_cli_and_http_route(self):
        with patch.object(app, "CODEX_USAGE_CONFIG_PATH", self.config):
            for command, expected in (("enable", True), ("status", True), ("disable", False)):
                with patch.object(sys, "argv", ["monitor", "usage", command]), \
                     redirect_stdout(io.StringIO()) as output:
                    app.main()
                self.assertEqual(json.loads(output.getvalue()), {"enabled": expected})
        fake = MagicMock()
        fake.path = "/api/codex-usage"
        with patch.object(app.CODEX_USAGE_MANAGER, "snapshot", return_value={"enabled": False}):
            app.DashboardHandler.do_GET(fake)
        self.assertEqual(fake._serve_json.call_args.args[0], {"enabled": False})

    def test_refresh_failure_preserves_last_successful_value(self):
        codex_usage.set_enabled(self.config, True)
        manager = codex_usage.WeeklyUsageManager()
        with patch.object(codex_usage, "_configured_command", return_value="codex.exe"), \
             patch.object(codex_usage, "read_weekly_limit", side_effect=[
                 {"remaining_percent": 44.0, "scope": "account", "resets_at": None},
                 None,
                 RuntimeError("temporary failure"),
             ]):
            manager._refresh(self.config)
            with manager._lock:
                manager._last_start = time.monotonic()
            first = manager.snapshot(self.config)
            manager._refresh(self.config)
            missing_window = manager.snapshot(self.config)
            manager._refresh(self.config)
            failed_request = manager.snapshot(self.config)

        self.assertEqual(first["state"], "ready")
        for result in (missing_window, failed_request):
            self.assertEqual(result["state"], "stale")
            self.assertEqual(result["remaining_percent"], 44.0)
            self.assertEqual(result["observed_at"], first["observed_at"])


if __name__ == "__main__":
    unittest.main()
