import io
import json
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from ai_agent_monitor import app, codex_cost


def event(kind, payload, timestamp="2026-09-28T00:00:00Z"):
    return json.dumps({"type": kind, "payload": payload, "timestamp": timestamp}) + "\n"


def usage(response_id, input_tokens, cached_input_tokens, output_tokens, write=0):
    return event("token_usage_record", {
        "response_id": response_id,
        "usage": {
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_input_tokens,
            "cache_write_input_tokens": write,
            "output_tokens": output_tokens,
        },
    })


class CodexCostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "project-a"
        self.root.mkdir()
        self.sessions = base / "sessions"
        self.sessions.mkdir()
        self.cache = base / "runtime" / "cache.json"
        self.config = base / "runtime" / "config.json"

    def test_project_scoping_caching_and_new_usage(self):
        own = self.sessions / "own.jsonl"
        own.write_text(
            event("session_meta", {"cwd": str(self.root)})
            + event("turn_context", {"cwd": str(self.root), "model": "gpt-6-sol"})
            + usage("one", 1_000_000, 800_000, 10_000)
            + usage("one", 1_000_000, 800_000, 10_000),
            encoding="utf-8",
        )
        (self.sessions / "other.jsonl").write_text(
            event("session_meta", {"cwd": str(self.root.parent / "project-b")})
            + event("turn_context", {"model": "gpt-6-astra"})
            + usage("other", 1_000_000, 0, 0),
            encoding="utf-8",
        )
        first = codex_cost.estimate(self.root, self.sessions, self.cache)
        self.assertEqual(first["amount_usd"], "1.27")
        self.assertEqual(first["priced_responses"], 1)
        self.assertEqual(first["excluded_responses"], 0)
        with patch.object(codex_cost, "_scan_file", side_effect=AssertionError):
            self.assertEqual(
                codex_cost.estimate(self.root, self.sessions, self.cache)["amount_usd"],
                "1.27",
            )
        with own.open("a", encoding="utf-8") as stream:
            stream.write(usage("two", 1_000_000, 900_000, 0))
        updated = codex_cost.estimate(self.root, self.sessions, self.cache)
        self.assertEqual(updated["amount_usd"], "2.03")
        self.assertEqual(updated["priced_responses"], 2)

    def test_long_context_and_unknown_model_are_disclosed(self):
        (self.sessions / "own.jsonl").write_text(
            event("session_meta", {"cwd": str(self.root)})
            + event("turn_context", {"model": "gpt-6-sol"})
            + usage("long", 300_000, 100_000, 100_000, 10_000)
            + event("turn_context", {"model": "codex-auto-review"})
            + usage("excluded", 10_000, 0, 500),
            encoding="utf-8",
        )
        result = codex_cost.estimate(self.root, self.sessions, self.cache)
        self.assertEqual(result["amount_usd"], "2.39")
        self.assertEqual(result["priced_responses"], 1)
        self.assertEqual(result["excluded_models"], {"codex-auto-review": 1})

    def test_enable_and_manager_do_not_block_http_request(self):
        self.assertFalse(codex_cost.enabled(self.config))
        manager = codex_cost.CostEstimateManager(refresh_seconds=60)
        self.assertEqual(
            manager.snapshot(self.config, self.root, self.sessions, self.cache),
            {"enabled": False},
        )
        codex_cost.set_enabled(self.config, True)
        first = manager.snapshot(self.config, self.root, self.sessions, self.cache)
        self.assertTrue(first["enabled"])
        self.assertIn(first["state"], ("calculating", "ready"))
        for _ in range(100):
            with manager._lock:
                if manager._result is not None:
                    break
            time.sleep(0.01)
        self.assertEqual(manager.snapshot(self.config, self.root, self.sessions, self.cache)["amount_usd"], "0.00")
        codex_cost.set_enabled(self.config, False)
        self.assertEqual(manager.snapshot(self.config, self.root, self.sessions, self.cache), {"enabled": False})

    def test_cli_config_and_api_route(self):
        with patch.object(app, "CODEX_COST_CONFIG_PATH", self.config):
            for command, value in (("enable", True), ("status", True), ("disable", False)):
                with patch.object(sys, "argv", ["monitor", "cost", command]), \
                     redirect_stdout(io.StringIO()) as output:
                    app.main()
                self.assertEqual(json.loads(output.getvalue()), {"enabled": value})
        fake = MagicMock()
        fake.path = "/api/codex-cost"
        with patch.object(app.CODEX_COST_MANAGER, "snapshot", return_value={"enabled": False}):
            app.DashboardHandler.do_GET(fake)
        self.assertEqual(fake._serve_json.call_args.args[0], {"enabled": False})

    def test_refresh_failure_preserves_last_successful_estimate(self):
        codex_cost.set_enabled(self.config, True)
        manager = codex_cost.CostEstimateManager()
        estimate = {"amount_usd": "12.34", "priced_responses": 5,
                    "calculated_at": "2026-09-28T00:00:00Z"}
        with patch.object(codex_cost, "estimate", side_effect=[
            estimate, RuntimeError("temporary scan failure")
        ]):
            manager._refresh(self.root, self.sessions, self.cache)
            with manager._lock:
                manager._last_start = time.monotonic()
            first = manager.snapshot(self.config, self.root, self.sessions, self.cache)
            manager._refresh(self.root, self.sessions, self.cache)
            failed = manager.snapshot(self.config, self.root, self.sessions, self.cache)

        self.assertEqual(first["state"], "ready")
        self.assertEqual(failed["state"], "ready")
        self.assertEqual(failed["amount_usd"], "12.34")
        self.assertEqual(failed["calculated_at"], first["calculated_at"])


if __name__ == "__main__":
    unittest.main()
