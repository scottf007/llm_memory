"""Frozen tests for the hermetic default suite (docs/design/hermetic-suite-2026-09-03.md).

Covers the spec's four acceptance rows (T1-T4). This module is the test
author's half of the arc: a different vendor implements the `live_corpus`
marker, the `pyproject.toml` config, and the D3 codex-guard fix against
these tests, unmodified. Nothing here reads or writes a live store except
T1's collection-only census, which never opens a store file -- it only
reads pytest's own collection output, run against an isolated empty HOME.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# T1 -- collection contract
# ---------------------------------------------------------------------------
#
# Derived mechanically: `pytest tests/ -p no:cacheprovider -v` run once with
# HOME pointed at an empty temp dir and LLM_MEMORY_HOME="", read only through
# pytest's own SKIPPED lines (never through a store file). That run produced
# exactly 36 skips. 35 of them have a reason naming a live store or corpus
# (~/.claude/memory ledger/snapshots, ~/.codex, ~/.grok, "local conversation
# corpus", "live store"); the one exclusion is structural, not store-gated:
# test_codex_adapter.py::test_fixture_envelope_survives_the_turn_counter[
# 01-compacted-dialect_item_completed-u0], reason "subagent threads are
# captured by their parent" (one FIXTURES entry is a subagent fixture, on
# every machine, live store or not). Confirmed with llm-pm-4 (event
# 01788413851332215213-llm-pm-4-57ea1e04): freeze on 35, not 33 -- the design
# note's "36 minus 3" undercounted by conflating the fake-HOME skip set with
# today's real-HOME skip set, which are not the same tests.

LIVE_CORPUS_NODE_IDS = tuple(sorted([
    "tests/test_adapter_oracle.py::test_stored_conversation_regenerates_byte_for_byte[NOTSET]",
    "tests/test_adapter_oracle.py::test_corpus_sample_is_actually_diverse",
    "tests/test_adapter_oracle.py::test_discover_finds_the_sampled_sessions",
    "tests/test_archive_class.py::test_never_whole_string_contains",
    "tests/test_archive_class.py::test_unclassified_trio_exact",
    "tests/test_archive_class.py::test_full_corpus_reproduces_33_37_3",
    "tests/test_archive_class.py::test_lifecycle_only_from_closure_prefix",
    "tests/test_archive_class.py::test_backfill_397_then_zero",
    "tests/test_archive_class.py::test_evaluate_computes_archive_class_if_absent",
    "tests/test_cascade.py::test_u1_citation_pair_stays_active_no_edge",
    "tests/test_cascade.py::test_regrade_lifecycle_parents_never_archive",
    "tests/test_certify.py::test_u1_citation_pair_is_suspect_not_contradiction",
    "tests/test_certify.py::test_regrade_lifecycle_parents_structurally_excluded",
    "tests/test_claim_match.py::test_u1_citation_matcher_positive",
    "tests/test_claim_match.py::test_u2_founding_span",
    "tests/test_claim_match.py::test_u2_replacement_fp_is_fuzzy_only",
    "tests/test_claim_match.py::test_u3_live_corpus_substring_facts",
    "tests/test_claim_match.py::test_u4_two_token_minimum",
    "tests/test_claim_match.py::test_u4_requires_parent_scoped_tokens",
    "tests/test_codex_adapter.py::test_no_real_session_is_mixed_dialect",
    "tests/test_codex_adapter.py::test_every_real_session_parses_without_crashing",
    "tests/test_codex_adapter.py::test_no_real_session_silently_yields_zero_turns",
    "tests/test_grok_adapter.py::test_live_store_every_non_superseded_session_verifies",
    "tests/test_grok_adapter.py::test_live_store_zero_parents_outside_their_childs_dir_and_zero_dangling",
    "tests/test_grok_adapter.py::test_live_store_acceptance_session_if_present",
    "tests/test_live_corpus_guard.py::test_live_ledger_loader_runs_when_present",
    "tests/test_live_corpus_guard.py::test_replay_loaders_run_when_present",
    "tests/test_narrative_coverage.py::test_live_finance_nexus_grok_unprocessed_all_clear_a3_threshold",
    "tests/test_narrative_coverage.py::test_live_load_balancer_acceptance_session_not_regressed",
    "tests/test_narrative_coverage.py::test_live_finance_nexus_excludes_grok_keepalive_forks",
    "tests/test_narrative_coverage.py::test_live_no_project_lists_a_grok_keepalive_transcript",
    "tests/test_rank_below.py::test_live_eight_query_panel_copy_no_archived_above_active",
    "tests/test_renderer.py::test_founding_case_omitted_before_ranking",
    "tests/test_replay_oracle.py::test_replay_pair_invariant_hash_pin",
    "tests/test_replay_oracle.py::test_source_pins_match",
]))

assert len(LIVE_CORPUS_NODE_IDS) == 35, len(LIVE_CORPUS_NODE_IDS)


def _run_pytest_collect(extra_args: list[str], home_dir: Path) -> tuple[set[str], subprocess.CompletedProcess[str]]:
    """Collect-only, against an isolated empty HOME so the result never
    depends on what live stores happen to exist on the machine running this
    test itself."""
    tmux_tmpdir = home_dir / "tmux"
    tmux_tmpdir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["HOME"] = str(home_dir)
    env["LLM_MEMORY_HOME"] = ""
    env["TMUX_TMPDIR"] = str(tmux_tmpdir)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-p", "no:cacheprovider",
         "--collect-only", "-q", *extra_args],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=180,
    )
    ids = {
        line.strip() for line in result.stdout.splitlines()
        if line.strip().startswith("tests/") and "::" in line
    }
    return ids, result


def test_default_collection_excludes_the_live_corpus_census(tmp_path):
    """Row T1: the default invocation collects no item marked live_corpus.

    Today (no marker registered yet) every one of the 35 pinned rows is
    still collected in the default run -- this is the RED state, and the
    reason is exactly "no marker exists yet", not a subprocess/env problem.
    """
    ids, result = _run_pytest_collect([], tmp_path / "home")
    overlap = ids & set(LIVE_CORPUS_NODE_IDS)
    assert not overlap, (
        f"default collection still includes {len(overlap)} live_corpus row(s): "
        f"{sorted(overlap)}\n{result.stdout[-2000:]}"
    )


def test_live_corpus_marker_collects_exactly_the_pinned_census(tmp_path):
    """Row T1: `-m live_corpus --collect-only -q` collects exactly the 35
    pinned node ids.

    Today this collects nothing (the marker does not exist), so the sets
    differ -- RED for the same reason as the test above.
    """
    ids, result = _run_pytest_collect(["-m", "live_corpus"], tmp_path / "home")
    assert ids == set(LIVE_CORPUS_NODE_IDS), (
        f"missing: {sorted(set(LIVE_CORPUS_NODE_IDS) - ids)}\n"
        f"unexpected: {sorted(ids - set(LIVE_CORPUS_NODE_IDS))}\n"
        f"{result.stdout[-2000:]}"
    )


# ---------------------------------------------------------------------------
# T2 -- marking rules
# ---------------------------------------------------------------------------

pytest_plugins = ["pytester"]


def test_marking_rule_applies_test_live_prefix_and_explicit_list_not_others(pytester, monkeypatch):
    """Row T2: a `test_live_x` function is marked (trigger, name-prefix rule),
    a function whose node id is in the explicit list is marked (trigger,
    explicit-list rule), and a plain `test_y` is not (non-trigger control).

    This drives the *real* `pytest_collection_modifyitems` hook from
    `tests/conftest.py` against a throwaway pytester project, monkeypatching
    only the explicit-list constant (`LIVE_CORPUS_NODE_IDS`) so the trigger
    case doesn't depend on this repo's real file layout. Today this fails at
    collection: `tests/conftest.py` defines neither the hook nor the
    constant yet.

    `pytester.runpytest()` runs the nested session in-process, so its item
    node ids come out relative to *this* session's rootdir, not the temp
    project's own directory (a pytester quirk, confirmed by printing
    `item.nodeid` from inside the hook) -- the expected prefix is computed
    from `pytester.path` rather than assumed as a bare filename.
    """
    import tests.conftest as target_conftest

    pytester.makepyfile(test_marking_probe="""
