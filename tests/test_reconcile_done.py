from __future__ import annotations

import copy

import pytest

import merger
from tools import reconcile_done

TS = "2026-08-27T00:00:00Z"


def _state():
    return {
        "project": "demo",
        "decisions": [
            {"id": "dec-cascade", "status": "archived", "text": "old filter",
             "archived_in": "old",
             "archived_reason": "superseded — replaced by the per-client filter"},
            {"id": "dec-regrade", "status": "archived", "text": "checks",
             "archived_in": "old",
             "archived_reason": "not design-shaping — still implemented"},
            {"id": "dec-unclear", "status": "archived", "text": "unclear",
             "archived_in": "old", "archived_reason": "retired after cleanup"},
            {"id": "dec-current", "status": "active", "text": "new filter"},
        ],
        "goals": [], "suggestions": [], "learnings": [],
        "done": [
            {"id": "work-orphan", "status": "active",
             "text": "Shipped the old filter", "decision_links": []},
            {"id": "work-live", "status": "active",
             "text": "Shipped checks", "decision_links": []},
        ],
        "sessions": [],
    }


def _delta(prepared, parent="dec-cascade"):
    quote = "replaced by the per-client filter"
    wrong = "A stranger would wrongly believe the old filter still ships."
    return {
        "session_id": "done-reconcile-demo-1", "started": TS, "ended": TS,
        "ledger_delta": {
            "introduced": {k: [] for k in
                           ("decisions", "goals", "suggestions", "learnings", "done")},
            "resolutions": {
                "closed": [], "rejected": [], "contradictions": [], "drift": [],
                "cascade_confirm": [], "cascade_reject": [],
                "archived": [{
                    "id": "work-orphan", "parent": parent,
                    "parent_reason_quote": quote, "wrong_belief": wrong,
                    "reason": f"superseded — work-orphan follows {parent}: {quote}. {wrong}",
                }],
            },
        },
        "reconciliation": {
            "input_fingerprint": prepared["input_fingerprint"],
            "examined_counts": {"active_done": 2, "cascade_candidates": 1,
                                "excluded_archived_decisions": 2},
            "ambiguous": [], "duplicates": [],
        },
    }


def test_prepare_partitions_full_population_and_is_stable():
    prepared = reconcile_done.build_reconciliation_input(_state())
    assert [x["id"] for x in prepared["active_done"]] == ["work-live", "work-orphan"]
    assert [x["id"] for x in prepared["cascade_candidates"]] == ["dec-cascade"]
    assert {x["id"]: x["archive_class"] for x in
            prepared["excluded_archived_decisions"]} == {
        "dec-regrade": "regrade", "dec-unclear": "unclassified"}
    assert [x["id"] for x in prepared["active_decisions"]] == ["dec-current"]
    assert prepared == reconcile_done.build_reconciliation_input(_state())


def test_valid_delta_uses_real_merge_path_and_replay_is_a_noop():
    state = _state()
    prepared = reconcile_done.build_reconciliation_input(state)
    delta = _delta(prepared)
    assert reconcile_done.validate_reconciliation_delta(
        state, prepared, delta)["archive_ids"] == ["work-orphan"]
    merged = merger.apply_delta(state, delta)
    orphan, live = (next(x for x in merged["done"] if x["id"] == iid)
                    for iid in ("work-orphan", "work-live"))
    assert (orphan["status"], orphan["archived_in"], orphan["archive_class"]) == (
        "archived", delta["session_id"], "cascade")
    assert orphan["archived_reason"] == delta["ledger_delta"]["resolutions"]["archived"][0]["reason"]
    assert live["status"] == "active"
    assert merger.apply_delta(merged, delta) is merged
    assert [x["session_id"] for x in merged["sessions"]] == [delta["session_id"]]


def test_regrade_parent_and_regrade_reason_are_non_triggers():
    state = _state()
    prepared = reconcile_done.build_reconciliation_input(state)
    with pytest.raises(reconcile_done.ReconciliationError, match="explicitly excluded"):
        reconcile_done.validate_reconciliation_delta(state, prepared,
                                                       _delta(prepared, "dec-regrade"))
    delta = _delta(prepared)
    delta["ledger_delta"]["resolutions"]["archived"][0]["reason"] = (
        "plumbing — dec-cascade is less important now")
    with pytest.raises(reconcile_done.ReconciliationError, match="cascade leading-clause"):
        reconcile_done.validate_reconciliation_delta(state, prepared, delta)
    assert all(x["status"] == "active" for x in state["done"])


def test_stale_or_incomplete_census_and_other_mutations_fail_closed():
    state = _state()
    prepared = reconcile_done.build_reconciliation_input(state)
    changed = copy.deepcopy(state)
    changed["done"].append({"id": "work-new", "status": "active", "text": "new"})
    with pytest.raises(reconcile_done.ReconciliationError, match="stale"):
        reconcile_done.validate_reconciliation_delta(changed, prepared, _delta(prepared))
    delta = _delta(prepared)
    delta["reconciliation"]["examined_counts"]["active_done"] = 1
    with pytest.raises(reconcile_done.ReconciliationError, match="examined_counts"):
        reconcile_done.validate_reconciliation_delta(state, prepared, delta)
    delta = _delta(prepared)
    delta["ledger_delta"]["revaluations"] = [{"id": "work-live", "value": 0.1}]
    with pytest.raises(reconcile_done.ReconciliationError, match="unsupported ledger_delta"):
        reconcile_done.validate_reconciliation_delta(state, prepared, delta)
