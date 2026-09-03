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
import asyncio
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

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


def test_claude_session_quoting_codex_auto_preamble_is_not_excluded(sandbox):
    turns = [("user", CODEX_AUTO_PREAMBLE), ("assistant", SUBSTANTIVE_REPLY)]
    for i in range(4):
        turns.extend([
            ("user", f"follow-up {i}: {DESIGN_COUNCIL_PROMPT}"),
            ("assistant", SUBSTANTIVE_REPLY),
        ])
    transcript = _write_session(sandbox, "claude-quotes-harness", "demo", "claude", turns)

    result = _coverage()

    assert str(transcript) in result["unprocessed"]
    assert result["skipped_codex_auto_count"] == 0


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
    assert result["min_user_turns_by_client"] == {"claude": 5, "codex": 1, "grok": 3}


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
    assert result["min_user_turns_by_client"] == {"claude": 5, "codex": 5, "grok": 5}


# --------------------------------------------------------------------------
# D7 (docs/design/grok-ingestion-2026-09-03.md): grok joins the per-client map
#
# Frozen RED on main: `_MIN_USER_TURNS_BY_CLIENT` and the hardcoded dict built
# inside `compute_narrative_coverage` (server.py ~263, ~589) list only
# "claude" and "codex" today. adapters.grok does not exist yet either, so
# these do not import it -- they pin the *server* behaviour a grok-aware
# server.py must have, independent of when the adapter itself lands.
#
# Amendment 3 (design note §7, A3.2): the flat D7 threshold of 1 (mirroring
# codex) let 52 keep-alive forks -- chain tails whose only real prompt is the
# seat's own scheduled-loop prompt -- reach narrative_coverage as unprocessed
# (feedback 01788396626574918910). grok:1 becomes grok:3; a two-turn tail is
# exactly the keep-alive-fork shape (D8 census, design note §7) and must be
# excluded, a three-turn one is real work and must still be included.
# --------------------------------------------------------------------------

def test_two_turn_grok_session_excluded_under_a3_threshold(sandbox):
    """A3.2 trigger: a keep-alive fork's shape is 1-2 real prompts (design
    note §7 census). At grok's amended threshold of 3, two turns must no
    longer reach narrative_coverage -- this is exactly the class of session
    feedback 01788396626574918910 reported as unprocessed noise."""
    turns = []
    for i in range(2):
        turns.append(("user", f"turn {i}: {DESIGN_COUNCIL_PROMPT}"))
        turns.append(("assistant", SUBSTANTIVE_REPLY))
    _write_session(sandbox, "grok-twoturn", "demo", "grok", turns)
    result = _coverage()
    assert result["unprocessed"] == []
    assert result["skipped_low_turn_count"] == 1


def test_three_turn_grok_session_included_under_a3_threshold(sandbox):
    """Non-trigger control: real seat work (design note §7 puts seat runs at
    5-10 prompts; three is the amended threshold's own boundary) must still
    clear the bar -- the amendment tightens keep-alive noise, not signal."""
    turns = []
    for i in range(3):
        turns.append(("user", f"turn {i}: {DESIGN_COUNCIL_PROMPT}"))
        turns.append(("assistant", SUBSTANTIVE_REPLY))
    transcript = _write_session(sandbox, "grok-threeturn", "demo", "grok", turns)
    result = _coverage()
    assert str(transcript) in result["unprocessed"]
    assert result["skipped_low_turn_count"] == 0


def test_min_user_turns_by_client_carries_grok(sandbox):
    _write_session(sandbox, "grok-anything", "demo", "grok",
                    [("user", DESIGN_COUNCIL_PROMPT), ("assistant", SUBSTANTIVE_REPLY)])
    result = _coverage()
    assert result["min_user_turns_by_client"].get("grok") == 3


def test_every_registered_adapter_has_an_explicit_threshold(sandbox):
    """Guards the next client, not just grok: a name present in
    `adapters.names()` with no entry here must not silently fall to the
    Claude default of 5."""
    import adapters
    _write_session(sandbox, "probe", "demo", "claude",
                    [("user", DESIGN_COUNCIL_PROMPT), ("assistant", SUBSTANTIVE_REPLY)])
    result = _coverage()
    for name in adapters.names():
        assert name in result["min_user_turns_by_client"], (
            f"{name!r} has no explicit min_user_turns entry — it would silently "
            f"inherit the claude default")


