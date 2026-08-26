"""Claude Code adapter.

Reads `~/.claude/projects/<encoded-cwd>/<sid>.jsonl` and the archived copies in
`~/.claude/memory/transcripts/`. One JSON object per line; `type` is `user`,
`assistant`, or plumbing we ignore. Everything Claude-specific lives here —
the noise tags it wraps injected context in, the `message.content` block
shapes, the `agent-` session-id convention for subagents.

This module was `extract_conversation.py` before the adapter split; that file
is now a thin CLI/compat shim over these functions. Parsing behaviour is
unchanged and pinned byte-for-byte by tests/test_adapter_oracle.py.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterator

from tools.memory_config import memory_root

from .base import SessionMeta, SessionRef, Turn, project_from_cwd

CLIENT = "claude"

PROJECTS_DIR = Path.home() / ".claude" / "projects"
ARCHIVE_DIR = memory_root() / "transcripts"

# Claude Code injects context into the user turn wrapped in these tags. It is
# machinery, not conversation, and it would swamp the extracted text.
_NOISE_TAG_RE = re.compile(
    r"<(?:ide_opened_file|local-command-caveat|command-me|system-reminder|command-name|"
    r"command-message|command-args|local-command-stdout|user-prompt-submit-hook|"
    r"available-deferred-tools|fast_mode_info)>.*?</(?:ide_opened_file|local-command-caveat|"
    r"command-me|system-reminder|command-name|command-message|command-args|"
    r"local-command-stdout|user-prompt-submit-hook|available-deferred-tools|fast_mode_info)>",
    re.DOTALL,
)


def client_name() -> str:
    return CLIENT


def _clean_text(text: str) -> str:
    text = _NOISE_TAG_RE.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_user_text(entry: dict) -> str:
    msg = entry.get("message", {})
    content = msg.get("content", "")
    if isinstance(content, str):
        return _clean_text(content)
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", "").strip())
            elif isinstance(block, str):
                parts.append(block.strip())
        return _clean_text(" ".join(p for p in parts if p))
    return ""


def _assistant_entry_parts(entry: dict) -> tuple[str, bool]:
    """Return (text, has_tool_use).

    has_tool_use distinguishes "this assistant entry invoked a tool" from
    "this entry was text/thinking only." Only tool_use triggers a [L:N] ref
    on the merged block — thinking blocks get dropped quietly, since the
    ref is meant to point at external side effects the reader might want
    to inspect, not at Claude's inner monologue.
    """
    msg = entry.get("message", {})
    content = msg.get("content", [])
    has_tool_use = False
    if isinstance(content, str):
        return _clean_text(content), False
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                btype = block.get("type")
                if btype == "text":
                    text = block.get("text", "").strip()
                    if text:
                        parts.append(text)
                elif btype == "tool_use":
                    has_tool_use = True
        return _clean_text(" ".join(parts)), has_tool_use
    return "", False


def ref_for_path(path: Path) -> SessionRef:
    """Build a ref from a transcript path. The session id is the file stem."""
    path = Path(path)
    return SessionRef(session_id=path.stem, path=path, client=CLIENT)


def _archive_path(session_id: str) -> str:
    # Recorded verbatim in frontmatter — the tilde form is what every existing
    # conversation .md carries, so it stays a literal, not an expanded path.
    return f"~/.claude/memory/transcripts/{session_id}.jsonl"


def discover() -> list[SessionRef]:
    """Every Claude session on disk, live projects first then the archive.

    A session that exists in both places is yielded once, pointing at the live
    file — that copy is the one still growing.
    """
    seen: set[str] = set()
    refs: list[SessionRef] = []

    if PROJECTS_DIR.exists():
        for project_dir in sorted(PROJECTS_DIR.iterdir()):
            if not project_dir.is_dir():
                continue
            for jsonl in sorted(project_dir.glob("*.jsonl")):
                if jsonl.stem not in seen:
                    seen.add(jsonl.stem)
                    refs.append(ref_for_path(jsonl))

    if ARCHIVE_DIR.exists():
        for jsonl in sorted(ARCHIVE_DIR.glob("*.jsonl")):
            if jsonl.stem not in seen:
                seen.add(jsonl.stem)
                refs.append(ref_for_path(jsonl))

    return refs


def _parse(ref: SessionRef) -> tuple[SessionMeta, list[Turn]]:
    """Single pass over the transcript producing both meta and turns.

    Session-level fields come from *any* entry that carries them, not only
    conversational ones: project attribution in particular often arrives on a
    plumbing entry. That is why this is one scan rather than two.
    """
    session_id = ref.session_id
    meta = SessionMeta(
        session_id=session_id,
        client=CLIENT,
        raw=_archive_path(session_id),
    )

    # Subagent transcripts are captured inside the parent session; nothing is
    # read from the file itself.
    if session_id.startswith("agent-"):
        meta.is_subagent = True
        return meta, []

    turns: list[Turn] = []
    first_ts = ""
    last_ts = ""

    with open(ref.path, "r", errors="replace") as f:
        for line_num, line in enumerate(f, 1):
            try:
                entry = json.loads(line.strip())
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(entry, dict):
                continue

            ts = entry.get("timestamp") or ""
            if ts:
                if not first_ts:
                    first_ts = ts
                last_ts = ts

            if not meta.project:
                cwd = entry.get("cwd") or ""
                if cwd:
                    meta.project = project_from_cwd(cwd)

            if not meta.parent_session_id and entry.get("parentSessionId"):
                meta.parent_session_id = entry["parentSessionId"]

            entry_type = entry.get("type")
            if entry_type == "user":
                text = _extract_user_text(entry)
                if text:
                    turns.append(Turn("user", ts, text, raw_line=line_num))
            elif entry_type == "assistant":
                text, has_tool_use = _assistant_entry_parts(entry)
                if text or has_tool_use:
                    turns.append(Turn("assistant", ts, text, has_tool_use, line_num))

    meta.started = first_ts
    meta.ended = last_ts
    return meta, turns


# One-entry memo so session_meta() + turns() on the same file costs one read.
# Keyed on identity *and* content stamp: transcripts grow while a session is
# live, and a stale cache there would silently truncate a conversation.
_CACHE: tuple[tuple, tuple[SessionMeta, list[Turn]]] | None = None


def _cache_key(ref: SessionRef) -> tuple | None:
    try:
        st = ref.path.stat()
    except OSError:
        return None
    return (str(ref.path), st.st_mtime_ns, st.st_size)


def parse(ref: SessionRef) -> tuple[SessionMeta, list[Turn]]:
    """Meta and turns together — the efficient path when you want both."""
    global _CACHE
    key = _cache_key(ref)
    if key is not None and _CACHE is not None and _CACHE[0] == key:
        return _CACHE[1]
    result = _parse(ref)
    if key is not None:
        _CACHE = (key, result)
    return result


def session_meta(ref: SessionRef) -> SessionMeta:
    return parse(ref)[0]


def turns(ref: SessionRef) -> Iterator[Turn]:
    return iter(parse(ref)[1])
