"""Shared filesystem configuration for llm_memory."""

from __future__ import annotations

import os
from pathlib import Path


MEMORY_HOME_ENV = "LLM_MEMORY_HOME"

#: The store root. ~/.claude/memory was the original location and is retired:
#: the store is not Claude-specific (codex and grok sessions ingest into it
#: too), and burying it under another tool's config directory made it look
#: like Claude Code state. Machines that moved kept a ~/.llm-memory symlink
#: for compatibility; that symlink is not required and should not be relied on.
DEFAULT_MEMORY_ROOT_NAME = ".llm-memory"

#: Retired location, retained ONLY so callers can detect and report a machine
#: that never moved. Nothing should read or write through it.
LEGACY_MEMORY_ROOT = (".claude", "memory")


def memory_root() -> Path:
    """Return the configured memory store root.

    Resolve on every call so tests, wrappers, and long-lived processes can
    relocate the store without relying on module import order.
    """
    configured = os.environ.get(MEMORY_HOME_ENV)
    if configured:
        return Path(configured)
    return Path.home() / DEFAULT_MEMORY_ROOT_NAME


def legacy_memory_root() -> Path:
    """The retired ~/.claude/memory path, for migration checks only."""
    return Path.home().joinpath(*LEGACY_MEMORY_ROOT)
