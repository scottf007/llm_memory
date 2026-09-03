"""Tests for the grok adapter, pinning docs/design/grok-ingestion-2026-09-03.md.

This file imports `adapters.grok`, which does not exist yet — every test here
is red at *collection* on main (ImportError), which is the frozen-test gate's
required starting state. The implementer's job is `adapters/grok.py`; this
file, `tests/fixtures/grok/`, and `tools/make_grok_fixtures.py` are frozen.

Four kinds of check, same shape as `test_codex_adapter.py`:

1. **Golden fixtures** — nine sanitised real (or, where none existed on this
   machine, hand-authored) sessions with pinned `.expected.md` /
   `.expected.envelope.jsonl` output, one per D9 feature.
2. **The envelope self-check** — a session whose envelope reads as zero user
   turns disappears from `narrative_coverage` with no error anywhere.
3. **Conformance attacks** — a broken adapter must be caught by the registry.
4. **Live-store census** — every non-superseded session on this machine, read
   only, skipped with a message when `~/.grok/sessions` is absent.

Each fixture is a *directory* (`chat_history.jsonl`, `summary.json`,
`events.jsonl`), unlike codex's single-file fixtures — D1 says a grok
`SessionRef.path` is the session directory. All nine fixtures live flatly
under `tests/fixtures/grok/`, which doubles as the "project directory" D4's
same-directory subagent rule is defined over: `03-chain-tail-forked`'s
`summary.json` names `04-superseded-parent` as its `parent_session_id`
literally, by fixture directory name, because a real store's cross-reference
is exactly that — one session's raw id in a sibling's own directory.
"""

from __future__ import annotations

import hashlib
import json
import re
import types
from pathlib import Path

import pytest

import adapters
from adapters import base, envelope
from adapters import grok  # noqa: E402  -- RED on main: this module does not exist yet

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "grok"
FIXTURES = sorted(p for p in FIXTURE_DIR.glob("*") if p.is_dir())


def _ref(path: Path):
    return grok.ref_for_path(path, session_id=f"grok-{path.name}")


def _parse_fixture(path: Path):
    return grok.parse(_ref(path))


# --------------------------------------------------------------------------
# Registration and protocol
# --------------------------------------------------------------------------

def test_grok_is_registered():
    assert "grok" in adapters.names()
    assert adapters.get("grok") is grok
    assert grok.client_name() == "grok"


def test_grok_ids_are_prefixed_and_do_not_collide_with_reserved_stems():
    assert grok.ID_PREFIX == "grok-"
    for reserved in adapters.RESERVED_PREFIXES:
        assert not grok.ID_PREFIX.startswith(reserved)
        assert not reserved.startswith(grok.ID_PREFIX)
    # And not the codex prefix either — one flat transcripts/ dir, one id
    # space per client.
    from adapters import codex
    assert not grok.ID_PREFIX.startswith(codex.ID_PREFIX)
    assert not codex.ID_PREFIX.startswith(grok.ID_PREFIX)


def test_session_id_prefixing_is_idempotent():
    assert grok.session_id_for("01a05f66-abc") == "grok-01a05f66-abc"
    assert grok.session_id_for("grok-01a05f66-abc") == "grok-01a05f66-abc"


def test_bare_ids_route_to_claude_and_prefixed_ids_to_their_owner():
    assert adapters.client_for_session_id("01a05f66-e2bc-7332") == "claude"
    assert adapters.client_for_session_id("grok-01a05f66-e2bc") == "grok"


@pytest.mark.parametrize("session_id", ["GROK-01a05f66", "Grok-01a05f66", "gRoK-01a05f66"])
def test_prefix_routing_is_case_insensitive(session_id):
    assert adapters.client_for_session_id(session_id) == "grok"


def test_case_insensitivity_does_not_capture_unrelated_ids():
    for session_id in ("grokish-abc", "gro-019", "xgrok-019", "019-grok-1"):
        assert adapters.client_for_session_id(session_id) == "claude"


def test_claude_adapter_never_re_extracts_a_grok_envelope():
    """The routing this whole prefix scheme exists for: a grok envelope is
    Claude-*shaped* by construction (adapters/envelope.py), so shape alone
    cannot stop the claude adapter re-extracting it as `client: claude`."""
    assert adapters.client_for_session_id("grok-01a05f66-e2bc-7332") != adapters.DEFAULT


# --------------------------------------------------------------------------
# Golden fixtures
# --------------------------------------------------------------------------

