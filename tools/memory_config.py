"""Shared filesystem configuration for llm_memory."""

from __future__ import annotations

import os
from pathlib import Path


MEMORY_HOME_ENV = "LLM_MEMORY_HOME"


def memory_root() -> Path:
    """Return the configured memory store root.

    Resolve on every call so tests, wrappers, and long-lived processes can
    relocate the store without relying on module import order. An unset or
    empty variable preserves the historical location.
    """
    configured = os.environ.get(MEMORY_HOME_ENV)
    if configured:
        return Path(configured)
    return Path.home() / ".claude" / "memory"
