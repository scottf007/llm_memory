"""Read session metadata from ~/.claude/memory/conversations/ frontmatter.

Replaces the old session_log SQL table. Each conversation.md file carries a YAML
frontmatter block with `session_id`, `project`, `started`, `ended`, etc. Those
files are the session registry.
"""
from __future__ import annotations

import pathlib
import re
from typing import Iterator

from tools.memory_config import memory_root

CONV_DIR = memory_root() / "conversations"

_FRONTMATTER_FIELD = re.compile(r"^(?P<key>[a-z_]+):\s*(?P<value>.+?)\s*$", re.MULTILINE)


def _parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}
    block = text[3:end]
    return {m.group("key"): m.group("value") for m in _FRONTMATTER_FIELD.finditer(block)}


def iter_sessions(conv_dir: pathlib.Path | None = None) -> Iterator[dict[str, str]]:
    """Yield a frontmatter dict per main-session conversation.md file.

    Skips agent-* and audit-* stems so callers don't have to filter.
    """
    d = conv_dir or CONV_DIR
    if not d.exists():
        return
    for md in sorted(d.glob("*.md")):
        sid = md.stem
        if sid.startswith(("agent-", "audit-")):
            continue
        try:
            text = md.read_text(errors="ignore")
        except OSError:
            continue
        fm = _parse_frontmatter(text)
        if not fm:
            continue
        fm.setdefault("session_id", sid)
        yield fm


def list_sessions(project: str, conv_dir: pathlib.Path | None = None) -> list[str]:
    """Return session_ids whose frontmatter project matches."""
    return [fm["session_id"] for fm in iter_sessions(conv_dir) if fm.get("project") == project]


def list_projects(conv_dir: pathlib.Path | None = None) -> list[str]:
    """Return unique project names across all main-session conversation files."""
    seen = {fm.get("project") for fm in iter_sessions(conv_dir)}
    return sorted(p for p in seen if p)


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 1:
        for p in list_projects():
            print(p)
    else:
        for sid in list_sessions(sys.argv[1]):
            print(sid)