def test_fixtures_are_present():
    assert len(FIXTURES) >= 8, f"expected the committed fixture set, found {len(FIXTURES)}"


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_renders_to_its_pinned_conversation(path):
    meta, turns = _parse_fixture(path)
    expected = (FIXTURE_DIR / f"{path.name}.expected.md").read_text()
    expected = expected.replace(
        f"raw_source: tests/fixtures/grok/{path.name}/chat_history.jsonl",
        f"raw_source: {path / 'chat_history.jsonl'}",
    )
    assert adapters.render_conversation(meta, turns) == expected


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_renders_to_its_pinned_envelope(path):
    meta, turns = _parse_fixture(path)
    expected = (FIXTURE_DIR / f"{path.name}.expected.envelope.jsonl").read_text()
    assert adapters.render_envelope(meta, turns) == expected


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_frontmatter_carries_grok_provenance(path):
    md = adapters.render_conversation(*_parse_fixture(path))
    head = md.split("\n---\n", 1)[0]
    assert "client: grok\n" in head
    assert "raw: transcripts/grok-" in head
    if "agent_session: true" not in head:
        # Only non-subagent fixtures carry raw_source and the D9 provenance.
        assert f"raw_source: {path / 'chat_history.jsonl'}" in head


def test_rendered_fixture_is_independent_of_the_callers_working_directory(monkeypatch, tmp_path):
    """T-F4: Grok provenance is an absolute source path, not cwd-relative."""
    path = FIXTURE_DIR / "02-single-prompt-primary"
    first_cwd = tmp_path / "one"
    second_cwd = tmp_path / "two"
    first_cwd.mkdir()
    second_cwd.mkdir()

    monkeypatch.chdir(first_cwd)
    first = adapters.render_conversation(*_parse_fixture(path))
    monkeypatch.chdir(second_cwd)
    second = adapters.render_conversation(*_parse_fixture(path))

    assert first == second
    assert f"raw_source: {path / 'chat_history.jsonl'}" in first


def test_chain_tail_fixture_carries_fork_of():
    md = (FIXTURE_DIR / "03-chain-tail-forked.expected.md").read_text()
    assert "fork_of: grok-04-superseded-parent" in md
    assert "parent_session_id: grok-04-superseded-parent" in md


# --------------------------------------------------------------------------
# D4 — the superseded-session / is_subagent rule
# --------------------------------------------------------------------------

def test_superseded_parent_yields_is_subagent_and_renders_as_stub():
    meta, turns = _parse_fixture(FIXTURE_DIR / "04-superseded-parent")
    assert meta.is_subagent is True
    assert turns == []
    rendered = adapters.render_conversation(meta, turns)
    assert "skipped: true" in rendered
    assert "agent_session: true" in rendered


def test_chain_tail_is_not_subagent_despite_subagent_resume_kind():
    """The named rejected alternative: stubbing every `subagent_resume`
    session would drop every seat's real work. Only being *named as a
    parent* makes a session superseded — the tail here has that
    session_kind and zero children, so it renders in full."""
    meta, turns = _parse_fixture(FIXTURE_DIR / "03-chain-tail-forked")
    assert meta.is_subagent is False
    assert meta.extra.get("session_kind") == "subagent_resume"
    assert len(turns) > 0


def test_process_foreign_session_skips_only_the_superseded_parent(tmp_path, monkeypatch):
    """`process_foreign_session` already skips `is_subagent` sessions (D4) —
    pin that the tail is archived and the parent is not, using the real
    module rather than re-deriving the rule."""
    import process_transcripts as pt

    archive = tmp_path / "transcripts"
    conv = tmp_path / "conversations"
    monkeypatch.setattr(pt, "ARCHIVE_DIR", archive)
    monkeypatch.setattr(pt, "CONVERSATIONS_DIR", conv)

    parent_ref = _ref(FIXTURE_DIR / "04-superseded-parent")
    tail_ref = _ref(FIXTURE_DIR / "03-chain-tail-forked")

    assert pt.process_foreign_session(parent_ref) is None
    assert pt.process_foreign_session(tail_ref) is not None
    assert (archive / f"{tail_ref.session_id}.jsonl").exists()
    assert not (archive / f"{parent_ref.session_id}.jsonl").exists()


# --------------------------------------------------------------------------
# D5 — the keep/drop table
# --------------------------------------------------------------------------

def _write_session(dirpath: Path, records: list[dict], events: list[dict] | None = None,
                    summary: dict | None = None) -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / "chat_history.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    if events is not None:
        (dirpath / "events.jsonl").write_text(
            "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    base_summary = {"info": {"id": dirpath.name, "cwd": "/home/user/projects/demo"},
                     "created_at": "2026-01-01T00:00:00.000Z",
                     "last_active_at": "2026-01-01T00:05:00.000Z",
                     "current_model_id": "grok-4.6"}
    base_summary.update(summary or {})
    (dirpath / "summary.json").write_text(json.dumps(base_summary), encoding="utf-8")
    return dirpath


