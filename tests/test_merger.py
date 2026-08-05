"""Tests for merger.py re-valuation — the write side of the contested-item
pass. The renderer names items near its budget cut line; the extractor
re-grades them; these entries apply that re-grade back into the ledger.
"""

import pytest

import merger


BASE_TS = "2026-01-01T00:00:00Z"


def _state_with(item):
    return {
        "project": "testproj",
        "decisions": [item],
        "goals": [], "suggestions": [], "learnings": [], "done": [],
        "sessions": [],
    }


def _item(**over):
    item = {
        "id": "dec-aaaa1111",
        "text": "a decision",
        "status": "active",
        "importance": "load_bearing",
        "value": 0.5,
        "last_touched_at": BASE_TS,
        "last_touched_in": "sess-old",
    }
    item.update(over)
    return item


def _delta(revaluations):
    return {
        "session_id": "sess-new",
        "started": "2026-06-01T00:00:00Z",
        "ended": "2026-06-01T01:00:00Z",
        "ledger_delta": {"introduced": {}, "revaluations": revaluations},
    }


def test_revaluation_updates_value():
    state = merger.apply_delta(_state_with(_item()), _delta([
        {"id": "dec-aaaa1111", "value": 0.9, "why": "foundational"},
    ]))
    assert state["decisions"][0]["value"] == pytest.approx(0.9)


def test_revaluation_can_downgrade_importance():
    state = merger.apply_delta(_state_with(_item()), _delta([
        {"id": "dec-aaaa1111", "value": 0.1, "importance": "minor",
         "why": "obvious from the code"},
    ]))
    item = state["decisions"][0]
    assert item["importance"] == "minor"
    assert item["value"] == pytest.approx(0.1)


def test_revaluation_does_not_bump_last_touched_at():
    """The feedback loop this guards against: if re-grading counted as
    activity, every contested item would get a recency boost, survive the
    next cut, and never be reconsidered again."""
    state = merger.apply_delta(_state_with(_item()), _delta([
        {"id": "dec-aaaa1111", "value": 0.9},
    ]))
    item = state["decisions"][0]
    assert item["last_touched_at"] == BASE_TS
    assert item["last_touched_in"] == "sess-old"
    assert item["revalued_in"] == "sess-new"


def test_revaluation_clamps_out_of_range_values():
    state = merger.apply_delta(_state_with(_item()), _delta([
        {"id": "dec-aaaa1111", "value": 7.5},
    ]))
    assert state["decisions"][0]["value"] == pytest.approx(1.0)


@pytest.mark.parametrize("bad", ["high", None, True, {"x": 1}])
def test_revaluation_ignores_non_numeric_value(bad):
    state = merger.apply_delta(_state_with(_item()), _delta([
        {"id": "dec-aaaa1111", "value": bad},
    ]))
    assert state["decisions"][0]["value"] == pytest.approx(0.5)


def test_revaluation_ignores_invalid_importance():
    state = merger.apply_delta(_state_with(_item()), _delta([
        {"id": "dec-aaaa1111", "importance": "critical"},
    ]))
    assert state["decisions"][0]["importance"] == "load_bearing"


def test_revaluation_of_unknown_id_is_a_noop():
    state = merger.apply_delta(_state_with(_item()), _delta([
        {"id": "dec-does-not-exist", "value": 0.9},
    ]))
    assert state["decisions"][0]["value"] == pytest.approx(0.5)
    applied = state["sessions"][0]["ledger_delta_applied"]["resolutions"]
    assert applied["revalued"] == []


def test_revaluation_is_recorded_on_the_session():
    state = merger.apply_delta(_state_with(_item()), _delta([
        {"id": "dec-aaaa1111", "value": 0.9},
    ]))
    applied = state["sessions"][0]["ledger_delta_applied"]["resolutions"]
    assert applied["revalued"] == ["dec-aaaa1111"]


def test_delta_without_revaluations_still_merges():
    """Backward compatibility — existing deltas have no revaluations key."""
    delta = {
        "session_id": "sess-new",
        "started": "2026-06-01T00:00:00Z",
        "ended": "2026-06-01T01:00:00Z",
        "ledger_delta": {"introduced": {}},
    }
    state = merger.apply_delta(_state_with(_item()), delta)
    assert len(state["sessions"]) == 1
    assert state["decisions"][0]["value"] == pytest.approx(0.5)
