"""Shared loading and persistence for split project-state ledgers.

The primary ``{project}.json`` file contains metadata, sessions, and active
ledger items. Archived ledger items live in ``{project}.archived.json``.
Legacy single-file states remain readable until the next successful write
splits them.
"""

from __future__ import annotations

import errno
import json
import os
import tempfile
from pathlib import Path
from typing import Callable

try:
    from tools.memory_config import memory_root
except ModuleNotFoundError:  # Direct import from the tools directory.
    from memory_config import memory_root


LEDGER_KEYS = ("decisions", "goals", "suggestions", "learnings", "done")
AtomicWriter = Callable[[Path, dict], None]


def _project_paths(project: str, projects_dir: Path | None = None) -> tuple[Path, Path]:
    base = Path(projects_dir) if projects_dir is not None else memory_root() / "projects"
    return base / f"{project}.json", base / f"{project}.archived.json"


def _read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_active(project: str, projects_dir: Path | None = None) -> dict:
    """Load project metadata and active ledger items only.

    Filtering on read makes an unsplit legacy file immediately useful to the
    normal narrative pipeline without mutating it. The first later write is
    the migration step.
    """
    active_path, _ = _project_paths(project, projects_dir)
    state = _read_json(active_path)
    for kind in LEDGER_KEYS:
        if kind in state:
            state[kind] = [
                item for item in (state.get(kind) or [])
                if not isinstance(item, dict) or item.get("status") != "archived"
            ]
    return state


def _merge_items(active_items: list, archived_items: list) -> list:
    """Merge one ledger array, preferring the archived copy on duplicate IDs."""
    merged = list(active_items)
    positions = {
        item.get("id"): index
        for index, item in enumerate(merged)
        if isinstance(item, dict) and item.get("id")
    }
    for item in archived_items:
        item_id = item.get("id") if isinstance(item, dict) else None
        if item_id and item_id in positions:
            merged[positions[item_id]] = item
        else:
            if item_id:
                positions[item_id] = len(merged)
            merged.append(item)
    return merged


def load_full(project: str, projects_dir: Path | None = None) -> dict:
    """Load active and archived state, with archived duplicates winning.

    If no archive sidecar exists, the primary file is treated as a legacy
    unsplit state and returned intact.
    """
    active_path, archived_path = _project_paths(project, projects_dir)
    state = _read_json(active_path)
    if not archived_path.exists():
        return state

    archived = _read_json(archived_path)
    for kind in LEDGER_KEYS:
        state[kind] = _merge_items(
            state.get(kind, []) or [], archived.get(kind, []) or []
        )
    return state


def _split_state(project: str, state: dict) -> tuple[dict, dict]:
    active = dict(state)
    archived: dict = {"project": state.get("project") or project}
    for kind in LEDGER_KEYS:
        items = state.get(kind, []) or []
        active[kind] = [
            item for item in items
            if not isinstance(item, dict) or item.get("status") != "archived"
        ]
        archived[kind] = [
            item for item in items
            if isinstance(item, dict) and item.get("status") == "archived"
        ]
    return active, archived


def _atomic_write_json(path: Path, value: dict) -> None:
    """Atomically replace one JSON file and fsync its parent directory."""
    data = json.dumps(value, indent=2)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    tmp_path = Path(tmp_name)
    try:
        if path.exists():
            os.chmod(tmp_path, path.stat().st_mode & 0o777)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        try:
            dirfd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
        except OSError as exc:
            if exc.errno not in (errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP):
                raise
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def write_full(
    project: str,
    state: dict,
    projects_dir: Path | None = None,
    *,
    atomic_write: AtomicWriter | None = None,
) -> None:
    """Split and atomically write a full state with no archival-loss window.

    The archived sidecar is replaced first. If a crash follows, a newly
    archived item is durable there while its previous primary-file copy still
    exists, so the inconsistency is duplication rather than loss. ``load_full``
    resolves that window monotonically in favour of the archived copy. The
    next successful call replaces the active file and self-heals the duplicate.
    """
    active_path, archived_path = _project_paths(project, projects_dir)
    active, archived = _split_state(project, state)
    writer = atomic_write or _atomic_write_json
    writer(archived_path, archived)
    writer(active_path, active)