@pytest.mark.parametrize("synthetic_reason", [
    "system_reminder", "project_instructions", "subagent_completed",
    "notification_drain", "scheduler_fired", "task_completed", "compaction_meta",
])
def test_synthetic_user_records_are_dropped(tmp_path, synthetic_reason):
    """D5: every documented `synthetic_reason` value is harness injection,
    never reaches the conversation, regardless of its text."""
    sdir = _write_session(tmp_path / "s", [
        {"type": "user", "content": [{"type": "text", "text": "<user_info>preamble</user_info>"}]},
        {"type": "user", "content": [{"type": "text", "text": "should never appear"}],
         "synthetic_reason": synthetic_reason},
        {"type": "user", "content": [{"type": "text", "text": "<user_query>\nreal prompt\n</user_query>"}],
         "prompt_index": 0},
        {"type": "assistant", "content": "ok"},
    ])
    _, turns = grok.parse(_ref(sdir))
    texts = [t.text for t in turns]
    assert not any("should never appear" in t for t in texts)
    assert not any("preamble" in t for t in texts)
    assert any("real prompt" in t for t in texts)


def test_keyless_preamble_is_dropped(tmp_path):
    """The first record of every real session — `<user_info>`/`<git_status>`/
    `<rules>`, neither `prompt_index` nor `synthetic_reason` — never reaches
    the conversation."""
    sdir = _write_session(tmp_path / "s", [
        {"type": "user", "content": [{"type": "text",
         "text": "<user_info>\nOS: linux\n</user_info>\n<git_status>...</git_status>"}]},
        {"type": "user", "content": [{"type": "text", "text": "<user_query>\nreal\n</user_query>"}],
         "prompt_index": 0},
        {"type": "assistant", "content": "ack"},
    ])
    _, turns = grok.parse(_ref(sdir))
    texts = [t.text for t in turns]
    assert not any("OS: linux" in t for t in texts)
    assert texts == ["real", "ack"]


def test_system_reasoning_and_tool_result_records_are_dropped(tmp_path):
    sdir = _write_session(tmp_path / "s", [
        {"type": "system", "content": "the xai system prompt"},
        {"type": "user", "content": [{"type": "text", "text": "<user_query>\nhi\n</user_query>"}],
         "prompt_index": 0},
        {"type": "reasoning", "id": "r1", "summary": [], "encrypted_content": "x", "status": "done"},
        {"type": "tool_result", "tool_call_id": "c1", "content": "some tool output"},
        {"type": "assistant", "content": "hello back"},
    ])
    _, turns = grok.parse(_ref(sdir))
    texts = [t.text for t in turns]
    assert not any("system prompt" in t for t in texts)
    assert not any("tool output" in t for t in texts)
    assert texts == ["hi", "hello back"]


def test_system_reminder_shaped_prompt_with_prompt_index_is_kept(tmp_path):
    """A harness-driven prompt is a bare `<system-reminder>` block, not
    `<user_query>` — but it still carries `prompt_index`, and D5 keys on the
    key, not the wrapper tag, so it is kept."""
    sdir = _write_session(tmp_path / "s", [
        {"type": "user", "content": [{"type": "text",
         "text": "<system-reminder>\nscheduled loop fired\n</system-reminder>"}],
         "prompt_index": 0},
        {"type": "assistant", "content": "on it"},
    ])
    _, turns = grok.parse(_ref(sdir))
    user_texts = [t.text for t in turns if t.role == "user"]
    assert user_texts == ["scheduled loop fired"]


# --------------------------------------------------------------------------
# A3.1 (docs/design/grok-ingestion-2026-09-03.md §7) -- telemetry-shaped
# prompt_index records are dropped even though "prompt_index wins" (D5,
# amended after judge finding F1 / feedback 01788396626574918910: 527
# ingested user turns were pure harness telemetry, not dialogue).
# --------------------------------------------------------------------------

@pytest.mark.parametrize("synthetic_reason", ["subagent_completed", "task_completed"])
def test_a3_telemetry_reason_with_prompt_index_is_dropped(tmp_path, synthetic_reason):
    """Trigger: unlike every other synthetic_reason, these two are dropped
    even when prompt_index is present -- D5's "prompt_index wins" rule no
    longer applies to them specifically."""
    sdir = _write_session(tmp_path / "s", [
        {"type": "user", "content": [{"type": "text", "text": "<user_query>\nreal work\n</user_query>"}],
         "prompt_index": 0},
        {"type": "assistant", "content": "did it"},
        {"type": "user", "content": [{"type": "text",
         "text": "<system-reminder>\nshould never appear\n</system-reminder>"}],
         "synthetic_reason": synthetic_reason, "prompt_index": 1},
        {"type": "assistant", "content": "ack"},
    ])
    _, turns = grok.parse(_ref(sdir))
    user_texts = [t.text for t in turns if t.role == "user"]
    assert user_texts == ["real work"]
    assert not any("should never appear" in t for t in user_texts)


