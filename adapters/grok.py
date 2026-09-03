"""Grok session adapter.

Grok stores one session per directory.  This adapter keeps only the dialogue
records that carry ``prompt_index`` and assistant prose, while deliberately
dropping the remaining record kinds:

| Dropped | Why |
| --- | --- |
| ``system`` / ``reasoning`` / ``tool_result`` | Preamble, encrypted thought, or tool output; none is dialogue. |
| user records without ``prompt_index`` | Startup context and harness records not selected as prompts. |
| ``backend_tool_call`` content | It is represented as a tool-use line marker, not prose. |

Forked ``subagent_resume`` sessions duplicate their parent's complete history.
Only a session named as another same-directory session's parent is superseded;
the chain tail remains a normal conversation.  Chat records have no timestamps:
the k-th retained user turn takes the k-th ``events.jsonl`` ``turn_started``
timestamp and assistants inherit it.  Missing or short events fall back to the
summary's ``created_at`` and record that observation in ``meta.notes``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import unquote

from .base import SessionMeta, SessionRef, Turn, archive_path, make_adapter_parser, project_from_cwd

CLIENT = "grok"
ID_PREFIX = "grok-"
SESSIONS_DIR = Path.home() / ".grok" / "sessions"


def client_name() -> str:
    return CLIENT


def session_id_for(raw_id: str) -> str:
    """Prefix a Grok session id, idempotently."""
    return raw_id if raw_id.startswith(ID_PREFIX) else f"{ID_PREFIX}{raw_id}"


def _clean_text(text: str) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if text.startswith("<user_query>") and text.endswith("</user_query>"):
        text = text[len("<user_query>"):-len("</user_query>")].strip()
    if text.startswith("<system-reminder>") and text.endswith("</system-reminder>"):
        text = text[len("<system-reminder>"):-len("</system-reminder>")].strip()
    return text


def _content_text(content: object) -> str:
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(parts)


_SUMMARY_CACHE: dict[Path, tuple[tuple[int, int], dict]] = {}


def _summary(path: Path) -> dict:
    summary_path = path / "summary.json"
    try:
        stat = summary_path.stat()
        key = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return {}
    cached = _SUMMARY_CACHE.get(path)
    if cached is not None and cached[0] == key:
        return cached[1]
    try:
        with summary_path.open(encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    result = value if isinstance(value, dict) else {}
    _SUMMARY_CACHE[path] = (key, result)
    return result


def _raw_id(ref: SessionRef) -> str:
    return ref.path.name


def ref_for_path(path: Path, session_id: str | None = None) -> SessionRef:
    """Build a session ref from a Grok session directory."""
    path = Path(path)
    return SessionRef(
        session_id=session_id or session_id_for(path.name), path=path, client=CLIENT,
    )


def source_path(ref: SessionRef) -> Path:
    """The mutable source file used by the incremental-sweep mtime check."""
    return ref.path / "chat_history.jsonl"


def is_superseded(ref: SessionRef) -> bool:
    """Whether this session is now a fork prefix owned by a later sibling."""
    return _is_superseded(ref)


def _created_sort_key(path: Path) -> tuple[str, int]:
    summary = _summary(path)
    created = str(summary.get("created_at") or "")
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = 0
    return created, mtime


def discover() -> list[SessionRef]:
    """Every Grok session directory, oldest first, with duplicate ids removed."""
    if not SESSIONS_DIR.exists():
        return []
    candidates: list[Path] = []
    try:
        project_dirs = list(SESSIONS_DIR.iterdir())
    except OSError:
        return []
    for project_dir in project_dirs:
        if not project_dir.is_dir():
            continue
        try:
            entries = project_dir.iterdir()
            for session_dir in entries:
                chat = session_dir / "chat_history.jsonl"
                if session_dir.is_dir() and chat.is_file():
                    candidates.append(session_dir)
        except OSError:
            continue

    refs: list[SessionRef] = []
    seen: set[str] = set()
    for path in sorted(candidates, key=_created_sort_key):
        ref = ref_for_path(path)
        if ref.session_id in seen:
            continue
        seen.add(ref.session_id)
        refs.append(ref)
    return refs


_SUPERSEDED_CACHE: dict[Path, tuple[int, set[str]]] = {}


def _is_superseded(ref: SessionRef) -> bool:
    """True when a sibling session names this session as its parent."""
    try:
        parent_dir = ref.path.parent
        directory_mtime = parent_dir.stat().st_mtime_ns
    except OSError:
        return False
    cached = _SUPERSEDED_CACHE.get(parent_dir)
    if cached is not None and cached[0] == directory_mtime:
        return _raw_id(ref) in cached[1]
    try:
        siblings = parent_dir.iterdir()
    except OSError:
        return False
    superseded: set[str] = set()
    for sibling in siblings:
        if sibling == ref.path or not sibling.is_dir():
            continue
        parent = _summary(sibling).get("parent_session_id")
        if isinstance(parent, str) and parent:
            superseded.add(parent)
    _SUPERSEDED_CACHE[parent_dir] = (directory_mtime, superseded)
    return _raw_id(ref) in superseded


def _relative_source(path: Path) -> str:
    """Keep fixture provenance portable while retaining absolute live paths."""
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _event_timestamps(path: Path) -> list[str]:
    values: list[str] = []
    try:
        handle = (path / "events.jsonl").open(encoding="utf-8", errors="replace")
    except OSError:
        return values
    with handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except (ValueError, json.JSONDecodeError):
                continue
            if not isinstance(entry, dict) or entry.get("type") != "turn_started":
                continue
            timestamp = entry.get("ts")
            if isinstance(timestamp, str) and timestamp:
                values.append(timestamp)
    return values


def _parse(ref: SessionRef) -> tuple[SessionMeta, list[Turn]]:
    summary = _summary(ref.path)
    info = summary.get("info") if isinstance(summary.get("info"), dict) else {}
    cwd = str(info.get("cwd") or unquote(ref.path.parent.name))
    started = str(summary.get("created_at") or "")
    ended = str(summary.get("last_active_at") or summary.get("updated_at") or "")
    parent = summary.get("parent_session_id")
    parent_id = session_id_for(parent) if isinstance(parent, str) and parent else None
    extra: dict[str, str] = {}
    if parent_id:
        extra["fork_of"] = parent_id
    for source, target in (
        ("current_model_id", "model"),
        ("generated_title", "title"),
        ("session_kind", "session_kind"),
        ("agent_name", "agent_name"),
    ):
        value = summary.get(source)
        if isinstance(value, str) and value:
            extra[target] = value
    meta = SessionMeta(
        session_id=ref.session_id,
        client=CLIENT,
        project=project_from_cwd(cwd),
        cwd=cwd,
        started=started,
        ended=ended,
        is_subagent=_is_superseded(ref),
        parent_session_id=parent_id,
        raw=archive_path(ref.session_id),
        raw_source=_relative_source(ref.path / "chat_history.jsonl"),
        extra=extra,
    )
    if meta.is_subagent:
        return meta, []

    event_timestamps = _event_timestamps(ref.path)
    fallback = started or ended
    current_timestamp = fallback
    next_user_index = 0
    used_fallback = False
    turns: list[Turn] = []
    try:
        handle = (ref.path / "chat_history.jsonl").open(encoding="utf-8", errors="replace")
    except OSError:
        return meta, turns
    with handle:
        for line_num, line in enumerate(handle, 1):
            try:
                entry = json.loads(line)
            except (ValueError, json.JSONDecodeError):
                continue
            if not isinstance(entry, dict):
                continue
            record_type = entry.get("type")
            if record_type == "user":
                if "prompt_index" not in entry:
                    continue
                timestamp = (event_timestamps[next_user_index]
                             if next_user_index < len(event_timestamps) else fallback)
                if next_user_index >= len(event_timestamps):
                    used_fallback = True
                next_user_index += 1
                current_timestamp = timestamp
                text = _clean_text(_content_text(entry.get("content")))
                if text:
                    turns.append(Turn("user", timestamp, text, raw_line=line_num))
            elif record_type == "assistant":
                text = _clean_text(str(entry.get("content") or ""))
                had_tool_use = bool(entry.get("tool_calls"))
                if text or had_tool_use:
                    turns.append(Turn("assistant", current_timestamp, text,
                                      had_tool_use=had_tool_use, raw_line=line_num))
            elif record_type == "backend_tool_call":
                turns.append(Turn("assistant", current_timestamp, "", had_tool_use=True,
                                  raw_line=line_num))
    if used_fallback:
        meta.notes.append(
            f"{ref.session_id}: events.jsonl missing or short; used summary created_at fallback"
        )
    return meta, turns


def cache_key(ref: SessionRef) -> tuple[object, ...] | None:
    """Stats for all Grok inputs that can alter a parsed session.

    The session directory itself is deliberately not the cache key: Grok
    appends to ``chat_history.jsonl`` in place, which need not touch the
    directory mtime. A missing history file returns no key so it is retried.
    """
    paths = (ref.path / "chat_history.jsonl", ref.path / "summary.json", ref.path / "events.jsonl")
    try:
        history_stat = paths[0].stat()
    except OSError:
        return None
    other_stats = []
    for path in paths[1:]:
        try:
            stat = path.stat()
        except OSError:
            other_stats.append(None)
        else:
            other_stats.append((stat.st_mtime_ns, stat.st_size))
    return str(ref.path), (history_stat.st_mtime_ns, history_stat.st_size), *other_stats


parse, session_meta, turns = make_adapter_parser(_parse, cache_key=cache_key)