def test_explicit_override_still_applies_to_grok(sandbox):
    _write_session(sandbox, "grok-council2", "demo", "grok",
                    [("user", DESIGN_COUNCIL_PROMPT), ("assistant", SUBSTANTIVE_REPLY)])
    result = _coverage(min_user_turns=5)
    assert result["unprocessed"] == []
    assert result["skipped_low_turn_count"] == 1
    assert result["min_user_turns_by_client"].get("grok") == 5


# --------------------------------------------------------------------------
# A3.5 (docs/design/grok-ingestion-2026-09-03.md §7, subsection A3.5): the
# multi-agent board's own self-wake keep-alive loop is a harness pattern for
# grok exactly the way codex-auto is for codex -- a fixed recipe phrase in
# the first kept user message, structural rather than content- or
# threshold-based. Measured on the A3.1-A3.4 candidate: narrative_coverage
# still listed 44 finance_nexus grok transcripts that are all 10-prompt
# keep-alive forks the A3.2 threshold cannot see, because each turn is a
# real prompt_index record with no synthetic_reason -- the same shape as a
# working seat's own scheduled-task prompt.
# --------------------------------------------------------------------------

GROK_KEEPALIVE_TRIGGER = (
    "Self-wake keep-alive for seat demo-seat on job demo-job. Run ONLY:\n"
    "am sync --job demo-job --seat demo-seat --monitor\n"
    "am sync --job demo-job --seat demo-seat\n"
)

GROK_KEEPALIVE_TRIGGER_CASE = (
    "Self-wake KEEP-ALIVE FOR SEAT demo-seat on job demo-job. Run ONLY:\n"
    "am sync --job demo-job --seat demo-seat --monitor\n"
    "am sync --job demo-job --seat demo-seat\n"
)

GROK_WORK_LOOP_PROMPT = (
    "You are seat demo-seat on job demo-job. This is a Grok self-wake tick.\n\n"
    "Brief: three review comments are still open on the migration guide; "
    "read them and reply with concrete fixes for each before the next tick."
)


def _grok_session(sandbox, sid, first_turn_text, extra_turns=2):
    """A grok session whose first user turn is `first_turn_text`, with enough
    total substantive turns (1 + extra_turns == grok's threshold of 3) that
    the A3.2 turn threshold alone would not exclude it -- isolating whatever
    the A3.5 structural filter does from the threshold's own effect."""
    turns = [("user", first_turn_text), ("assistant", SUBSTANTIVE_REPLY)]
    for i in range(extra_turns):
        turns.append(("user", f"tick {i}: {DESIGN_COUNCIL_PROMPT}"))
        turns.append(("assistant", SUBSTANTIVE_REPLY))
    return _write_session(sandbox, sid, "demo", "grok", turns)


def test_grok_keepalive_session_excluded(sandbox):
    """A3.5 trigger: the fixed self-wake keep-alive recipe as the first user
    turn excludes the session even though it clears grok's turn threshold of
    3 on its own (1 trigger turn + 2 extra = 3 user turns), and the summary
    filter note names the exclusion."""
    _grok_session(sandbox, "grok-keepalive", GROK_KEEPALIVE_TRIGGER)
    result = _coverage()
    assert result["unprocessed"] == []
    assert result["skipped_grok_keepalive_count"] == 1
    assert result["skipped_low_turn_count"] == 0
    assert "1 grok-keepalive" in result["summary"]


def test_grok_keepalive_session_excluded_case_insensitive(sandbox):
    """A3.5 trigger, case: the plan requires case-insensitive matching on
    `keep-alive for seat`."""
    _grok_session(sandbox, "grok-keepalive-upper", GROK_KEEPALIVE_TRIGGER_CASE)
    result = _coverage()
    assert result["unprocessed"] == []
    assert result["skipped_grok_keepalive_count"] == 1


def test_grok_work_loop_session_included(sandbox):
    """Non-trigger control: a real seat's scheduled self-wake tick shares the
    board-harness scheduling shape but is not the keep-alive recipe -- it
    must still reach the narrative. GREEN on a5077b3 already (nothing yet
    excludes it); stays green once A3.5 lands."""
    transcript = _grok_session(sandbox, "grok-workloop", GROK_WORK_LOOP_PROMPT)
    result = _coverage()
    assert str(transcript) in result["unprocessed"]


