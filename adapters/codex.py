"""Codex CLI adapter.

Reads `~/.codex/sessions/YYYY/MM/DD/rollout-<iso>-<uuid>.jsonl`. Same
one-object-per-line shape as Claude and the same top-level `timestamp` field
name, but the conversation lives in a different place — and, as it turns out,
in two different places depending on how the session was started.

## The two dialogue streams

Codex records the same conversation twice at different altitudes: a low-level
`response_item` stream (raw model I/O, including every prompt the harness
injects) and a high-level `event_msg` stream (what a human would call the
conversation). The high-level stream is the right input — it is already
stripped of harness scaffolding — but it comes in two dialects:

- `codex_exec` sessions emit `event_msg` / `user_message` + `agent_message`,
  with the text directly on `payload.message`.
- `codex-tui` sessions emit `event_msg` / `item_completed`, with the text in
  `payload.item.content[].text` and the kind in `payload.item.type`
  (`UserMessage` / `AgentMessage`).

Across the 127 sessions on this machine the two are perfectly disjoint: 123
files use the first dialect, 4 use the second, none use both. So the adapter
reads whichever it finds, per record, with no risk of double-counting.

Reading only the first dialect — which is what the format survey originally
described — silently yields a **zero-turn conversation** for every TUI
session, including the richest codex session on disk (10,571 records, 74 real
user turns). That is the documented silent-drop failure mode, so both dialects
are tested.

## What is dropped, and why

Stripping is per-vendor. Claude's noise arrives as tags injected into
otherwise-real turns, so it is removed with a regex. Codex's noise arrives as
whole records, so it is dropped by record type — there is no tag regex here,
and adding one would be wrong: the `<...>` strings that appear in codex user
text are placeholders in prose (`<sha>`, `<job>`, `<seat>`), not wrappers.

| Dropped | Why |
|---|---|
| `response_item` / `message` (all roles) | The low-altitude duplicate of the kept stream. `developer` role is skills instructions and plugin catalogues; `user` role includes `<environment_context>` and the exec harness prompt. Keeping it would double every turn and bury the dialogue. |
| `response_item` / `reasoning` | `encrypted_content` with no readable text — codex's equivalent of a thinking block. |
| `event_msg` / `item_completed` where item is `Reasoning` | Same content at the other altitude. |
| `*_output` records (`custom_tool_call_output`, `function_call_output`) | Tool results, the bulk of the bytes. |
| `event_msg` bookkeeping (`token_count`, `task_started`, `task_complete`, `thread_settings_applied`, `turn_aborted`, `web_search_end`, `patch_apply_end`, `context_compacted`, `mcp_tool_call_end`, `thread_rolled_back`) | Telemetry and lifecycle. |
| `world_state`, `turn_context`, `compacted`, `inter_agent_communication_metadata` | Harness state snapshots. |
| `response_item` / `agent_message` | Subagent→parent routing envelopes, largely encrypted. Not the same thing as `event_msg` / `agent_message` despite the name. |

Tool calls are dropped as *content* but kept as a *signal*: they set
`had_tool_use` on the turn so the rendered block carries its `[L:N]` ref back
to the source line, exactly as for Claude.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Iterator

from tools.memory_config import memory_root

from .base import SessionMeta, SessionRef, Turn, project_from_cwd

CLIENT = "codex"

# Foreign session ids are prefixed so one flat transcripts/ directory cannot
# silently overwrite a Claude session, and so provenance is visible in every
# downstream listing. Deliberately not `agent-` or `audit-`, which are already
# load-bearing filters in conversations.py and process_transcripts.py.
ID_PREFIX = "codex-"

SESSIONS_DIR = Path.home() / ".codex" / "sessions"
ARCHIVE_DIR = memory_root() / "transcripts"

# rollout-2026-08-13T18-25-55-019ffa3a-5670-7b12-8895-334229d49024.jsonl
_ROLLOUT_RE = re.compile(
    r"^rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-(?P<uuid>[0-9a-fA-F-]{36})$"
)

# Records that mean "the model called a tool here". Content is dropped; the
# line number survives as the block's [L:N] ref.
_TOOL_CALL_TYPES = {"custom_tool_call", "function_call", "web_search_call", "local_shell_call"}
_TOOL_ITEM_TYPES = {"CommandExecution", "FileChange", "CollabAgentToolCall", "Extension", "WebSearch"}


def client_name() -> str:
    return CLIENT


def session_id_for(raw_id: str) -> str:
    """Prefix a codex thread id, idempotently."""
    return raw_id if raw_id.startswith(ID_PREFIX) else f"{ID_PREFIX}{raw_id}"


def _clean_text(text: str) -> str:
    """Whitespace normalisation only.

    No tag stripping: codex's harness noise is dropped by record type above,
    and the angle brackets that survive into kept text are prose.
    """
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _content_text(content) -> str:
    """Pull readable text out of a codex content list.

    Block `type` varies by dialect and by codex version — `input_text`,
    `output_text`, `text`, `Text` have all been observed — so this takes any
    block carrying a `text` string rather than allow-listing type names, and
    skips `encrypted_content` blocks, which have none.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text)
    return "\n".join(parts)


def ref_for_path(path: Path, session_id: str | None = None) -> SessionRef:
    """Build a ref from a rollout file. The id comes from the filename."""
    path = Path(path)
    if session_id is None:
        match = _ROLLOUT_RE.match(path.stem)
        # A file that does not match the rollout convention still gets a
        # usable id rather than being dropped — discovery counts and reports
        # such files instead of pretending they do not exist.
        session_id = session_id_for(match.group("uuid") if match else path.stem)
    return SessionRef(session_id=session_id, path=path, client=CLIENT)