@pytest.mark.parametrize("synthetic_reason", ["scheduler_fired", "notification_drain"])
def test_a3_control_other_synthetic_reasons_with_prompt_index_still_kept(tmp_path, synthetic_reason):
    """Control: A3.1 names exactly two reasons. scheduler_fired (the seat's
    own task) and notification_drain (peer board prose) are unaffected --
    D5's "prompt_index wins" still applies to them."""
    sdir = _write_session(tmp_path / "s", [
        {"type": "user", "content": [{"type": "text", "text": "<system-reminder>\nkept text\n</system-reminder>"}],
         "synthetic_reason": synthetic_reason, "prompt_index": 0},
        {"type": "assistant", "content": "ok"},
    ])
    _, turns = grok.parse(_ref(sdir))
    user_texts = [t.text for t in turns if t.role == "user"]
    assert user_texts == ["kept text"]


def test_a3_control_non_synthetic_prompt_index_still_kept(tmp_path):
    """Control: a plain interactive prompt (no synthetic_reason at all) is
    untouched by A3.1."""
    sdir = _write_session(tmp_path / "s", [
        {"type": "user", "content": [{"type": "text", "text": "<user_query>\nplain prompt\n</user_query>"}],
         "prompt_index": 0},
        {"type": "assistant", "content": "ok"},
    ])
    _, turns = grok.parse(_ref(sdir))
    user_texts = [t.text for t in turns if t.role == "user"]
    assert user_texts == ["plain prompt"]


def test_a3_dropped_record_still_consumes_its_event_timestamp_slot(tmp_path):
    """Design decision pinned here because §7 does not spell it out: on this
    machine every prompt_index record -- kept or A3.1-dropped -- has its own
    turn_started/turn_ended pair in events.jsonl (verified 1:1 on the real
    fixture 09-both-keys-retained source: 32 prompt_index records, 32
    turn_started entries). So a dropped record must still consume one
    events.jsonl slot, or every later kept turn's timestamp shifts earlier
    by the number of drops before it. Three user prompts, three event pairs,
    the middle one task_completed and dropped: the third (kept) user turn
    must take the *third* timestamp, not the second."""
    sdir = _write_session(
        tmp_path / "s",
        [
            {"type": "user", "content": [{"type": "text", "text": "<user_query>\none\n</user_query>"}],
             "prompt_index": 0},
            {"type": "assistant", "content": "a1"},
            {"type": "user", "content": [{"type": "text",
             "text": "<system-reminder>\ndropped\n</system-reminder>"}],
             "synthetic_reason": "task_completed", "prompt_index": 1},
            {"type": "assistant", "content": "a2"},
            {"type": "user", "content": [{"type": "text", "text": "<user_query>\nthree\n</user_query>"}],
             "prompt_index": 2},
            {"type": "assistant", "content": "a3"},
        ],
        events=[
            {"ts": "2026-02-01T00:00:00.000Z", "type": "turn_started", "turn_number": 0},
            {"ts": "2026-02-01T00:00:10.000Z", "type": "turn_ended", "outcome": "completed"},
            {"ts": "2026-02-01T00:01:00.000Z", "type": "turn_started", "turn_number": 1},
            {"ts": "2026-02-01T00:01:10.000Z", "type": "turn_ended", "outcome": "completed"},
            {"ts": "2026-02-01T00:02:00.000Z", "type": "turn_started", "turn_number": 2},
            {"ts": "2026-02-01T00:02:10.000Z", "type": "turn_ended", "outcome": "completed"},
        ],
    )
    _, turns = grok.parse(_ref(sdir))
    user_turns = [t for t in turns if t.role == "user"]
    assert [t.text for t in user_turns] == ["one", "three"]
    assert user_turns[0].timestamp == "2026-02-01T00:00:00.000Z"
    assert user_turns[1].timestamp == "2026-02-01T00:02:00.000Z"


def test_user_query_wrapper_is_stripped_exactly_once(tmp_path):
    sdir = _write_session(tmp_path / "s", [
        {"type": "user",
         "content": [{"type": "text", "text": "<user_query>\nouter\n</user_query>"}],
         "prompt_index": 0},
        {"type": "assistant", "content": "ack"},
    ])
    _, turns = grok.parse(_ref(sdir))
    user_text = next(t.text for t in turns if t.role == "user")
    assert user_text == "outer"
    assert "<user_query>" not in user_text


def test_inner_angle_brackets_survive_the_wrapper_strip(tmp_path):
    """Non-trigger control: stripping is exactly one outer layer. Prose that
    happens to use `<job>`/`<seat>` as placeholders must not be touched —
    there is no general tag regex, per the design note's D5."""
    sdir = _write_session(tmp_path / "s", [
        {"type": "user",
         "content": [{"type": "text",
          "text": "<user_query>\nCheck <job> under <seat> before merging.\n</user_query>"}],
         "prompt_index": 0},
        {"type": "assistant", "content": "checked"},
    ])
    _, turns = grok.parse(_ref(sdir))
    user_text = next(t.text for t in turns if t.role == "user")
    assert user_text == "Check <job> under <seat> before merging."


