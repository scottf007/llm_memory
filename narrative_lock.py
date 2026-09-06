"""Shared, non-blocking per-project lock for narrative mutations."""

from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path


class NarrativeLockBusy(RuntimeError):
    """Raised when another narrative transaction owns a project lock."""


def _path(home: Path, project: str) -> Path:
    return Path(home) / "runtime" / "locks" / "narrative" / f"{project}.lock"


def inherited_lock(home: Path, project: str):
    """Return an inherited lock descriptor only when it is the same inode.

    The worker keeps its lock while using in-process merger/render operations.
    A named environment variable alone is never sufficient authority.
    """
    value = os.environ.get("LLM_MEMORY_NARRATIVE_LOCK", "")
    try:
        named_project, raw_fd = value.rsplit(":", 1)
        fd = int(raw_fd)
    except ValueError as exc:
        raise RuntimeError("no valid inherited narrative lock") from exc
    if named_project != project:
        raise RuntimeError("inherited lock is for another project")
    target = _path(home, project)
    try:
        held, wanted = os.fstat(fd), target.stat()
    except OSError as exc:
        raise RuntimeError("inherited lock is unavailable") from exc
    if (held.st_dev, held.st_ino) != (wanted.st_dev, wanted.st_ino):
        raise RuntimeError("inherited lock does not identify project lock")
    return fd


@contextmanager
def project_lock(home: Path, project: str, *, wait: bool = False):
    """Acquire ``runtime/locks/narrative/{project}.lock`` with flock."""
    path = _path(home, project)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        flags = fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB
        try:
            fcntl.flock(fd, flags)
        except BlockingIOError as exc:
            raise NarrativeLockBusy(project) from exc
        yield fd
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
