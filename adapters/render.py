"""Shared renderer: SessionMeta + Turn[] -> the conversations/<sid>.md contract.

This is the one place the `.md` format is defined. Adapters parse; this
renders. The format is load-bearing — `conversations.py` reads its
frontmatter as the session registry, and the delta-extractor agent reads its
body — so the shape below is a contract, not a preference:

    ---
    session_id: <sid>
    project: <name>          (omitted when unattributed)
    client: <client>
    raw: transcripts/<sid>.jsonl
    turns: <user turn count>
    started: <iso>           (omitted when unknown)
    ended: <iso>             (omitted when unknown)
    ---

    === user 2026-08-17T01:02:03.000Z ===
    <text>

    === assistant 2026-08-17T01:02:09.000Z [L:412] ===
    <text>

Consecutive assistant entries are merged into one block per turn, headed by
the timestamp of the first entry that carried text and tagged with the source
line of the first tool call in the group. Grouping lives here rather than in
each adapter so every client gets identical block structure.

`tests/test_adapter_oracle.py` pins this against real stored conversations:
re-rendering must reproduce them byte for byte, modulo the `client:` line.
"""

from __future__ import annotations

from typing import Iterable

from .base import SessionMeta, Turn

SUBAGENT_NOTE = (
    "_This is a subagent session. Full conversation is in the parent "
    "session's transcript; raw JSONL is preserved at the path above "
    "for inspection._"
)


def _frontmatter(lines: list[str]) -> str:
    return "\n".join(["---", *lines, "---"])


def render_subagent(meta: SessionMeta) -> str:
    """Stub for a subagent session.

    Subagent transcripts don't get their own extraction: their content is
    already inside the parent session's transcript, and the raw JSONL stays on
    disk for inspection. The stub exists so the file is present and self
    explanatory rather than missing.
    """
    fm = _frontmatter([
        f"session_id: {meta.session_id}",
        f"client: {meta.client}",
        "agent_session: true",
        "skipped: true",
        f"raw: {meta.raw}",
    ])
    return f"{fm}\n\n{SUBAGENT_NOTE}\n"


def render_conversation(meta: SessionMeta, turns: Iterable[Turn]) -> str:
    """Render a full session. `turns` is consumed once, in order."""
    if meta.is_subagent:
        return render_subagent(meta)

    blocks: list[str] = []
    user_turns = 0

    # Buffer for grouping consecutive assistant entries into one block.
    asst_texts: list[str] = []
    asst_first_ts = ""
    asst_tool_line: int | None = None

    def flush_assistant() -> None:
        nonlocal asst_texts, asst_first_ts, asst_tool_line
        if asst_texts:
            ref = f" [L:{asst_tool_line}]" if asst_tool_line is not None else ""
            header = f"=== assistant {asst_first_ts}{ref} ===".rstrip()
            blocks.append(f"{header}\n" + "\n\n".join(asst_texts))
        asst_texts = []
        asst_first_ts = ""
        asst_tool_line = None

    for turn in turns:
        if turn.role == "user":
            if not turn.text:
                # Synthetic user entries (tool results and the like) carry no
                # prose and must not break the assistant grouping.
                continue
            flush_assistant()
            user_turns += 1
            header = f"=== user {turn.timestamp} ===".rstrip()
            blocks.append(f"{header}\n{turn.text}")
        elif turn.role == "assistant":
            if turn.text:
                if not asst_first_ts:
                    asst_first_ts = turn.timestamp
                asst_texts.append(turn.text)
            if turn.had_tool_use and asst_tool_line is None and turn.raw_line is not None:
                asst_tool_line = turn.raw_line

    flush_assistant()

    fm_lines = [f"session_id: {meta.session_id}"]
    if meta.project:
        fm_lines.append(f"project: {meta.project}")
    fm_lines.append(f"client: {meta.client}")
    fm_lines.append(f"raw: {meta.raw}")
    fm_lines.append(f"turns: {user_turns}")
    if meta.started:
        fm_lines.append(f"started: {meta.started}")
    if meta.ended:
        fm_lines.append(f"ended: {meta.ended}")
    if meta.parent_session_id:
        fm_lines.append(f"parent_session_id: {meta.parent_session_id}")
    if meta.raw_source:
        fm_lines.append(f"raw_source: {meta.raw_source}")
    for key, value in meta.extra.items():
        fm_lines.append(f"{key}: {value}")

    body = "\n\n".join(blocks)
    return _frontmatter(fm_lines) + "\n\n" + body + "\n"
