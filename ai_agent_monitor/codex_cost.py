"""Optional, local Codex token usage estimate at current Standard API rates.

This is an API-equivalent reference value, never a ChatGPT or API invoice.
Only token usage metadata is cached; session messages are not retained.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

PRICING_AS_OF = "2026-09-28"
PRICING_URL = "https://developers.openai.com/api/docs/pricing"
LONG_CONTEXT_INPUT_TOKENS = 272_000
RATES = {
    "gpt-5.6-luna": ("0.20", "0.02", "1.20"),
    "gpt-5.6-terra": ("2.00", "0.20", "12.00"),
    "gpt-5.6-sol": ("4.00", "0.40", "20.00"),
    "gpt-6-luna": ("0.10", "0.01", "0.50"),
    "gpt-6-sol": ("2.00", "0.20", "10.00"),
    "gpt-6-astra": ("10.00", "1.00", "50.00"),
}


def _same_project(cwd: str | None, root: Path) -> bool:
    if not isinstance(cwd, str) or not cwd:
        return False
    current = os.path.normcase(os.path.normpath(cwd)).casefold()
    project = os.path.normcase(os.path.normpath(str(root))).casefold()
    return current == project or current.startswith(project + os.sep)


def _file_belongs_to_project(path: Path, root: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            first = json.loads(stream.readline())
    except (OSError, ValueError):
        return False
    return first.get("type") == "session_meta" and _same_project(
        (first.get("payload") or {}).get("cwd"), root
    )


def _scan_file(path: Path, root: Path) -> list[dict]:
    records = []
    model = None
    cwd = None
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            if not any(name in line for name in (
                '"session_meta"', '"turn_context"', '"token_usage_record"'
            )):
                continue
            try:
                event = json.loads(line)
            except ValueError:  # A session may be actively writing its final line.
                continue
            kind = event.get("type")
            payload = event.get("payload") or {}
            if kind == "session_meta":
                cwd = payload.get("cwd")
            elif kind == "turn_context":
                cwd = payload.get("cwd") or cwd
                model = payload.get("model") or model
            elif kind == "token_usage_record" and _same_project(cwd, root):
                usage = payload.get("usage") or {}
                response_id = payload.get("response_id")
                if not isinstance(response_id, str) or not response_id:
                    continue
                try:
                    input_tokens = int(usage.get("input_tokens") or 0)
                    cached_tokens = int(usage.get("cached_input_tokens") or 0)
                    write_tokens = int(usage.get("cache_write_input_tokens") or 0)
                    output_tokens = int(usage.get("output_tokens") or 0)
                except (TypeError, ValueError):
                    continue
                if (min(input_tokens, cached_tokens, write_tokens, output_tokens) < 0
                        or cached_tokens > input_tokens):
                    continue
                records.append({
                    "id": response_id,
                    "model": model,
                    "input": input_tokens,
                    "cached": cached_tokens,
                    "write": write_tokens,
                    "output": output_tokens,
                    "timestamp": event.get("timestamp"),
                })
    return records


def _load_cache(path: Path, root: Path, sessions_root: Path) -> dict:
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if (cache.get("schema_version") != 1
            or cache.get("project_root") != str(root)
            or cache.get("sessions_root") != str(sessions_root)):
        return {}
    return cache.get("files") if isinstance(cache.get("files"), dict) else {}


def _save_cache(path: Path, root: Path, sessions_root: Path, files: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps({
        "schema_version": 1,
        "project_root": str(root),
        "sessions_root": str(sessions_root),
        "files": files,
    }, ensure_ascii=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix="codex-cost-",
        suffix=".tmp", delete=False,
    ) as stream:
        temporary = Path(stream.name)
        stream.write(content)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _record_cost(record: dict) -> Decimal | None:
    prices = RATES.get(record.get("model"))
    if prices is None:
        return None
    input_rate, cached_rate, output_rate = map(Decimal, prices)
    long_context = record["input"] > LONG_CONTEXT_INPUT_TOKENS
    input_multiplier = 2 if long_context else 1
    output_multiplier = Decimal("1.5") if long_context else Decimal(1)
    return (
        Decimal(record["input"] - record["cached"]) * input_rate * input_multiplier
        + Decimal(record["cached"]) * cached_rate * input_multiplier
        + Decimal(record["write"]) * input_rate * Decimal("1.25") * input_multiplier
        + Decimal(record["output"]) * output_rate * output_multiplier
    ) / Decimal(1_000_000)


def estimate(
    root: Path, sessions_root: Path, cache_path: Path
) -> dict:
    """Refresh changed session files and return the recorded-token reference value."""
    root = root.resolve()
    sessions_root = sessions_root.resolve()
    previous = _load_cache(cache_path, root, sessions_root)
    files = {}
    if sessions_root.is_dir():
        for path in sessions_root.rglob("*.jsonl"):
            if not _file_belongs_to_project(path, root):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            key = str(path)
            prior = previous.get(key)
            if (isinstance(prior, dict)
                    and prior.get("size") == stat.st_size
                    and prior.get("mtime_ns") == stat.st_mtime_ns
                    and isinstance(prior.get("records"), list)):
                files[key] = prior
                continue
            try:
                records = _scan_file(path, root)
            except OSError:  # A session file may disappear during rotation.
                continue
            files[key] = {
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "records": records,
            }
    _save_cache(cache_path, root, sessions_root, files)

    seen = set()
    total = Decimal(0)
    priced = 0
    excluded = {}
    timestamps = []
    for file in files.values():
        for record in file["records"]:
            if record["id"] in seen:
                continue
            seen.add(record["id"])
            cost = _record_cost(record)
            if cost is None:
                name = record.get("model") or "unknown"
                excluded[name] = excluded.get(name, 0) + 1
                continue
            total += cost
            priced += 1
            if isinstance(record.get("timestamp"), str):
                timestamps.append(record["timestamp"])
    return {
        "state": "ready",
        "amount_usd": str(total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "priced_responses": priced,
        "excluded_responses": sum(excluded.values()),
        "excluded_models": excluded,
        "first_recorded_at": min(timestamps) if timestamps else None,
        "last_recorded_at": max(timestamps) if timestamps else None,
        "calculated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "pricing_as_of": PRICING_AS_OF,
        "pricing_url": PRICING_URL,
        "source": "local_codex_token_usage_records",
    }


def enabled(config_path: Path) -> bool:
    try:
        return json.loads(config_path.read_text(encoding="utf-8")).get("enabled") is True
    except (OSError, ValueError, AttributeError):
        return False


def set_enabled(config_path: Path, value: bool) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({"enabled": value}) + "\n", encoding="utf-8")


class CostEstimateManager:
    """Run file scans away from HTTP request threads and throttle refreshes."""

    def __init__(self, refresh_seconds: int = 30) -> None:
        self.refresh_seconds = refresh_seconds
        self._lock = threading.Lock()
        self._result: dict | None = None
        self._error = False
        self._running = False
        self._last_start = 0.0

    def snapshot(
        self, config_path: Path, root: Path, sessions_root: Path, cache_path: Path
    ) -> dict:
        if not enabled(config_path):
            return {"enabled": False}
        with self._lock:
            if (not self._running
                    and (self._last_start == 0
                         or time.monotonic() - self._last_start >= self.refresh_seconds)):
                self._running = True
                self._last_start = time.monotonic()
                threading.Thread(
                    target=self._refresh,
                    args=(root, sessions_root, cache_path),
                    daemon=True,
                ).start()
            if self._result is not None:
                return {"enabled": True, **self._result,
                        "state": "updating" if self._running else "ready"}
            return {"enabled": True,
                    "state": "error" if self._error and not self._running else "calculating"}

    def _refresh(self, root: Path, sessions_root: Path, cache_path: Path) -> None:
        try:
            result = estimate(root, sessions_root, cache_path)
        except Exception:  # Keep the dashboard responsive if a local log is damaged.
            with self._lock:
                self._error = True
                self._running = False
            return
        with self._lock:
            self._result = result
            self._error = False
            self._running = False