def test_live_x():
    pass


def test_explicit_target():
    pass


def test_y():
    pass
""")
    pytester.makeconftest(f"""
import sys
sys.path.insert(0, {str(REPO_ROOT)!r})
from tests.conftest import pytest_collection_modifyitems  # noqa: F401
""")
    probe_prefix = f"{os.path.relpath(pytester.path, start=REPO_ROOT)}/test_marking_probe.py"
    monkeypatch.setattr(
        target_conftest,
        "LIVE_CORPUS_NODE_IDS",
        (f"{probe_prefix}::test_explicit_target",),
        raising=False,
    )

    result = pytester.runpytest("--collect-only", "-q", "-m", "live_corpus")
    collected = {
        line.strip() for line in result.stdout.lines
        if line.strip().startswith(f"{probe_prefix}::")
    }
    assert collected == {
        f"{probe_prefix}::test_live_x",
        f"{probe_prefix}::test_explicit_target",
    }, f"stdout:\n{result.stdout.str()}"


# ---------------------------------------------------------------------------
# T3 -- codex zero-turn guard candidate rule (D3)
# ---------------------------------------------------------------------------


def _rollout(records: list[dict[str, object]], tmp_path: Path, name: str) -> Path:
    path = tmp_path / f"{name}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return path


def _meta_record(cwd: str = "/home/u/projects/demo") -> dict[str, object]:
    return {"timestamp": "2026-01-01T00:00:00.000Z", "type": "session_meta",
            "payload": {"id": "abc", "cwd": cwd}}


def test_session_meta_and_task_started_only_is_not_a_zero_turn_candidate(tmp_path):
    """Row T3 non-trigger: a rollout with only session_meta + event_msg/
    task_started never had a user message at all, so it is not "a session
    that would vanish" -- the candidate rule must not flag it.

    Today this fails to import `is_zero_turn_candidate` from
    `tests.test_codex_adapter`; the implementer factors it out of
    `test_no_real_session_silently_yields_zero_turns`.
    """
    from adapters import codex
    from tests.test_codex_adapter import is_zero_turn_candidate

    path = _rollout([
        _meta_record(),
        {"timestamp": "2026-01-01T00:00:01.000Z", "type": "event_msg",
         "payload": {"type": "task_started"}},
    ], tmp_path, name="rollout-no-user-record")
    ref = codex.ref_for_path(path, session_id="codex-no-user-record")

    assert is_zero_turn_candidate(ref) is False


def test_a_real_user_record_that_renders_zero_turns_is_a_zero_turn_candidate(tmp_path):
    """Row T3 trigger: a rollout with a real event_msg/user_message record
    whose text is whitespace-only renders zero user turns (the adapter's
    `_clean_text` drops it) -- exactly "a session with content that renders
    empty", so the candidate rule must flag it.

    Today this fails to import `is_zero_turn_candidate` for the same reason
    as the non-trigger test above.
    """
    from adapters import codex
    from tests.test_codex_adapter import is_zero_turn_candidate

    path = _rollout([
        _meta_record(),
        {"timestamp": "2026-01-01T00:00:01.000Z", "type": "event_msg",
         "payload": {"type": "user_message", "message": "   \n  "}},
    ], tmp_path, name="rollout-blank-user-message")
    ref = codex.ref_for_path(path, session_id="codex-blank-user-message")

    meta, turns = codex.parse(ref)
    assert not any(t.role == "user" and t.text for t in turns), (
        "fixture must actually render zero user turns for this to be the guard's trigger case"
    )
    assert is_zero_turn_candidate(ref) is True


# ---------------------------------------------------------------------------
# T4 -- docs
# ---------------------------------------------------------------------------


def test_testing_doc_names_the_default_and_live_corpus_invocations():
    """Row T4: `docs/testing.md` exists and names the three invocations
    (default, `-m live_corpus`, and `-m ""` for a single deselected node id).

    Today the file does not exist.
    """
    doc = REPO_ROOT / "docs" / "testing.md"
    assert doc.is_file(), "docs/testing.md does not exist yet"
    text = doc.read_text()
    assert "-m live_corpus" in text
    assert '-m ""' in text
