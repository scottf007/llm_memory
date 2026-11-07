"""Shared fixtures for llm_memory tests.

The old `memories`-table-based fixtures were removed along with
tests/test_server.py when the narrative/note/session_log types were
retired. New tests for the current tooling (items-table search,
narrative_coverage, resume, project_lookup) manage their own setup
in test_hooks.py / test_agent_memory.py / test_install.py.
"""

from pathlib import Path

import pytest


# Tests whose assertions read the maintainer's mutable local corpus.  This is
# deliberately an explicit, sorted census rather than an import-time probe of
# any adapter or store: collection must be hermetic on every machine.
LIVE_CORPUS_NODE_IDS = (
    "tests/test_adapter_oracle.py::test_corpus_sample_is_actually_diverse",
    "tests/test_adapter_oracle.py::test_discover_finds_the_sampled_sessions",
    "tests/test_adapter_oracle.py::test_stored_conversation_regenerates_byte_for_byte[NOTSET]",
    "tests/test_archive_class.py::test_backfill_397_then_zero",
    "tests/test_archive_class.py::test_evaluate_computes_archive_class_if_absent",
    "tests/test_archive_class.py::test_full_corpus_reproduces_33_37_3",
    "tests/test_archive_class.py::test_lifecycle_only_from_closure_prefix",
    "tests/test_archive_class.py::test_never_whole_string_contains",
    "tests/test_archive_class.py::test_unclassified_trio_exact",
    "tests/test_cascade.py::test_regrade_lifecycle_parents_never_archive",
    "tests/test_cascade.py::test_u1_citation_pair_stays_active_no_edge",
    "tests/test_certify.py::test_regrade_lifecycle_parents_structurally_excluded",
    "tests/test_certify.py::test_u1_citation_pair_is_suspect_not_contradiction",
    "tests/test_claim_match.py::test_u1_citation_matcher_positive",
    "tests/test_claim_match.py::test_u2_founding_span",
    "tests/test_claim_match.py::test_u2_replacement_fp_is_fuzzy_only",
    "tests/test_claim_match.py::test_u3_live_corpus_substring_facts",
    "tests/test_claim_match.py::test_u4_requires_parent_scoped_tokens",
    "tests/test_claim_match.py::test_u4_two_token_minimum",
    "tests/test_codex_adapter.py::test_every_real_session_parses_without_crashing",
    "tests/test_codex_adapter.py::test_no_real_session_is_mixed_dialect",
    "tests/test_codex_adapter.py::test_no_real_session_silently_yields_zero_turns",
    "tests/test_grok_adapter.py::test_live_store_acceptance_session_if_present",
    "tests/test_grok_adapter.py::test_live_store_every_non_superseded_session_verifies",
    "tests/test_grok_adapter.py::test_live_store_zero_parents_outside_their_childs_dir_and_zero_dangling",
    "tests/test_live_corpus_guard.py::test_live_ledger_loader_runs_when_present",
    "tests/test_live_corpus_guard.py::test_replay_loaders_run_when_present",
    "tests/test_narrative_coverage.py::test_live_finance_nexus_excludes_grok_keepalive_forks",
    "tests/test_narrative_coverage.py::test_live_finance_nexus_grok_unprocessed_all_clear_a3_threshold",
    "tests/test_narrative_coverage.py::test_live_load_balancer_acceptance_session_not_regressed",
    "tests/test_narrative_coverage.py::test_live_no_project_lists_a_grok_keepalive_transcript",
    "tests/test_rank_below.py::test_live_eight_query_panel_copy_no_archived_above_active",
    "tests/test_renderer.py::test_founding_case_omitted_before_ranking",
    "tests/test_replay_oracle.py::test_replay_pair_invariant_hash_pin",
    "tests/test_replay_oracle.py::test_source_pins_match",
)

_LIVE_CORPUS_NODE_ID_BASES = frozenset(
    node_id.split("[", 1)[0] for node_id in LIVE_CORPUS_NODE_IDS
)

_LIVE_CORPUS_NAME_EXCLUSIONS = {
    "tests/test_hermetic_suite.py::test_live_corpus_marker_collects_exactly_the_pinned_census",
    "tests/test_live_corpus_guard.py::test_live_ledger_loader_skips_when_file_absent",
}


def unclassified_live_name_node_ids(items) -> tuple[str, ...]:
    """Return ``test_live_`` rows absent from both live-corpus classifications."""
    return tuple(sorted({
        item.nodeid.split("[", 1)[0]
        for item in items
        if getattr(item, "originalname", item.name).startswith("test_live_")
        and item.nodeid.split("[", 1)[0] not in _LIVE_CORPUS_NODE_ID_BASES
        and item.nodeid.split("[", 1)[0] not in _LIVE_CORPUS_NAME_EXCLUSIONS
    }))


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items):
    """Mark the static live-store census without importing adapters or stores."""
    for item in items:
        function_name = getattr(item, "originalname", item.name)
        node_id_base = item.nodeid.split("[", 1)[0]
        if item.nodeid in LIVE_CORPUS_NODE_IDS or node_id_base in _LIVE_CORPUS_NODE_ID_BASES or (
            function_name.startswith("test_live_")
            and node_id_base not in _LIVE_CORPUS_NAME_EXCLUSIONS
        ):
            item.add_marker(pytest.mark.live_corpus)

# Untracked F-03 list. Gitignored (see .gitignore). Present on the maintainer's
# machine so the literal-name guard runs; absent on a public clone, where the
# named guard skips and the synthetic trigger control still runs.
REAL_NAMES_FILE = Path(__file__).resolve().parent / ".real-names"

# Live-corpus certification tests (the 19 CI FileNotFoundError rows) skip
# when the pinned owner files are absent. The skip lives in the loaders
# (tests/fixtures/certification/live_ledger.py and replay_oracle.py) so
# every existing caller is covered without changing frozen assertion bodies.


@pytest.fixture
def real_names_file():
    """Return (path, names) for tests/.real-names, or skip if it is absent.

    The file is the untracked local source for the F-03 regression guard.
    Tests that need the *mechanism* (skip-when-absent / run-when-present)
    must not use this fixture — they isolate env + path themselves. Tests
    that need the *maintainer list* use this so a public clone skips them
    instead of inventing names in the committed tree.
    """
    if not REAL_NAMES_FILE.is_file():
        pytest.skip(
            "tests/.real-names is absent; F-03 literal-name guard is disabled. "
            "Create it (one name per line) or set LLM_MEMORY_REAL_NAMES."
        )
    names = tuple(
        line.strip()
        for line in REAL_NAMES_FILE.read_text().splitlines()
        if line.strip()
    )
    if not names:
        pytest.skip("tests/.real-names exists but is empty")
    return REAL_NAMES_FILE, names
