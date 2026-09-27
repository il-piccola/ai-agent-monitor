"""Project-local registry of other Monitor dashboards.

The registry reads public Monitor HTTP APIs. It never opens another project's
database or runtime files.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


SCHEMA_VERSION = 1
MAX_RESPONSE_BYTES = 1024 * 1024
FETCH_TIMEOUT_SECONDS = 2
MAX_PROJECTS = 50
PROJECT_ID_PATTERN = re.compile(r"[0-9a-f]{16}\Z")


def normalize_dashboard_url(value: str) -> str:
    """Accept an HTTPS dashboard or a loopback HTTP dashboard root."""
    value = value.strip()
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid dashboard URL.") from exc

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not hostname:
        raise ValueError("Dashboard URL must use HTTP or HTTPS and have a host.")
    if parsed.username or parsed.password or parsed.path not in {"", "/"}:
        raise ValueError("Dashboard URL must be the site root without credentials.")
    if parsed.query or parsed.fragment:
        raise ValueError("Dashboard URL must not contain a query or fragment.")
    if scheme == "http":
        try:
            loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            loopback = hostname.lower() == "localhost"
        if not loopback:
            raise ValueError("Plain HTTP is allowed only for loopback dashboards.")

    host = f"[{hostname}]" if ":" in hostname else hostname.lower()
    authority = f"{host}:{port}" if port is not None else host
    return urlunsplit((scheme, authority, "", "", ""))


def _fetch_json(base_url: str, endpoint: str) -> dict[str, object]:
    request = urllib.request.Request(
        f"{base_url}{endpoint}", headers={"Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
        if response.status != 200:
            raise ValueError("Monitor API did not return HTTP 200.")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Monitor API response is too large.")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Monitor API response is not a JSON object.")
    return payload


def _identity(payload: dict[str, object]) -> tuple[str, str]:
    project_id = payload.get("project_id")
    name = payload.get("name")
    if not isinstance(project_id, str) or not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise ValueError("Monitor API returned an invalid project ID.")
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise ValueError("Monitor API returned an invalid project name.")
    return project_id, name.strip()


def load_entries(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Registry has an unsupported schema version.")
    entries = payload.get("projects")
    if not isinstance(entries, list) or len(entries) > MAX_PROJECTS:
        raise ValueError("Registry project list is invalid.")
    result = []
    seen_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Registry project entry is invalid.")
        project_id, name = _identity(entry)
        url = entry.get("dashboard_url")
        if not isinstance(url, str) or normalize_dashboard_url(url) != url:
            raise ValueError("Registry dashboard URL is invalid.")
        if project_id in seen_ids:
            raise ValueError("Registry contains a duplicate project ID.")
        seen_ids.add(project_id)
        result.append({"project_id": project_id, "name": name, "dashboard_url": url})
    return result


def _save_entries(path: Path, entries: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=".registry-", suffix=".tmp", delete=False,
        ) as output:
            temporary = Path(output.name)
            json.dump({"schema_version": SCHEMA_VERSION, "projects": entries},
                      output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def add_project(path: Path, dashboard_url: str) -> dict[str, str]:
    url = normalize_dashboard_url(dashboard_url)
    project_id, name = _identity(_fetch_json(url, "/api/project"))
    entries = load_entries(path)
    if any(e["dashboard_url"] == url and e["project_id"] != project_id for e in entries):
        raise ValueError("That URL is registered for a different project ID.")
    entry = {"project_id": project_id, "name": name, "dashboard_url": url}
    for index, existing in enumerate(entries):
        if existing["project_id"] == project_id:
            entries[index] = entry
            break
    else:
        if len(entries) >= MAX_PROJECTS:
            raise ValueError("Registry is full.")
        entries.append(entry)
    _save_entries(path, entries)
    return entry


def remove_project(path: Path, project_id: str) -> bool:
    entries = load_entries(path)
    remaining = [entry for entry in entries if entry["project_id"] != project_id]
    if len(remaining) == len(entries):
        return False
    _save_entries(path, remaining)
    return True


def _last_activity(status: dict[str, object]) -> str | None:
    timestamps: list[str] = []
    for singular, field in (("current_task", "started_at"),
                            ("latest_artifact", "created_at")):
        item = status.get(singular)
        if isinstance(item, dict) and isinstance(item.get(field), str):
            timestamps.append(item[field])
    for plural, field in (("recent_progress", "created_at"),
                          ("open_questions", "created_at"),
                          ("recent_answers", "answered_at"),
                          ("metrics", "updated_at")):
        items = status.get(plural)
        if isinstance(items, list):
            timestamps.extend(item[field] for item in items
                              if isinstance(item, dict) and isinstance(item.get(field), str))
    return max(timestamps, default=None)


def _legacy_status(url: str) -> dict[str, object]:
    project = _fetch_json(url, "/api/project")
    task = _fetch_json(url, "/api/task")
    questions = _fetch_json(url, "/api/questions")
    progress = _fetch_json(url, "/api/progress")
    answers = _fetch_json(url, "/api/answers")
    return {
        "project": project,
        "current_task": task.get("task"),
        "open_questions": questions.get("questions"),
        "recent_progress": progress.get("progress"),
        "recent_answers": answers.get("answers"),
    }


def _probe(entry: dict[str, str]) -> dict[str, object]:
    result: dict[str, object] = {
        **entry, "available": False, "health": "unavailable",
        "current_task": None, "unanswered_count": None, "last_activity": None,
    }
    url = entry["dashboard_url"]
    try:
        try:
            status = _fetch_json(url, "/api/status")
            if status.get("schema_version") != 1:
                raise ValueError("Unsupported Monitor status schema.")
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            status = _legacy_status(url)
        project = status.get("project")
        if not isinstance(project, dict) or project.get("project_id") != entry["project_id"]:
            result["health"] = "identity_mismatch"
            return result
        task = status.get("current_task")
        questions = status.get("open_questions")
        if task is not None and not isinstance(task, dict):
            raise ValueError("Invalid task response.")
        if not isinstance(questions, list):
            raise ValueError("Invalid questions response.")
        result.update({
            "available": True,
            "health": "ok",
            "current_task": task.get("title") if task else None,
            "unanswered_count": len(questions),
            "last_activity": _last_activity(status),
        })
    except (OSError, ValueError, json.JSONDecodeError, UnicodeError, urllib.error.URLError):
        pass
    return result


def snapshot(path: Path) -> dict[str, object]:
    entries = load_entries(path)
    if entries:
        with ThreadPoolExecutor(max_workers=min(8, len(entries))) as pool:
            projects = list(pool.map(_probe, entries))
    else:
        projects = []
    return {
        "schema_version": SCHEMA_VERSION,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "projects": projects,
    }
