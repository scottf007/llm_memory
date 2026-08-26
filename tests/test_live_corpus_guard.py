"""Skip-when-absent / run-when-present for the live-corpus certification loaders.

House style matches tests/test_real_names_guard.py: the skip lives in the
loader, names the missing pinned file, and frozen assertion bodies are
untouched. These tests prove that mechanism with temp paths — they never
copy owner ledger data into the tree.
"""
from __future__ import annotations

import pytest

from tests.fixtures.certification import live_ledger
from tests.fixtures.certification import replay_oracle

from tests import test_archive_class
from tests import test_cascade
from tests import test_certify
from tests import test_claim_match
from tests import test_replay_oracle
from tests import test_renderer


def test_live_ledger_loader_skips_when_file_absent(monkeypatch, tmp_path):
    missing = tmp_path / "projects" / "llm_memory.json"
    monkeypatch.setattr(live_ledger, "LIVE_LEDGER_PATH", missing)
    with pytest.raises(pytest.skip.Exception, match="pinned live ledger missing") as exc:
        live_ledger.load_live_state()
    assert str(missing) in str(exc.value)
    assert "never committed as fixtures" in str(exc.value)


def test_replay_snapshot_loader_skips_when_file_absent(monkeypatch, tmp_path):
    missing = tmp_path / "llm_memory.json.before"
    monkeypatch.setattr(replay_oracle, "SNAPSHOT_PATH", missing)
    with pytest.raises(pytest.skip.Exception, match="pinned replay-oracle source missing") as exc:
        replay_oracle.load_snapshot()
    assert str(missing) in str(exc.value)
    assert "never committed as fixtures" in str(exc.value)


def test_replay_delta_loader_skips_when_file_absent(monkeypatch, tmp_path):
    missing = tmp_path / "llm_memory.audit.delta.json"
    monkeypatch.setattr(replay_oracle, "DELTA_PATH", missing)
    with pytest.raises(pytest.skip.Exception, match="pinned replay-oracle source missing") as exc:
        replay_oracle.load_audit_delta()
    assert str(missing) in str(exc.value)


def test_frozen_live_ledger_tests_skip_via_loader(monkeypatch, tmp_path):
    """The named CI rows that read the live ledger skip, they do not FileNotFoundError."""
    monkeypatch.setattr(live_ledger, "LIVE_LEDGER_PATH", tmp_path / "missing.json")
    with pytest.raises(pytest.skip.Exception, match="pinned live ledger missing"):
        test_archive_class.test_never_whole_string_contains()
    with pytest.raises(pytest.skip.Exception, match="pinned live ledger missing"):
        test_cascade.test_u1_citation_pair_stays_active_no_edge()
    with pytest.raises(pytest.skip.Exception, match="pinned live ledger missing"):
        test_certify.test_u1_citation_pair_is_suspect_not_contradiction()
    with pytest.raises(pytest.skip.Exception, match="pinned live ledger missing"):
        test_claim_match.test_u1_citation_matcher_positive()


def test_frozen_replay_tests_skip_via_loader(monkeypatch, tmp_path):
    monkeypatch.setattr(replay_oracle, "SNAPSHOT_PATH", tmp_path / "missing.before")
    monkeypatch.setattr(replay_oracle, "DELTA_PATH", tmp_path / "missing.delta")
    with pytest.raises(pytest.skip.Exception, match="pinned replay-oracle source missing"):
        test_replay_oracle.test_source_pins_match()
    with pytest.raises(pytest.skip.Exception, match="pinned replay-oracle source missing"):
        test_replay_oracle.test_replay_pair_invariant_hash_pin()
    with pytest.raises(pytest.skip.Exception, match="pinned replay-oracle source missing"):
        test_claim_match.test_u2_founding_span()
    with pytest.raises(pytest.skip.Exception, match="pinned replay-oracle source missing"):
        test_renderer.test_founding_case_omitted_before_ranking()


def test_live_ledger_loader_runs_when_present():
    if not live_ledger.LIVE_LEDGER_PATH.is_file():
        pytest.skip("meta: live ledger is absent on this machine")
    state = live_ledger.load_live_state()
    assert state.get("decisions") is not None


def test_replay_loaders_run_when_present():
    if not replay_oracle.SNAPSHOT_PATH.is_file() or not replay_oracle.DELTA_PATH.is_file():
        pytest.skip("meta: replay-oracle sources are absent on this machine")
    snapshot = replay_oracle.load_snapshot()
    delta = replay_oracle.load_audit_delta()
    assert snapshot.get("decisions") is not None
    assert delta.get("session_id") == replay_oracle.AUDIT_DELTA_SESSION_ID
