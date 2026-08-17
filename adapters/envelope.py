"""The canonical envelope: a Claude-shaped JSONL written from any client's turns.

`server.py` has three raw-JSONL readers — `_count_substantive_user_turns`,
`_transcript_tail_ts` and `_find_project_transcripts` — and all three assume
Claude's record shape. There were two ways to make a foreign session visible
to them: teach the readers every client format, or have each adapter emit a
minimal Claude-shaped file. The second keeps client knowledge inside adapters,
which is the whole point of the layer, and costs one duplicated artefact on
disk. The client's original file is never moved or touched; its path is
recorded in the conversation frontmatter as `raw_source:`.

The failure mode this exists to prevent is silent. A session whose envelope
`_count_substantive_user_turns` reads as zero is filtered out of
`narrative_coverage` with no error and no log line — the narrative simply has
a hole in it that nobody can see. So `verify_envelope()` is not optional
politeness: it re-reads what was written, with the same rules the server uses,
and every adapter's tests run it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Sequence

from .base import SessionMeta, Turn


def envelope_records(meta: SessionMeta, turns: Sequence[Turn]) -> list[dict]:
    """Turns -> Claude-shaped records.

    `cwd` carries the project so `_find_project_transcripts` and the extractor
    agree on attribution even if the conversation .md is ever regenerated from
    the envelope rather than from the client's own file.
    """
    cwd = meta.cwd or (f"/projects/{meta.project}" if meta.project else "")
    records: list[dict] = []
    for turn in turns:
        if not turn.text:
            # Tool-only markers carry no prose. They exist for the [L:N] ref in
            # the .md and would only add noise here.
            continue
        record: dict = {
            "type": turn.role,
            "timestamp": turn.timestamp,
            "sessionId": meta.session_id,
            "client": meta.client,
        }
        if cwd:
            record["cwd"] = cwd
        if turn.role == "user":
            # A plain string, not a block list: `_count_substantive_user_turns`
            # discards turns whose content is exclusively `tool_result` blocks,
            # and a string can never look like one.
            record["message"] = {"role": "user", "content": turn.text}
        else:
            record["message"] = {
                "role": "assistant",
                "content": [{"type": "text", "text": turn.text}],
            }
        records.append(record)
    return records


def render_envelope(meta: SessionMeta, turns: Sequence[Turn]) -> str:
    """The envelope as text. ASCII-escaped, because the server opens these
    with `encoding="utf-8"` and no error handler."""
    return "".join(
        json.dumps(rec, ensure_ascii=True) + "\n"
        for rec in envelope_records(meta, turns)
    )


def write_envelope(meta: SessionMeta, turns: Sequence[Turn], dest_dir: Path) -> Path:
    """Write `<dest_dir>/<session_id>.jsonl` and return its path."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{meta.session_id}.jsonl"
    dest.write_text(render_envelope(meta, turns), encoding="utf-8")
    return dest


def count_user_turns(path: Path, cap: int = 1000) -> int:
    """Count user turns the way `server.py` counts them.

    Deliberately a re-implementation of `_count_substantive_user_turns` rather
    than an import: `server.py` pulls in `mcp`, which is installed only in the
    lib venv, so importing it would make this check unrunnable exactly where
    it is most useful. The rules are copied verbatim and a test pins the two
    against each other.
    """
    if cap <= 0:
        return 0
    n = 0
    try:
        with open(path, encoding="utf-8") as fp:
            for line in fp:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "user" or rec.get("isSidechain"):
                    continue
                msg = rec.get("message") or {}
                if msg.get("role") != "user":
                    continue
                content = msg.get("content")
                if isinstance(content, list) and content and all(
                    isinstance(c, dict) and c.get("type") == "tool_result"
                    for c in content
                ):
                    continue
                n += 1
                if n >= cap:
                    return n
    except OSError:
        return cap
    return n


def verify_envelope(path: Path, expected_user_turns: int) -> tuple[bool, str]:
    """Re-read a written envelope and confirm the server will see its turns.

    Returns (ok, detail). `expected_user_turns` is what the adapter parsed; a
    session with genuine user turns whose envelope reads as zero is the named
    silent-drop failure, and is what this catches.
    """
    counted = count_user_turns(Path(path))
    if expected_user_turns > 0 and counted == 0:
        return False, (
            f"{Path(path).name}: adapter found {expected_user_turns} user turn(s) but the "
            f"envelope reads as 0 — this session would be dropped from narrative_coverage "
            f"with no error"
        )
    if counted != expected_user_turns:
        return False, (
            f"{Path(path).name}: envelope reads {counted} user turn(s), adapter found "
            f"{expected_user_turns}"
        )
    return True, f"{Path(path).name}: {counted} user turn(s) visible to the server"


def user_turn_count(turns: Iterable[Turn]) -> int:
    return sum(1 for t in turns if t.role == "user" and t.text)
