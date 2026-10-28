import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from ai_agent_monitor import app


class RemoteRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, value in (("PROJECT_ROOT", self.root), ("DATA_DIR", self.root / ".agent-monitor")):
            p = patch.object(app, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.saved = dict(project_root=str(self.root), backend_port=8778,
                          https_port=9456, pid=1234, backend_url="http://127.0.0.1:8778")
        self.status = {"Web": {"host:9456": {"Handlers": {"/": {"Proxy": self.saved["backend_url"]}}}}}
        p = patch.object(app, "_tailscale_dns_name", return_value="host")
        p.start()
        self.addCleanup(p.stop)

    def test_reboot_keeps_existing_serve_and_ports(self):
        with patch.object(app, "_port_is_free", return_value=True):
            self.assertEqual(app._remote_ports(self.status, self.saved), (8778, 9456))

    def test_reassigned_or_busy_ports_are_not_reallocated(self):
        with patch.object(app, "_port_is_free", return_value=False):
            with self.assertRaises(RuntimeError):
                app._remote_ports(self.status, self.saved)
        self.status["Web"]["host:9456"]["Handlers"]["/"]["Proxy"] = "http://127.0.0.1:9999"
        with self.assertRaises(RuntimeError):
            app._remote_ports(self.status, self.saved)

    def test_wrong_project_is_rejected(self):
        self.saved["project_root"] = "other"
        with self.assertRaises(RuntimeError):
            app._remote_ports(self.status, self.saved)

    def test_failed_restart_preserves_saved_state(self):
        app._write_remote_state(self.saved)
        with patch.object(app, "init_project"), patch.object(app, "_project_server_matches", return_value=False), patch.object(app, "_process_is_alive", return_value=False), patch.object(app, "_tailscale_status_json", return_value=self.status), patch.object(app, "_port_is_free", return_value=False):
            with self.assertRaises(RuntimeError):
                app.remote_start()
        self.assertEqual(app.read_remote_state(), self.saved)

    def test_healthy_endpoint_with_stale_pid_does_not_launch_duplicate(self):
        app._write_remote_state(self.saved)
        with patch.object(app, "init_project"), patch.object(app, "_project_server_matches", return_value=True), patch.object(app, "remote_status", return_value={"backend_alive": True, "tailscale_active": True}), patch.object(app.subprocess, "Popen") as launch:
            self.assertTrue(app.remote_start()["backend_alive"])
            launch.assert_not_called()

    def test_stop_then_start_retains_endpoint(self):
        app._write_remote_state(self.saved)
        process = MagicMock(pid=5678)
        with patch.object(app, "_process_is_alive", return_value=False), patch.object(app, "_tailscale_status_json", return_value=self.status), patch.object(app, "_tailscale_command"):
            app.remote_stop()
        self.assertIsNone(app.read_remote_state())
        with patch.object(app, "init_project"), patch.object(app, "_tailscale_status_json", return_value={}), patch.object(app, "_port_is_free", return_value=True), patch.object(app.subprocess, "Popen", return_value=process), patch.object(app, "_wait_for_project_server"), patch.object(app, "_tailscale_command"):
            state = app.remote_start()
        self.assertEqual((state["backend_port"], state["https_port"]), (8778, 9456))

    def test_failed_reboot_does_not_remove_existing_serve(self):
        app._write_remote_state(self.saved)
        with patch.object(app, "init_project"), patch.object(app, "_project_server_matches", return_value=False), patch.object(app, "_process_is_alive", return_value=False), patch.object(app, "_tailscale_status_json", return_value=self.status), patch.object(app, "_port_is_free", return_value=True), patch.object(app.subprocess, "Popen", return_value=MagicMock()), patch.object(app, "_wait_for_project_server"), patch.object(app, "_tailscale_command") as command, patch.object(app, "_write_remote_state", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                app.remote_start()
        self.assertFalse(any("off" in call.args for call in command.call_args_list))

    def test_healthy_backend_repairs_missing_serve_without_new_process(self):
        app._write_remote_state(self.saved)
        with patch.object(app, "init_project"), patch.object(app, "_project_server_matches", return_value=True), patch.object(app, "remote_status", side_effect=[{"tailscale_active": False}, {"tailscale_active": True}]), patch.object(app, "_tailscale_status_json", return_value={}), patch.object(app, "_tailscale_command") as command, patch.object(app.subprocess, "Popen") as launch:
            self.assertTrue(app.remote_start()["tailscale_active"])
        launch.assert_not_called()
        command.assert_called_once_with("serve", "--bg", "--yes", "--https=9456", self.saved["backend_url"])

    def test_legacy_migration_requires_live_project_identity(self):
        (self.root / "monitor.py").touch()
        legacy = self.root / ".runtime"
        legacy.mkdir()
        (legacy / "ports.env").write_text("BACKEND_PORT=8778\nTAILSCALE_HTTPS_PORT=9456\nMONITOR_PID=1234\n")
        with patch.object(app, "init_project"), patch.object(app, "_tailscale_status_json", return_value=self.status), patch.object(app, "_project_server_matches", return_value=False):
            with self.assertRaises(RuntimeError):
                app.remote_start()
        self.assertIsNone(app.read_remote_state())
        with patch.object(app, "init_project"), patch.object(app, "_tailscale_status_json", return_value=self.status), patch.object(app, "_project_server_matches", return_value=True), patch.object(app, "_process_is_alive", return_value=True), patch.object(app.subprocess, "Popen") as launch:
            state = app.remote_start()
        launch.assert_not_called()
        self.assertEqual(state["https_port"], 9456)
