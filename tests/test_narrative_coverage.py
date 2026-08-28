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
import os
from datetime import datetime, timezone, timedelta

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