def discover() -> list[SessionRef]:
    """Every codex session on disk, oldest path first.

    Sorted by path, which for `YYYY/MM/DD/rollout-<iso>-...` is chronological.
    Unreadable directories are skipped rather than raised: discovery that dies
    on one bad entry reports zero sessions, which is worse than reporting the
    rest.
    """
    if not SESSIONS_DIR.exists():
        return []
    try:
        paths = sorted(SESSIONS_DIR.rglob("rollout-*.jsonl"))
    except OSError:
        return []

    refs: list[SessionRef] = []
    seen: set[str] = set()
    for path in paths:
        if not path.is_file():
            continue
        ref = ref_for_path(path)
        # Same thread id in two files would be a silent overwrite in the flat
        # archive dir. Keep the first and let the caller notice the count gap.
        if ref.session_id in seen:
            continue
        seen.add(ref.session_id)
        refs.append(ref)
    return refs


def _archive_path(session_id: str) -> str:
    return f"~/.claude/memory/transcripts/{session_id}.jsonl"


def _parse(ref: SessionRef) -> tuple[SessionMeta, list[Turn]]:
    """One streaming pass. Never slurps: codex sessions reach 36 MB."""
    meta = SessionMeta(
        session_id=ref.session_id,
        client=CLIENT,
        raw=_archive_path(ref.session_id),
        raw_source=str(ref.path),
    )

    turns: list[Turn] = []
    first_ts = ""
    last_ts = ""
    pending_tool_line: int | None = None
    dialects: set[str] = set()

    def add(role: str, ts: str, text: str, line_num: int) -> None:
        nonlocal pending_tool_line
        cleaned = _clean_text(text)
        if not cleaned:
            return
        turns.append(Turn(role, ts, cleaned, raw_line=line_num))
        if role == "user":
            # A user turn is where the renderer closes the assistant block, so
            # it is also where the next block's [L:N] starts looking again.
            pending_tool_line = None

    try:
        handle = open(ref.path, "r", errors="replace")
    except OSError:
        # A session whose file cannot be read still yields meta, so the caller
        # can report it rather than crash mid-discovery.
        return meta, []

    with handle as f:
        for line_num, line in enumerate(f, 1):
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(entry, dict):
                continue

            ts = entry.get("timestamp") or ""
            if ts:
                if not first_ts:
                    first_ts = ts
                last_ts = ts

            etype = entry.get("type")
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                payload = {}
            ptype = payload.get("type")

            if etype == "session_meta":
                # Several files carry a second session_meta (a forked or
                # spawned thread). The first one is the session's own.
                if not meta.cwd and payload.get("cwd"):
                    meta.cwd = payload["cwd"]
                    meta.project = project_from_cwd(meta.cwd)
                if not meta.parent_session_id and payload.get("parent_thread_id"):
                    meta.parent_session_id = session_id_for(payload["parent_thread_id"])
                # A spawned subagent thread is captured inside its parent, the
                # same way Claude's `agent-` transcripts are.
                if payload.get("agent_path") or payload.get("parent_thread_id"):
                    meta.is_subagent = True
                continue

            if etype != "event_msg":
                # Everything else is response_item / world_state / turn_context
                # noise, except that a tool call marks the line its block
                # should point at.
                if etype == "response_item" and ptype in _TOOL_CALL_TYPES and pending_tool_line is None:
                    pending_tool_line = line_num
                    turns.append(Turn("assistant", ts, "", had_tool_use=True, raw_line=line_num))
                continue

            # -- the kept stream --------------------------------------------
            if ptype == "user_message":
                dialects.add("event_msg")
                add("user", ts, payload.get("message") or "", line_num)
            elif ptype == "agent_message":
                dialects.add("event_msg")
                add("assistant", ts, payload.get("message") or "", line_num)
            elif ptype == "item_completed":
                item = payload.get("item")
                if not isinstance(item, dict):
                    continue
                itype = item.get("type")
                if itype == "UserMessage":
                    dialects.add("item_completed")
                    add("user", ts, _content_text(item.get("content")), line_num)
                elif itype == "AgentMessage":
                    dialects.add("item_completed")
                    add("assistant", ts, _content_text(item.get("content")), line_num)
                elif itype in _TOOL_ITEM_TYPES and pending_tool_line is None:
                    pending_tool_line = line_num
                    turns.append(Turn("assistant", ts, "", had_tool_use=True, raw_line=line_num))

    meta.started = first_ts
    meta.ended = last_ts

    # Across the 127 sessions on this machine the two dialects are perfectly
    # disjoint per file — 123 event_msg, 4 item_completed, no overlap — which
    # is what makes reading both safe rather than double-counting. If a future
    # codex version ever emits both in one session, that assumption is dead and
    # the turns above may be duplicated. Both are kept, because dropping half a
    # conversation to protect an invariant is the worse failure, but it says so
    # loudly rather than quietly returning a doubled transcript.
    if len(dialects) > 1:
        note = (f"{ref.session_id}: both codex dialogue dialects present "
                f"({', '.join(sorted(dialects))}); turns from both were kept and may "
                f"be duplicated — the disjointness this adapter relies on no longer holds")
        meta.notes.append(note)
        print(f"WARNING: {note}", file=sys.stderr)

    return meta, turns


_CACHE: tuple[tuple, tuple[SessionMeta, list[Turn]]] | None = None


def _cache_key(ref: SessionRef) -> tuple | None:
    try:
        st = ref.path.stat()
    except OSError:
        return None
    return (str(ref.path), st.st_mtime_ns, st.st_size)


def parse(ref: SessionRef) -> tuple[SessionMeta, list[Turn]]:
    """Meta and turns together — one read when a caller wants both."""
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