# --------------------------------------------------------------------------
# Tool markers
# --------------------------------------------------------------------------

def test_tool_calls_sets_had_tool_use_and_empty_text_contributes_no_prose(tmp_path):
    sdir = _write_session(tmp_path / "s", [
        {"type": "user", "content": [{"type": "text", "text": "<user_query>\ngo\n</user_query>"}],
         "prompt_index": 0},
        {"type": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "run"}]},
        {"type": "tool_result", "tool_call_id": "c1", "content": "output"},
        {"type": "assistant", "content": "done"},
    ])
    _, turns = grok.parse(_ref(sdir))
    assistant_turns = [t for t in turns if t.role == "assistant"]
    assert any(t.had_tool_use and t.text == "" for t in assistant_turns)
    rendered = adapters.render_conversation(*grok.parse(_ref(sdir)))
    # The empty-text marker contributes an [L:N] ref but no prose block of its own.
    assert rendered.count("=== assistant") == 1
    assert "[L:2]" in rendered or "[L:" in rendered


def test_backend_tool_call_sets_had_tool_use(tmp_path):
    sdir = _write_session(tmp_path / "s", [
        {"type": "user", "content": [{"type": "text", "text": "<user_query>\nsearch it\n</user_query>"}],
         "prompt_index": 0},
        {"type": "backend_tool_call", "kind": {"tool_type": "web_search", "action": {"type": "search"}}},
        {"type": "assistant", "content": "found it"},
    ])
    _, turns = grok.parse(_ref(sdir))
    assert any(t.had_tool_use for t in turns)
    rendered = adapters.render_conversation(*grok.parse(_ref(sdir)))
    assert "[L:" in rendered


# --------------------------------------------------------------------------
# D5 — timestamps
# --------------------------------------------------------------------------

def test_kth_user_turn_takes_kth_turn_started_timestamp(tmp_path):
    sdir = _write_session(
        tmp_path / "s",
        [
            {"type": "user", "content": [{"type": "text", "text": "<user_query>\none\n</user_query>"}],
             "prompt_index": 0},
            {"type": "assistant", "content": "a1"},
            {"type": "user", "content": [{"type": "text", "text": "<user_query>\ntwo\n</user_query>"}],
             "prompt_index": 1},
            {"type": "assistant", "content": "a2"},
        ],
        events=[
            {"ts": "2026-02-01T00:00:00.000Z", "type": "turn_started", "turn_number": 0},
            {"ts": "2026-02-01T00:00:10.000Z", "type": "turn_ended", "outcome": "completed"},
            {"ts": "2026-02-01T00:01:00.000Z", "type": "turn_started", "turn_number": 1},
            {"ts": "2026-02-01T00:01:10.000Z", "type": "turn_ended", "outcome": "completed"},
        ],
    )
    _, turns = grok.parse(_ref(sdir))
    user_turns = [t for t in turns if t.role == "user"]
    assert user_turns[0].timestamp == "2026-02-01T00:00:00.000Z"
    assert user_turns[1].timestamp == "2026-02-01T00:01:00.000Z"
    assistant_turns = [t for t in turns if t.role == "assistant"]
    assert assistant_turns[0].timestamp == user_turns[0].timestamp
    assistant_turns_by_second = assistant_turns[1].timestamp
    assert assistant_turns_by_second == user_turns[1].timestamp


def test_short_events_falls_back_to_created_at_and_notes(tmp_path):
    sdir = _write_session(
        tmp_path / "s",
        [
            {"type": "user", "content": [{"type": "text", "text": "<user_query>\none\n</user_query>"}],
             "prompt_index": 0},
            {"type": "assistant", "content": "a1"},
            {"type": "user", "content": [{"type": "text", "text": "<user_query>\ntwo\n</user_query>"}],
             "prompt_index": 1},
            {"type": "assistant", "content": "a2"},
        ],
        events=[
            {"ts": "2026-02-01T00:00:00.000Z", "type": "turn_started", "turn_number": 0},
            {"ts": "2026-02-01T00:00:10.000Z", "type": "turn_ended", "outcome": "completed"},
        ],
        summary={"created_at": "2026-02-01T09:00:00.000Z"},
    )
    meta, turns = grok.parse(_ref(sdir))
    user_turns = [t for t in turns if t.role == "user"]
    assert user_turns[0].timestamp == "2026-02-01T00:00:00.000Z"
    assert user_turns[1].timestamp == "2026-02-01T09:00:00.000Z"
    assert any("events.jsonl" in n or "created_at" in n for n in meta.notes)
    assert all(t.timestamp for t in turns)