def test_claude_session_quoting_keepalive_phrase_is_not_excluded(sandbox):
    """Non-trigger control, client scope: the marker check is grok-scoped,
    mirroring the codex-auto preamble's client scope -- a claude session
    that merely quotes the phrase is real material, not harness noise.
    GREEN on a5077b3 already; stays green once A3.5 lands."""
    turns = [("user", GROK_KEEPALIVE_TRIGGER), ("assistant", SUBSTANTIVE_REPLY)]
    for i in range(4):
        turns.extend([
            ("user", f"follow-up {i}: {DESIGN_COUNCIL_PROMPT}"),
            ("assistant", SUBSTANTIVE_REPLY),
        ])
    transcript = _write_session(sandbox, "claude-quotes-keepalive", "demo", "claude", turns)
    result = _coverage()
    assert str(transcript) in result["unprocessed"]


def test_codex_session_quoting_keepalive_phrase_is_not_excluded(sandbox):
    """Non-trigger control, client scope: same as above for codex, at
    codex's own threshold of 1. GREEN on a5077b3 already; stays green once
    A3.5 lands."""
    transcript = _write_session(sandbox, "codex-quotes-keepalive", "demo", "codex",
                                 [("user", GROK_KEEPALIVE_TRIGGER), ("assistant", SUBSTANTIVE_REPLY)])
    result = _coverage()
    assert str(transcript) in result["unprocessed"]


def test_skipped_grok_keepalive_count_present_and_zero_with_no_grok_sessions(sandbox):
    """Payload shape: the new key must exist at 0 even when nothing grok is
    present, and the pre-existing codex-auto behaviour must be untouched."""
    _write_session(sandbox, "codex-auto-noreply2", "demo", "codex",
                    [("user", CODEX_AUTO_PREAMBLE), ("assistant", "NO_REPLY")])
    result = _coverage()
    assert result["skipped_grok_keepalive_count"] == 0
    assert result["skipped_codex_auto_count"] == 1


def test_coverage_payload_exposes_skipped_id_lists(sandbox):
    """Judge finding F5 on amendment 3: the two count-only filters hardest to
    audit from outside (low-turn, and now grok-keepalive) must also expose
    the actual excluded paths, parallel to the existing count keys, so a
    human or PM can name what got dropped without re-deriving it. RED on
    a5077b3: neither `skipped_low_turn` nor `skipped_grok_keepalive` exists
    in the payload yet."""
    low_turn_transcript = _write_session(sandbox, "claude-onlyturn2", "demo", "claude",
                                          [("user", DESIGN_COUNCIL_PROMPT),
                                           ("assistant", SUBSTANTIVE_REPLY)])
    keepalive_transcript = _grok_session(sandbox, "grok-keepalive2", GROK_KEEPALIVE_TRIGGER)
    result = _coverage()

    assert "skipped_low_turn" in result
    assert isinstance(result["skipped_low_turn"], list)
    assert len(result["skipped_low_turn"]) == result["skipped_low_turn_count"]
    assert str(low_turn_transcript) in result["skipped_low_turn"]

    assert "skipped_grok_keepalive" in result
    assert isinstance(result["skipped_grok_keepalive"], list)
    assert len(result["skipped_grok_keepalive"]) == result["skipped_grok_keepalive_count"]
    assert str(keepalive_transcript) in result["skipped_grok_keepalive"]

    # Existing shape is unchanged: unprocessed and the other count keys are
    # still exactly what they were before this key was added.
    assert result["unprocessed"] == []
    assert isinstance(result["unprocessed"], list)
    assert all(isinstance(p, str) for p in result["unprocessed"])
    for key in ("skipped_subagent_count", "skipped_codex_auto_count",
                "skipped_low_turn_count", "skipped_low_content_count"):
        assert isinstance(result[key], int)


def test_narrative_coverage_tool_description_names_grok_keepalive_loops():
    """The public tool contract must expose its largest exclusion class."""
    tools = asyncio.run(server.list_tools())
    tool = next(tool for tool in tools if tool.name == "narrative_coverage")
    assert "Grok self-wake keep-alive loops" in tool.description
    assert "skipped_grok_keepalive" in tool.description


