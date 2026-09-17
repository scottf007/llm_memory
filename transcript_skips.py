"""Durable ingest decisions for non-narrative foreign sessions.

The normal transcript archive and conversation registry intentionally contain
only material that can reach the narrative pipeline.  This module keeps the
opposite decision separately, keyed by session id and by the mutable source
file version that was inspected.  Coverage can then discard an already-known
noise session without reopening its archived JSONL body.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_SKIP_INDEX = "transcript-skips.json"
_ELIGIBLE_STATE = "transcript-eligible-sources.json"


def source_version(path: Path) -> dict[str, int | str] | None:
    """Stable identity/version fields for a source file, or ``None`` on error."""
    try:
        resolved = Path(path).resolve()
        stat = resolved.stat()
    except OSError:
        return None
    return {
        "path": str(resolved),
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
    }


def _path(root: Path, filename: str) -> Path:
    return Path(root) / filename


def _read(root: Path, filename: str) -> dict[str, Any]:
    try:
        value = json.loads(_path(root, filename).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {"version": 1, "entries": {}}
    if not isinstance(value, dict) or not isinstance(value.get("entries"), dict):
        return {"version": 1, "entries": {}}
    return value


def _write(root: Path, filename: str, value: dict[str, Any]) -> None:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    destination = _path(root, filename)
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.write_text(
        json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(destination)


def record_skip(
    root: Path,
    session_id: str,
    client: str,
    version: dict[str, int | str] | None,
    reason: str,
) -> None:
    """Persist an ineligible decision for exactly one source version."""
    index = _read(root, _SKIP_INDEX)
    index["entries"][session_id] = {
        "session_id": session_id,
        "client": client,
        "source_version": version,
        "reason": reason,
    }
    _write(root, _SKIP_INDEX, index)


def applicable_skip(
    root: Path,
    session_id: str,
    client: str,
    version: dict[str, int | str] | None,
) -> dict[str, Any] | None:
    """Return the current-source skip entry, never one for an old version."""
    entry = _read(root, _SKIP_INDEX)["entries"].get(session_id)
    if not isinstance(entry, dict):
        return None
    if entry.get("client") != client or entry.get("source_version") != version:
        return None
    return entry


def clear_skip(root: Path, session_id: str) -> None:
    """Remove a superseded skip decision after the source becomes eligible."""
    index = _read(root, _SKIP_INDEX)
    if session_id not in index["entries"]:
        return
    del index["entries"][session_id]
    _write(root, _SKIP_INDEX, index)


def skipped_session_ids(root: Path) -> set[str]:
    """All durably excluded archive ids, without reading archive bodies.

    Ingest clears an entry before restoring an eligible source to the active
    archive.  Coverage therefore need not inspect the mutable foreign source
    just to apply this set difference.
    """
    return set(_read(root, _SKIP_INDEX)["entries"])


def mark_eligible(
    root: Path,
    session_id: str,
    client: str,
    version: dict[str, int | str] | None,
) -> None:
    """Record that an eligible source version has already been ingested."""
    state = _read(root, _ELIGIBLE_STATE)
    state["entries"][session_id] = {
        "session_id": session_id,
        "client": client,
        "source_version": version,
    }
    _write(root, _ELIGIBLE_STATE, state)


def eligible_source_is_current(
    root: Path,
    session_id: str,
    client: str,
    version: dict[str, int | str] | None,
) -> bool:
    """Whether normal archival already handled this exact source version."""
    entry = _read(root, _ELIGIBLE_STATE)["entries"].get(session_id)
    return bool(
        isinstance(entry, dict)
        and entry.get("client") == client
        and entry.get("source_version") == version
    )


def clear_eligible(root: Path, session_id: str) -> None:
    """Remove an old eligible stamp when the same source becomes noise."""
    state = _read(root, _ELIGIBLE_STATE)
    if session_id not in state["entries"]:
        return
    del state["entries"][session_id]
    _write(root, _ELIGIBLE_STATE, state)