def test_missing_events_file_falls_back_to_created_at_for_every_turn(tmp_path):
    sdir = _write_session(
        tmp_path / "s",
        [
            {"type": "user", "content": [{"type": "text", "text": "<user_query>\nhi\n</user_query>"}],
             "prompt_index": 0},
            {"type": "assistant", "content": "hey"},
        ],
        events=None,
        summary={"created_at": "2026-03-01T00:00:00.000Z"},
    )
    meta, turns = grok.parse(_ref(sdir))
    assert all(t.timestamp == "2026-03-01T00:00:00.000Z" for t in turns)
    assert meta.notes


def test_no_turn_has_an_empty_timestamp_when_summary_exists():
    """The fixture set's own regression: assistant content that precedes the
    first kept user turn (a harness kickoff reply) must not render with an
    empty timestamp."""
    meta, turns = _parse_fixture(FIXTURE_DIR / "09-both-keys-retained")
    assert all(t.timestamp for t in turns)


# --------------------------------------------------------------------------
# D3 — session_meta
# --------------------------------------------------------------------------

def test_meta_cwd_from_summary_info_cwd_and_project_derivation():
    meta, _ = _parse_fixture(FIXTURE_DIR / "05-backend-tool-call")
    assert meta.cwd.startswith("/home/user/projects/project-")
    assert meta.project == meta.cwd.rsplit("/", 1)[-1]


def test_meta_falls_back_to_url_decoded_dir_name_when_cwd_absent(tmp_path):
    """D3: when `summary.json.info.cwd` is missing, the project-level
    directory name (URL-encoded, as the real store names it) is decoded and
    used instead. `SessionRef.path` is the *session* directory, so the
    project-level name lives one level up — the real
    `SESSIONS_DIR/<urlencoded-cwd>/<session_id>/` shape."""
    project_dir = tmp_path / "%2Fhome%2Fuser%2Fworktrees%2Ffoo"
    sdir = project_dir / "sess-1"
    _write_session(sdir, [
        {"type": "user", "content": [{"type": "text", "text": "<user_query>\nhi\n</user_query>"}],
         "prompt_index": 0},
        {"type": "assistant", "content": "hey"},
    ], summary={"info": {"id": "sess-1"}})  # no cwd key
    meta, _ = grok.parse(grok.ref_for_path(sdir, session_id="grok-sess-1"))
    assert meta.cwd == "/home/user/worktrees/foo"


def test_dotted_worktree_cwd_attributes_to_parent_project(tmp_path):
    sdir = _write_session(tmp_path / "s", [
        {"type": "user", "content": [{"type": "text", "text": "<user_query>\nhi\n</user_query>"}],
         "prompt_index": 0},
        {"type": "assistant", "content": "hey"},
    ], summary={"info": {"id": "s", "cwd": "/home/user/projects/.agent-messaging-worktrees/"
                                            "agent-messaging/c2-spec/seat"}})
    meta, _ = grok.parse(_ref(sdir))
    assert meta.project == "agent-messaging"


def test_unattributed_cwd_fixture_has_no_project():
    meta, _ = _parse_fixture(FIXTURE_DIR / "07-unattributed-cwd")
    assert meta.project == ""
    md = (FIXTURE_DIR / "07-unattributed-cwd.expected.md").read_text()
    assert "\nproject:" not in md.split("\n---\n", 1)[0]


def test_meta_started_ended_and_extra_fields():
    meta, _ = _parse_fixture(FIXTURE_DIR / "01-interactive-primary")
    assert meta.started
    assert meta.ended
    for key in ("model", "title", "agent_name"):
        assert key in meta.extra, f"missing extra[{key!r}]"


def test_meta_extra_carries_session_kind_when_present():
    meta, _ = _parse_fixture(FIXTURE_DIR / "03-chain-tail-forked")
    assert meta.extra.get("session_kind") == "subagent_resume"


# --------------------------------------------------------------------------
# D2 — discover() edge cases, on a synthetic tmp tree
# --------------------------------------------------------------------------

def _make_real_session(root: Path, project_enc: str, sid: str, created_at: str) -> Path:
    sdir = root / project_enc / sid
    _write_session(sdir, [
        {"type": "user", "content": [{"type": "text", "text": "<user_query>\nhi\n</user_query>"}],
         "prompt_index": 0},
        {"type": "assistant", "content": "hey"},
    ], summary={"info": {"id": sid, "cwd": "/home/user/projects/demo"}, "created_at": created_at})
    return sdir


