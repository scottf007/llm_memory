"""Tests for the per-client narrative_coverage filter in server.py.

Item 2 of the post-ingest slice, amended (board event 6a853f48) from a flat
codex threshold to a three-part filter, because a blanket codex min_user_turns
of 1 ingests 84 noise sessions (the codex-auto board-polling harness) and a
flat 5 throws away real signal (single-prompt design-council rounds):

1. A structural pre-filter drops any session whose first user turn is the
   codex-auto harness's fixed instruction preamble, regardless of turn count
   or what it replied — its reply is either NO_REPLY or already posted to the
   board verbatim by construction.
2. Per-client min_user_turns (claude: 5, codex: 1), read from the `client:`
   conversation frontmatter S1 added.
3. A minimum assistant-content gate (~50 chars) for whatever remains, so a
   bare PONG/exit/id reply doesn't count as coverage.

`_find_project_transcripts` resolves its live/archive directories via
`Path.home()`, not `server.DB_DIR` — so both HOME and DB_DIR are pointed at
the same sandbox to keep them in sync.
"""

from __future__ import annotations

import json

import pytest

import server

# The literal preamble the multi-agent board's codex-auto harness sends as
# the first user turn of every session it launches (verified against real
# ~/.codex sessions containing "NO_REPLY" while building item 1's fixtures).
CODEX_AUTO_PREAMBLE = (
    "You are the managed `codex-auto` participant in a live multi-AI project "
    "discussion.\n\nRead AGENTS.md and any project files needed. Reply with "
    "your view, or output exactly `NO_REPLY` if nothing is needed.\n\n"
    "CURRENT EVENT\nid: 01786596448703901385-codex-root-c6d592a3\n"
    "from: codex-root\nto: all\nkind: decision\n"
)

DESIGN_COUNCIL_PROMPT = (
    "Given the latency/consistency tradeoff on this path, I'd favor eventual "
    "consistency: the write volume is low enough that a reconciliation pass "
    "once a minute is cheaper than paying synchronous replication on every "
    "write, and nothing here reads its own write within that window."
)

SUBSTANTIVE_REPLY = (
    "Agreed on eventual consistency for this path. One addition: the "
    "reconciliation pass needs to be idempotent, because a crash mid-pass "
    "and a retry must not double-apply a write. I'll add a version check."
)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    home = tmp_path / "home"
    memory = home / ".claude" / "memory"
    (memory / "transcripts").mkdir(parents=True)
    (memory / "conversations").mkdir(parents=True)
    (memory / "projects").mkdir(parents=True)
    (memory / "projects" / "demo.json").write_text(json.dumps({"sessions": []}))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(server, "DB_DIR", memory)
    return memory


def _write_session(memory, sid, project, client, turns):
    """turns: list of (role, text) tuples, in order."""
    records = []
    for i, (role, text) in enumerate(turns):
        records.append({
            "type": role,
            "timestamp": f"2026-01-01T00:{i:02d}:00.000Z",
            "message": {"role": role, "content": text},
        })
    transcript = memory / "transcripts" / f"{sid}.jsonl"
    transcript.write_text("".join(json.dumps(r) + "\n" for r in records))
    (memory / "conversations" / f"{sid}.md").write_text(
        f"---\nsession_id: {sid}\nproject: {project}\nclient: {client}\n"
        f"started: 2026-01-01T00:00:00Z\nended: 2026-01-01T00:10:00Z\n---\n\n"
        f"body\n"
    )
    return transcript


def _coverage(project="demo", **kwargs):
    args = {"project": project, **kwargs}
    result = server._handle_narrative_coverage(args)
    return json.loads(result[0].text)


# --------------------------------------------------------------------------
# (1) The codex-auto structural pre-filter
# --------------------------------------------------------------------------

def test_codex_auto_no_reply_session_excluded(sandbox):
    """The overwhelming majority case: NO_REPLY, nothing to extract."""
    _write_session(sandbox, "codex-auto-noreply", "demo", "codex",
                    [("user", CODEX_AUTO_PREAMBLE), ("assistant", "NO_REPLY")])
    result = _coverage()
    assert result["unprocessed"] == []
    assert result["skipped_codex_auto_count"] == 1