# --------------------------------------------------------------------------
# A3.2 acceptance -- real live store, read-only, skipped when the store is
# absent. Property assertions, not fixed counts: the store grows under us
# (other seats/board activity write to it concurrently), so pinning an exact
# unprocessed count would be flaky by construction.
# --------------------------------------------------------------------------

_LIVE_MEMORY_DIR = Path.home() / ".claude" / "memory"


def _kept_user_turn_count(transcript_path: str) -> int:
    """Count `type: user` records directly in the archived envelope .jsonl --
    independent of compute_narrative_coverage's own internals, since this is
    the property that's supposed to gate what compute_narrative_coverage
    lists, not a restatement of its implementation."""
    count = 0
    for line in Path(transcript_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("type") == "user":
            count += 1
    return count


def _assert_only_keepalive_recipes_are_skipped(result: dict) -> None:
    """The structural filter may exclude only its documented recipe shape."""
    unexpected = {
        path: _first_user_text_independent(path)
        for path in result["skipped_grok_keepalive"]
        if "keep-alive for seat" not in _first_user_text_independent(path).lower()
    }
    assert unexpected == {}, (
        "grok transcript(s) without the keep-alive recipe were filtered: "
        f"{unexpected}"
    )


def test_grok_keepalive_recipe_oracle_allows_structural_control(sandbox):
    """Non-trigger: the documented keep-alive recipe remains excludable."""
    _grok_session(sandbox, "grok-recipe-control", GROK_KEEPALIVE_TRIGGER)
    result = _coverage()

    _assert_only_keepalive_recipes_are_skipped(result)


def test_grok_keepalive_recipe_oracle_fires_on_overmatch(sandbox, monkeypatch):
    """Trigger: an over-broad marker predicate must expose real work."""
    _grok_session(sandbox, "grok-overmatch", GROK_WORK_LOOP_PROMPT)
    monkeypatch.setattr(server, "_is_grok_keepalive_loop", lambda _: True)
    result = _coverage()

    with pytest.raises(AssertionError, match="without the keep-alive recipe"):
        _assert_only_keepalive_recipes_are_skipped(result)


@pytest.mark.skipif(not _LIVE_MEMORY_DIR.exists(), reason="no live llm_memory store on this machine")
def test_live_finance_nexus_grok_unprocessed_all_clear_a3_threshold():
    """A3.2 acceptance (design note §7): finance_nexus is where the 52
    keep-alive-fork feedback (01788396626574918910) was reported. Every grok
    transcript compute_narrative_coverage still lists as unprocessed must
    have at least 3 kept user turns. The positive case is separately guarded
    by the filter-recipe trigger/control below, so an all-processed project is
    a valid live state rather than a test failure."""
    result = server.compute_narrative_coverage("finance_nexus")
    grok_paths = [p for p in result["unprocessed"] if Path(p).stem.startswith("grok-")]
    if not grok_paths and not result["skipped_grok_keepalive"]:
        pytest.skip("finance_nexus has no grok transcripts")
    counts = {p: _kept_user_turn_count(p) for p in grok_paths}
    under_threshold = {p: c for p, c in counts.items() if c < 3}
    assert under_threshold == {}, (
        f"grok transcript(s) listed as unprocessed below the A3.2 threshold of 3: {under_threshold}")
    _assert_only_keepalive_recipes_are_skipped(result)


@pytest.mark.skipif(not _LIVE_MEMORY_DIR.exists(), reason="no live llm_memory store on this machine")
def test_live_load_balancer_acceptance_session_not_regressed():
    """The owner's own load_balancer session (21 real prompts, far above any
    plausible threshold) must not be collateral damage from A3.2: it is
    either still correctly unprocessed, or was already merged into
    load_balancer.json by an earlier /narrative run -- either is fine, both
    absent would mean the amendment regressed real signal."""
    sid = "grok-01a05f66-e2bc-7332-8728-546c1a71e8cf"
    result = server.compute_narrative_coverage("load_balancer")
    in_unprocessed = any(Path(p).stem == sid for p in result["unprocessed"])
    state = server.load_active("load_balancer", server.DB_DIR / "projects")
    processed_ids = {s.get("session_id") for s in state.get("sessions", []) or []}
    assert in_unprocessed or sid in processed_ids, (
        f"{sid} is neither unprocessed nor merged for load_balancer")


# --------------------------------------------------------------------------
# A3.5 acceptance -- real live store, read-only, skipped when the store is
# absent. The marker check is on the first KEPT user message of the archived
# envelope, read independently of server._first_user_message_text here so
# the test isn't just restating the implementation it's meant to gate.
# --------------------------------------------------------------------------

def _first_user_text_independent(transcript_path: str) -> str:
    for line in Path(transcript_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("type") != "user":
            continue
        msg = record.get("message") or {}
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(c.get("text", "") for c in content
                            if isinstance(c, dict) and c.get("type") == "text")
        return ""
    return ""


@pytest.mark.skipif(not _LIVE_MEMORY_DIR.exists(), reason="no live llm_memory store on this machine")
def test_live_finance_nexus_excludes_grok_keepalive_forks():
    """A3.5 acceptance (design note §7): finance_nexus is where the 44
    ten-prompt keep-alive forks were measured. None of the grok transcripts
    compute_narrative_coverage still lists as unprocessed may carry the
    self-wake keep-alive recipe as its first kept user message."""
    result = server.compute_narrative_coverage("finance_nexus")
    grok_paths = [p for p in result["unprocessed"] if Path(p).stem.startswith("grok-")]
    if not grok_paths and not result["skipped_grok_keepalive"]:
        pytest.skip("finance_nexus has no grok transcripts")
    texts = {p: _first_user_text_independent(p) for p in grok_paths}
    marked = {p: t for p, t in texts.items() if "keep-alive for seat" in t.lower()}
    assert marked == {}, (
        f"grok transcript(s) listed as unprocessed carry the keep-alive marker: {list(marked)}")
    _assert_only_keepalive_recipes_are_skipped(result)


@pytest.mark.skipif(not _LIVE_MEMORY_DIR.exists(), reason="no live llm_memory store on this machine")
def test_live_no_project_lists_a_grok_keepalive_transcript():
    """A3.5 acceptance, store-wide: across every registered project, no grok
    transcript compute_narrative_coverage lists as unprocessed carries the
    keep-alive marker -- the filter is not finance_nexus-specific."""
    projects_dir = _LIVE_MEMORY_DIR / "projects"
    names = sorted({
        p.name[: -len(".json")]
        for p in projects_dir.glob("*.json")
        if not p.name.endswith((".archived.json", ".certificate.json", ".contested.json"))
    })
    if not names:
        pytest.skip("no registered projects in the live store")
    marked: dict[str, list[str]] = {}
    for name in names:
        result = server.compute_narrative_coverage(name)
        grok_paths = [p for p in result["unprocessed"] if Path(p).stem.startswith("grok-")]
        hits = [p for p in grok_paths if "keep-alive for seat" in _first_user_text_independent(p).lower()]
        if hits:
            marked[name] = hits
    assert marked == {}, f"project(s) list a grok keep-alive transcript as unprocessed: {marked}"


# --------------------------------------------------------------------------
# Roadmap #4: _stale_session + narrative_liveness
# --------------------------------------------------------------------------

def test_stale_session_reports_grown_transcript(sandbox):
    """Direct pin for _stale_session (UAI: UNTESTED_BUT_LIVE, only caller
    is _handle_narrative_coverage). A merged session whose transcript grew
    more than STALE_TAIL_HOURS after `ended` must be reported."""
    ended = datetime(2026, 8, 1, tzinfo=timezone.utc)
    last = ended + timedelta(days=13)
    _write_session(sandbox, "grown", "demo", "codex",
                    [("user", DESIGN_COUNCIL_PROMPT),
                     ("assistant", SUBSTANTIVE_REPLY)])
    # Overwrite timestamps: extractor recorded `ended`, tail is 13d later.
    transcript = sandbox / "transcripts" / "grown.jsonl"
    lines = []
    for rec in transcript.read_text().splitlines():
        obj = json.loads(rec)
        obj["timestamp"] = (
            ended.strftime("%Y-%m-%dT%H:%M:%SZ")
            if obj["type"] == "user"
            else last.strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        lines.append(json.dumps(obj))
    transcript.write_text("\n".join(lines) + "\n")

    info = server._stale_session({
        "session_id": "grown",
        "ended": ended.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "topic": "long run",
    })
    assert info is not None
    assert info["session_id"] == "grown"
    assert info["grew_days"] >= 12


def test_stale_session_ignores_closing_message_tail(sandbox):
    """Control: growth inside STALE_TAIL_HOURS is not a stale session."""
    ended = datetime(2026, 8, 1, tzinfo=timezone.utc)
    last = ended + timedelta(hours=3)
    _write_session(sandbox, "tail", "demo", "codex",
                    [("user", DESIGN_COUNCIL_PROMPT),
                     ("assistant", SUBSTANTIVE_REPLY)])
    transcript = sandbox / "transcripts" / "tail.jsonl"
    lines = []
    for rec in transcript.read_text().splitlines():
        obj = json.loads(rec)
        obj["timestamp"] = last.strftime("%Y-%m-%dT%H:%M:%SZ")
        lines.append(json.dumps(obj))
    transcript.write_text("\n".join(lines) + "\n")
    assert server._stale_session({
        "session_id": "tail",
        "ended": ended.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }) is None


def test_liveness_dormant_old_file_no_unprocessed():
    """PM-STATE §3: file age is not the measure."""
    now = datetime(2026, 8, 26, 5, 15, 36, tzinfo=timezone.utc)
    sig = server.narrative_liveness({
        "narrative_updated": "2026-08-10T05:15:36",
        "unprocessed_count": 0,
        "stale_count": 0,
        "stale": [],
    }, now=now)
    assert sig["aging"] is False
    assert sig["reason"] == "dormant"
    assert sig["narrative_age_days"] == 16


def test_liveness_unprocessed_aging():
    now = datetime(2026, 8, 26, 5, 15, 36, tzinfo=timezone.utc)
    sig = server.narrative_liveness({
        "narrative_updated": "2026-08-10T05:15:36",
        "unprocessed_count": 6,
        "unprocessed_last_activity": ["2026-08-10T05:15:36"],
        "stale_count": 0,
        "stale": [],
    }, now=now)
    assert sig["aging"] is True
    assert sig["reason"] == "unprocessed_aging"
    assert sig["signal_days"] == 16
    assert sig["threshold_days"] == server.NARRATIVE_LIVENESS_DAYS


def test_liveness_fresh_backlog_not_aging():
    now = datetime(2026, 8, 26, 12, 0, 0, tzinfo=timezone.utc)
    sig = server.narrative_liveness({
        "narrative_updated": "2026-08-25T12:00:00",
        "unprocessed_count": 3,
        "unprocessed_last_activity": ["2026-08-25T12:00:00"],
        "stale_count": 0,
        "stale": [],
    }, now=now)
    assert sig["aging"] is False
    assert sig["reason"] == "fresh_backlog"
    assert sig["narrative_age_days"] == 1


def test_liveness_stale_merged_aging():
    now = datetime(2026, 8, 26, tzinfo=timezone.utc)
    sig = server.narrative_liveness({
        "narrative_updated": "2026-08-25T12:00:00",  # recently rendered
        "unprocessed_count": 0,
        "stale_count": 1,
        "stale": [{
            "session_id": "long-run",
            "grew_days": 13.0,
            "last_activity": "2026-08-13T00:00:00Z",
        }],
    }, now=now)
    assert sig["aging"] is True
    assert sig["reason"] == "stale_merged_aging"
    assert sig["signal_days"] == 13


def test_liveness_fresh_tail_after_old_merge():
    """Reviewer adversarial #1: merge 20d ago, tail written now, grew_days=20.
    The missing content is fresh — wait is 0, must not fire."""
    now = datetime(2026, 8, 26, tzinfo=timezone.utc)
    sig = server.narrative_liveness({
        "narrative_updated": "2026-08-25T00:00:00",  # 1d-old narrative
        "unprocessed_count": 0,
        "stale_count": 1,
        "stale": [{
            "session_id": "fresh-tail",
            "grew_days": 20.0,
            "merged_through": "2026-08-06T00:00:00Z",
            "last_activity": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }],
    }, now=now)
    assert sig["aging"] is False
    assert sig["reason"] == "fresh_backlog"
    assert sig["signal_days"] is None


def test_liveness_old_wait_after_short_growth():
    """Reviewer adversarial #2: grew_days=1.5 (under the 7d proxy) but
    last_activity 16d ago. Content has been waiting 16 days — must fire."""
    now = datetime(2026, 8, 26, tzinfo=timezone.utc)
    last = now - timedelta(days=16)
    sig = server.narrative_liveness({
        "narrative_updated": "2026-08-25T00:00:00",
        "unprocessed_count": 0,
        "stale_count": 1,
        "stale": [{
            "session_id": "quiet-tail",
            "grew_days": 1.5,
            "merged_through": (last - timedelta(days=1.5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "last_activity": last.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }],
    }, now=now)
    assert sig["aging"] is True
    assert sig["reason"] == "stale_merged_aging"
    assert sig["signal_days"] == 16


def test_liveness_filtered_noise_does_not_age():
    """Low-turn / pong sessions are unprocessed_count=0 after coverage's
    filter — they must not trip the age alarm even if the file is old."""
    now = datetime(2026, 8, 26, tzinfo=timezone.utc)
    sig = server.narrative_liveness({
        "narrative_updated": "2026-08-10T05:15:36",
        "unprocessed_count": 0,
        "stale_count": 0,
        "stale": [],
        "skipped_low_turn_count": 64,
        "skipped_low_content_count": 1,
    }, now=now)
    assert sig["aging"] is False
    assert sig["reason"] == "dormant"


def test_compute_coverage_includes_stale_and_unprocessed(sandbox):
    """Hook helper must see the same filtered unprocessed set as the MCP tool."""
    _write_session(sandbox, "fresh", "demo", "codex",
                    [("user", DESIGN_COUNCIL_PROMPT),
                     ("assistant", SUBSTANTIVE_REPLY)])
    via_helper = server.compute_narrative_coverage("demo")
    via_mcp = _coverage()
    assert via_helper["unprocessed_count"] == via_mcp["unprocessed_count"]
    assert via_helper["unprocessed"] == via_mcp["unprocessed"]
    assert via_helper["stale_count"] == via_mcp["stale_count"]
    assert set(via_helper) == set(via_mcp)


def test_unprocessed_sorted_has_chronological_timestamped_paths(sandbox):
    """The skill queue is chronological without changing legacy paths."""
    early = _write_session(sandbox, "z-early", "demo", "codex",
                           [("user", DESIGN_COUNCIL_PROMPT),
                            ("assistant", SUBSTANTIVE_REPLY)])
    fallback = _write_session(sandbox, "m-fallback", "demo", "codex",
                              [("user", DESIGN_COUNCIL_PROMPT),
                               ("assistant", SUBSTANTIVE_REPLY)])
    late = _write_session(sandbox, "a-late", "demo", "codex",
                          [("user", DESIGN_COUNCIL_PROMPT),
                           ("assistant", SUBSTANTIVE_REPLY)])

    def set_timestamps(path, stamp):
        records = [json.loads(line) for line in path.read_text().splitlines()]
        for record in records:
            record["timestamp"] = stamp
        path.write_text("".join(json.dumps(record) + "\n" for record in records))

    set_timestamps(early, "2026-01-01T00:00:00Z")
    set_timestamps(late, "2026-01-03T00:00:00Z")
    records = [json.loads(line) for line in fallback.read_text().splitlines()]
    for record in records:
        record.pop("timestamp")
    fallback.write_text("".join(json.dumps(record) + "\n" for record in records))
    os.utime(fallback, (datetime(2026, 1, 2, tzinfo=timezone.utc).timestamp(),) * 2)

    result = _coverage()
    assert result["unprocessed"] == [str(late), str(fallback), str(early)]
    assert result["unprocessed_sorted"] == [
        {"path": str(early), "timestamp": "2026-01-01T00:00:00Z"},
        {"path": str(fallback), "timestamp": "2026-01-02T00:00:00Z"},
        {"path": str(late), "timestamp": "2026-01-03T00:00:00Z"},
    ]


def test_unprocessed_sorted_is_available_when_bootstrapping(sandbox):
    (sandbox / "projects" / "demo.json").unlink()
    transcript = _write_session(sandbox, "bootstrap", "demo", "codex",
                                [("user", DESIGN_COUNCIL_PROMPT),
                                 ("assistant", SUBSTANTIVE_REPLY)])

    result = _coverage()
    assert result["status"] == "no_state"
    assert result["unprocessed"] == [str(transcript)]
    assert result["unprocessed_sorted"] == [{
        "path": str(transcript),
        "timestamp": "2026-01-01T00:00:00Z",
    }]