def test_discover_skips_non_directory_entries_and_ignored_files(tmp_path, monkeypatch):
    monkeypatch.setattr(grok, "SESSIONS_DIR", tmp_path)
    proj = tmp_path / "%2Fhome%2Fuser%2Fprojects%2Fdemo"
    proj.mkdir()
    (proj / "session_search.sqlite").write_text("not a session")
    (proj / "prompt_history.jsonl").write_text('{"not":"a session"}\n')
    _make_real_session(tmp_path, "%2Fhome%2Fuser%2Fprojects%2Fdemo", "sess-real",
                        "2026-01-01T00:00:00.000Z")
    refs = grok.discover()
    ids = {r.session_id for r in refs}
    assert any("sess-real" in i for i in ids)
    assert not any("sqlite" in i or "prompt_history" in i for i in ids)


def test_discover_skips_and_counts_unreadable_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(grok, "SESSIONS_DIR", tmp_path)
    _make_real_session(tmp_path, "%2Fhome%2Fuser%2Fprojects%2Fdemo", "sess-ok",
                        "2026-01-01T00:00:00.000Z")
    bad = tmp_path / "%2Fhome%2Fuser%2Fprojects%2Fdemo" / "sess-bad"
    bad.mkdir()
    (bad / "chat_history.jsonl").write_text("{not json\n")
    (bad / "chat_history.jsonl").chmod(0o000)
    try:
        refs = grok.discover()
        assert any("sess-ok" in r.session_id for r in refs)
    finally:
        (bad / "chat_history.jsonl").chmod(0o644)


def test_discover_keeps_first_on_duplicate_id(tmp_path, monkeypatch):
    monkeypatch.setattr(grok, "SESSIONS_DIR", tmp_path)
    _make_real_session(tmp_path, "%2Fhome%2Fuser%2Fprojects%2Fone", "dup-id",
                        "2026-01-01T00:00:00.000Z")
    _make_real_session(tmp_path, "%2Fhome%2Fuser%2Fprojects%2Ftwo", "dup-id",
                        "2026-01-02T00:00:00.000Z")
    refs = grok.discover()
    matching = [r for r in refs if r.session_id.endswith("dup-id")]
    assert len(matching) == 1


def test_discover_orders_oldest_first_by_created_at(tmp_path, monkeypatch):
    monkeypatch.setattr(grok, "SESSIONS_DIR", tmp_path)
    _make_real_session(tmp_path, "%2Fhome%2Fuser%2Fprojects%2Fdemo", "sess-b",
                        "2026-01-02T00:00:00.000Z")
    _make_real_session(tmp_path, "%2Fhome%2Fuser%2Fprojects%2Fdemo", "sess-a",
                        "2026-01-01T00:00:00.000Z")
    refs = grok.discover()
    ids = [r.session_id for r in refs]
    a_index = next(i for i, x in enumerate(ids) if "sess-a" in x)
    b_index = next(i for i, x in enumerate(ids) if "sess-b" in x)
    assert a_index < b_index


def test_discover_returns_empty_list_when_sessions_dir_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(grok, "SESSIONS_DIR", tmp_path / "does-not-exist")
    assert grok.discover() == []


# --------------------------------------------------------------------------
# Envelope
# --------------------------------------------------------------------------

@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_envelope_verifies_for_every_fixture(path, tmp_path):
    meta, turns = _parse_fixture(path)
    dest = envelope.write_envelope(meta, turns, tmp_path)
    expected_count = envelope.user_turn_count(turns)
    ok, detail = envelope.verify_envelope(dest, expected_count)
    assert ok, detail


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_envelope_is_ascii(path, tmp_path):
    meta, turns = _parse_fixture(path)
    dest = envelope.write_envelope(meta, turns, tmp_path)
    raw = dest.read_bytes()
    raw.decode("ascii")  # raises if any byte is non-ASCII


# --------------------------------------------------------------------------
# Conformance
# --------------------------------------------------------------------------

def test_registry_rejects_a_module_missing_one_of_the_four_functions():
    fake = types.ModuleType("fake_adapter")
    fake.discover = lambda: []
    fake.session_meta = lambda ref: None
    fake.turns = lambda ref: []
    # client_name deliberately missing
    assert not base.conforms(fake)


def test_grok_module_itself_conforms():
    assert base.conforms(grok)


# --------------------------------------------------------------------------
# Fixture hygiene
# --------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:+-]{1,40}$")
_PLACEHOLDER_RE = re.compile(
    r"^<[A-Za-z0-9_-]+:\d+(?::[0-9a-f]{8})?>$|^<[A-Za-z0-9_-]+>$"
    r"|^<(user_query|system-reminder)>\n<inner:\d+>\n</\1>$")
_ALLOWED_LITERALS = {"/home/user"}
_PLACEHOLDER_CWD_RE = re.compile(
    r"^/home/user/(projects|worktrees)/project-[0-9a-f]{8}/?$")