def test_codex_auto_session_with_substantive_reply_excluded(sandbox):
    """Redundant by construction: the reply is already on the board verbatim,
    so a long, content-gate-passing reply must still be excluded — the
    structural filter runs before, and independently of, the content gate."""
    _write_session(sandbox, "codex-auto-substantive", "demo", "codex",
                    [("user", CODEX_AUTO_PREAMBLE), ("assistant", SUBSTANTIVE_REPLY)])
    result = _coverage()
    assert result["unprocessed"] == []
    assert result["skipped_codex_auto_count"] == 1
    assert result["skipped_low_turn_count"] == 0
    assert result["skipped_low_content_count"] == 0


# --------------------------------------------------------------------------
# (2) Per-client min_user_turns
# --------------------------------------------------------------------------

def test_one_turn_design_council_session_included(sandbox):
    """Non-trigger control for both the harness filter and the turn
    threshold: a real one-turn codex session — not the harness pattern,
    substantive content — must reach the narrative at codex's threshold
    of 1, where a flat threshold of 5 would have dropped it."""
    transcript = _write_session(sandbox, "codex-council", "demo", "codex",
                                 [("user", DESIGN_COUNCIL_PROMPT),
                                  ("assistant", SUBSTANTIVE_REPLY)])
    result = _coverage()
    assert str(transcript) in result["unprocessed"]
    assert result["skipped_codex_auto_count"] == 0
    assert result["skipped_low_turn_count"] == 0
    assert result["skipped_low_content_count"] == 0


def test_one_turn_claude_session_still_excluded(sandbox):
    """Claude keeps its default of 5 — the per-client map must not leak
    codex's threshold onto other clients."""
    _write_session(sandbox, "claude-onlyturn", "demo", "claude",
                    [("user", DESIGN_COUNCIL_PROMPT),
                     ("assistant", SUBSTANTIVE_REPLY)])
    result = _coverage()
    assert result["unprocessed"] == []
    assert result["skipped_low_turn_count"] == 1
    assert result["min_user_turns_by_client"] == {"claude": 5, "codex": 1}


def test_five_turn_claude_session_included(sandbox):
    """Non-trigger control: a claude session that actually clears the
    default-5 bar is not collateral damage from the per-client change."""
    turns = []
    for i in range(5):
        turns.append(("user", f"turn {i}: {DESIGN_COUNCIL_PROMPT}"))
        turns.append(("assistant", SUBSTANTIVE_REPLY))
    transcript = _write_session(sandbox, "claude-fiveturn", "demo", "claude", turns)
    result = _coverage()
    assert str(transcript) in result["unprocessed"]


# --------------------------------------------------------------------------
# (3) Minimum assistant-content gate
# --------------------------------------------------------------------------

def test_pong_excluded(sandbox):
    """A bare health-check reply must not count as narrative-worthy coverage,
    even once it clears the (low, codex) turn threshold."""
    _write_session(sandbox, "codex-pong", "demo", "codex",
                    [("user", DESIGN_COUNCIL_PROMPT), ("assistant", "PONG")])
    result = _coverage()
    assert result["unprocessed"] == []
    assert result["skipped_low_content_count"] == 1
    assert result["skipped_low_turn_count"] == 0


# --------------------------------------------------------------------------
# Explicit override still applies uniformly (back-compat)
# --------------------------------------------------------------------------

def test_explicit_min_user_turns_overrides_the_per_client_map(sandbox):
    _write_session(sandbox, "codex-council2", "demo", "codex",
                    [("user", DESIGN_COUNCIL_PROMPT), ("assistant", SUBSTANTIVE_REPLY)])
    result = _coverage(min_user_turns=5)
    assert result["unprocessed"] == []
    assert result["skipped_low_turn_count"] == 1
    assert result["min_user_turns_by_client"] == {"claude": 5, "codex": 5}
