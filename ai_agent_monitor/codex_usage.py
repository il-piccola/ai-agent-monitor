"""Read the signed-in Codex account's weekly limit via local app-server stdio."""

from __future__ import annotations

import json
import queue
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

WEEK_MINUTES = 7 * 24 * 60
DOCUMENTATION_URL = "https://learn.chatgpt.com/docs/app-server"


def weekly_limit(result: dict) -> dict | None:
    """Return the core Codex weekly window, excluding all account identifiers."""
    buckets = result.get("rateLimitsByLimitId")
    core = buckets.get("codex") if isinstance(buckets, dict) else None
    if not isinstance(core, dict):
        core = result.get("rateLimits")
    if not isinstance(core, dict):
        return None
    for name in ("primary", "secondary"):
        window = core.get(name)
        if not isinstance(window, dict) or window.get("windowDurationMins") != WEEK_MINUTES:
            continue
        used = window.get("usedPercent")
        if isinstance(used, bool) or not isinstance(used, (int, float)):
            return None
        remaining = round(max(0.0, min(100.0, 100.0 - float(used))), 1)
        reset = window.get("resetsAt")
        try:
            resets_at = datetime.fromtimestamp(reset, timezone.utc).isoformat().replace(
                "+00:00", "Z"
            ) if reset is not None else None
        except (TypeError, ValueError, OverflowError, OSError):
            resets_at = None
        return {
            "remaining_percent": remaining,
            "window_minutes": WEEK_MINUTES,
            "resets_at": resets_at,
            "scope": "account",
            "source": "codex_app_server_rate_limits",
            "documentation_url": DOCUMENTATION_URL,
        }
    return None


def read_weekly_limit(codex_command: str, timeout: float = 15.0) -> dict | None:
    """Ask only for rate limits. Never persist or expose the raw account reply."""
    process = subprocess.Popen(
        [codex_command, "app-server", "--listen", "stdio://"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", errors="replace",
    )
    lines: queue.Queue[str] = queue.Queue()

    def read_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            lines.put(line)

    threading.Thread(target=read_output, daemon=True).start()
    messages = (
        {"id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": "ai_agent_monitor", "title": "AI Agent Monitor", "version": "1.6.4"}
        }},
        {"method": "initialized", "params": {}},
        {"id": 2, "method": "account/rateLimits/read", "params": {}},
    )
    try:
        assert process.stdin is not None
        for message in messages:
            process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Codex rate-limit lookup timed out")
            try:
                line = lines.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError("Codex rate-limit lookup timed out") from exc
            try:
                reply = json.loads(line)
            except ValueError:
                continue
            if reply.get("id") != 2:
                continue
            if "error" in reply:
                raise ValueError("Codex rate-limit lookup failed")
            result = reply.get("result")
            return weekly_limit(result) if isinstance(result, dict) else None
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        if process.stdin:
            process.stdin.close()
        if process.stdout:
            process.stdout.close()


def enabled(config_path: Path) -> bool:
    try:
        return json.loads(config_path.read_text(encoding="utf-8")).get("enabled") is True
    except (OSError, ValueError, AttributeError):
        return False


def _configured_command(config_path: Path) -> str | None:
    try:
        command = json.loads(config_path.read_text(encoding="utf-8")).get("command")
    except (OSError, ValueError, AttributeError):
        command = None
    current = shutil.which("codex")
    if current:
        return current
    if isinstance(command, str) and Path(command).is_file():
        return command
    return None


def set_enabled(config_path: Path, value: bool) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        old = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    if not isinstance(old, dict):
        old = {}
    old["enabled"] = value
    if value:
        command = shutil.which("codex")
        if command:
            old["command"] = command
    config_path.write_text(json.dumps(old) + "\n", encoding="utf-8")


class WeeklyUsageManager:
    def __init__(self, refresh_seconds: int = 60) -> None:
        self.refresh_seconds = refresh_seconds
        self._lock = threading.Lock()
        self._last_start = 0.0
        self._running = False
        self._result: dict | None = None
        self._error = False

    def snapshot(self, config_path: Path) -> dict:
        if not enabled(config_path):
            return {"enabled": False}
        with self._lock:
            if (not self._running and (self._last_start == 0
                    or time.monotonic() - self._last_start >= self.refresh_seconds)):
                self._running = True
                self._last_start = time.monotonic()
                threading.Thread(target=self._refresh, args=(config_path,), daemon=True).start()
            if self._result is not None:
                return {"enabled": True, **self._result,
                        "state": "updating" if self._running else (
                            "stale" if self._error else self._result["state"])}
            return {"enabled": True,
                    "state": "error" if self._error and not self._running else "calculating"}

    def _refresh(self, config_path: Path) -> None:
        try:
            command = _configured_command(config_path)
            if command is None:
                raise FileNotFoundError("Codex executable is not on PATH")
            value = read_weekly_limit(command)
            result = {"state": "ready" if value else "unavailable",
                      "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
            if value:
                result.update(value)
        except Exception:
            with self._lock:
                self._error = True
                self._running = False
            return
        with self._lock:
            if value is None and self._result is not None and "remaining_percent" in self._result:
                self._error = True
            else:
                self._result = result
                self._error = False
            self._running = False