def _walk_strings(node, path="$"):
    if isinstance(node, dict):
        for key, value in node.items():
            yield f"{path}.{key}", str(key)
            yield from _walk_strings(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk_strings(value, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


def _is_sanitised(value: str) -> bool:
    if value == "" or value in _ALLOWED_LITERALS or _PLACEHOLDER_CWD_RE.match(value):
        return True
    if _PLACEHOLDER_RE.match(value) or _TOKEN_RE.match(value):
        return True
    return False


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_every_string_at_every_depth_is_sanitised(path):
    offenders = []
    for source_name in ("chat_history.jsonl", "events.jsonl"):
        source = path / source_name
        if not source.exists():
            continue
        for line_no, line in enumerate(source.read_text().splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except Exception:
                offenders.append((source_name, line_no, "$", "<unparseable line>"))
                continue
            for json_path, value in _walk_strings(record):
                if not _is_sanitised(value):
                    offenders.append((source_name, line_no, json_path, value[:80]))
    summary = json.loads((path / "summary.json").read_text())
    for json_path, value in _walk_strings(summary):
        if not _is_sanitised(value):
            offenders.append(("summary.json", 0, json_path, value[:80]))
    assert not offenders, f"{path.name}: unsanitised string(s): {offenders[:5]}"


def test_the_depth_walk_would_catch_nested_prose():
    smuggled = {"type": "user", "content": [
        {"deep": {"deeper": ["a sentence of real prose that should never ship"]}}]}
    bad = [v for _, v in _walk_strings(smuggled) if not _is_sanitised(v)]
    assert bad == ["a sentence of real prose that should never ship"]
    keyed = {"/home/user/projects/thing/notes.md": {"type": "x"}}
    assert any(not _is_sanitised(v) for _, v in _walk_strings(keyed))


def test_fixture_tree_contains_no_symlinks():
    links = [p for p in FIXTURE_DIR.rglob("*") if p.is_symlink()]
    assert not links, f"symlinks in the fixture tree: {links}"


def test_fixtures_carry_no_identifying_content():
    home = str(Path.home())
    for path in FIXTURE_DIR.rglob("*"):
        if path.is_dir():
            continue
        text = path.read_text(errors="replace")
        assert home not in text, f"{path} leaks a real home directory"
        assert "scott" not in text.lower() or path.name.endswith(".gitignore"), (
            f"{path} may leak the owner's name")


# --------------------------------------------------------------------------
# Live-store census (read-only; skipped when the store is absent)
# --------------------------------------------------------------------------

@pytest.mark.skipif(not (grok.SESSIONS_DIR.exists()), reason="no ~/.grok/sessions on this machine")
def test_live_store_every_non_superseded_session_verifies():
    refs = grok.discover()
    failures = []
    for ref in refs:
        meta, turns = grok.parse(ref)
        if meta.is_subagent:
            continue
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            dest = envelope.write_envelope(meta, turns, Path(td))
            ok, detail = envelope.verify_envelope(dest, envelope.user_turn_count(turns))
            if not ok:
                failures.append(detail)
    assert not failures, f"{len(failures)} session(s) failed envelope verification: {failures[:5]}"


@pytest.mark.skipif(not (grok.SESSIONS_DIR.exists()), reason="no ~/.grok/sessions on this machine")
def test_live_store_zero_parents_outside_their_childs_dir_and_zero_dangling():
    """D4's same-directory assumption, pinned against the real store."""
    refs = grok.discover()
    by_dir: dict[Path, list] = {}
    for ref in refs:
        by_dir.setdefault(ref.path.parent, []).append(ref)

    cross_dir_parents = []
    dangling_parents = []
    for parent_dir, dir_refs in by_dir.items():
        ids_in_dir = {r.path.name for r in dir_refs}
        for ref in dir_refs:
            summary_path = ref.path / "summary.json"
            if not summary_path.exists():
                continue
            try:
                summary = json.loads(summary_path.read_text())
            except Exception:
                continue
            parent = summary.get("parent_session_id")
            if not parent:
                continue
            if parent not in ids_in_dir:
                # Could be cross-dir (present elsewhere) or dangling (nowhere).
                found_elsewhere = any(
                    parent in {r.path.name for r in refs2}
                    for d2, refs2 in by_dir.items() if d2 != parent_dir
                )
                if found_elsewhere:
                    cross_dir_parents.append((ref.session_id, parent))
                else:
                    dangling_parents.append((ref.session_id, parent))
    assert cross_dir_parents == []
    assert dangling_parents == []


@pytest.mark.skipif(not (grok.SESSIONS_DIR.exists()), reason="no ~/.grok/sessions on this machine")
def test_live_store_acceptance_session_if_present():
    target = "01a05f66-e2bc-7332-8728-546c1a71e8cf"
    refs = [r for r in grok.discover() if target in r.session_id]
    if not refs:
        pytest.skip(f"acceptance session {target} not present on this machine")
    meta, turns = grok.parse(refs[0])
    user_turns = sum(1 for t in turns if t.role == "user" and t.text)
    assert user_turns == 21
    assert meta.project == "load_balancer"
