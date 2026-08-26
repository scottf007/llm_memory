"""Tests for lib/cascade.py — the gated archive action (M3). Spec:
SPEC-rev2-certification-cascade.md §7, §14.

Cascade is the terminal, monotone counterpart to certify's reversible
quarantine (§0): it mints a `decision_links` edge and archives a `done`
child, but only on U1-exact evidence or an explicit human/automated
confirmation (§7.2) — never on a fuzzy U2/U3/U4 signal alone (disposition
C1). Action order is load-bearing (disposition #21, extended per C5):
existing whole edge -> existing partial edge (routes to review, C5) -> U1
exact (mint + archive) -> fuzzy (review only). Rejected pairs are
permanent; invalidated pairs may re-propose (disposition C4).
"""

import pytest

from tests.fixtures.certification.live_ledger import load_live_state, find

import merger
from lib import archive_class, cascade, claim_match


SESSION_ID = "sess-cascade-test"
TS = "2026-06-01T00:00:00Z"
LEDGER_KEYS = ("decisions", "goals", "suggestions", "learnings", "done")


def _base_state(**kinds):
    state = {
        "project": "testproj",
        "decisions": [], "goals": [], "suggestions": [],
        "learnings": [], "done": [], "sessions": [], "cascade_reviews": [],
    }
    state.update(kinds)
    return state


def _cascade_parent(pid, **over):
    p = {
        "id": pid, "text": "old claim text", "rationale": "",
        "archived_reason": "reversed -- the underlying claim no longer holds",
        "status": "archived", "archived_in": "sess1",
    }
    p.update(over)
    return p


def _done_child(iid, **over):
    c = {
        "id": iid, "status": "active", "importance": "standard",
        "text": "a work item with no citation or quoted content at all",
        "rationale": "", "decision_links": [],
    }
    c.update(over)
    return c


def _review_row(child_id, parent_id, item_fp, parent_fp, **over):
    row = {
        "child": child_id, "candidate_parents": [parent_id],
        "reason_code": "lcs_92", "proposed_test": "U3",
        "status": "open", "first_seen_render": 0,
        "item_fingerprint": item_fp, "parent_set_fingerprint": parent_fp,
        "resolved_in": None, "resolution_reason": None,
    }
    row.update(over)
    return row


# --- action order (disposition #21, C5) -------------------------------------

def test_existing_whole_edge_archives_before_matcher_runs(monkeypatch):
    """Regression guard for B's blocking defect #1 (disposition #21): the
    existing-whole-edge check must short-circuit cascade.apply's per-parent
    loop before `claim_match.match_one` is ever called for this pair."""
    def _boom(parent, child):
        raise AssertionError("matcher must not be called when an existing whole edge exists")
    monkeypatch.setattr(claim_match, "match_one", _boom)

    parent = _cascade_parent("dec-wholeedge1")
    child = _done_child("work-wholeedge1", text="totally unrelated content",
                         decision_links=[{
                             "decision_id": "dec-wholeedge1",
                             "relation": "implements_current_claim", "scope": "whole",
                             "evidence_source": "extractor", "written_in": "sess0",
                         }])

    state = _base_state(decisions=[parent], done=[child])
    result = cascade.apply(state, SESSION_ID, TS)

    assert child["status"] == "archived"
    assert {"child": child["id"], "parent": parent["id"]} == {
        "child": result["archived"][0]["child"], "parent": result["archived"][0]["parent"],
    }


def test_u1_mints_edge_then_archives_same_pass():
    parent = _cascade_parent(
        "dec-mintedge1", archived_reason="reversed -- 'six token span right here now'")
    exact_child = _done_child("work-mintedge1",
                               text="This restates dec-mintedge1 directly.")
    fuzzy_child = _done_child(
        "work-mintedge2", text="unrelated except the six token span right here now",
    )
    state = _base_state(decisions=[parent], done=[exact_child, fuzzy_child])

    result = cascade.apply(state, SESSION_ID, TS)

    assert exact_child["status"] == "archived"
    links = exact_child["decision_links"]
    assert len(links) == 1
    assert links[0]["scope"] == "whole" and links[0]["evidence_source"] == "id_link"
    assert {"child": "work-mintedge1", "parent": "dec-mintedge1"} in [
        {"child": a["child"], "parent": a["parent"]} for a in result["archived"]
    ]

    # Fuzzy-only match: proposed, never archived.
    assert fuzzy_child["status"] == "active"
    assert not any(a["child"] == "work-mintedge2" for a in result["archived"])
    assert any(p["child"] == "work-mintedge2" for p in result["proposed"])


def test_u1_citation_pair_stays_active_no_edge():
    """dec-06ca4291 -> lrn-18ac47d5 (live): child kind is learnings, out of
    cascade's scope (done-only), so it can never appear anywhere in
    cascade.apply's result for this parent."""
    state = load_live_state()
    result = cascade.apply(state, SESSION_ID, TS)

    for bucket in ("archived", "proposed", "rejected"):
        assert not any(
            row.get("child") == "lrn-18ac47d5" or row.get("parent") == "dec-06ca4291"
            for row in result[bucket]
        )


def test_citation_shaped_done_item_archives_same_pass():
    """Known residual risk (§17, disposition #26), tested as a trigger, not
    silently left untested: a synthetic `done` item that whole-token-cites a
    cascade-class parent in prose, with no existing decision_links entry,
    archives same-pass under the current, unchanged U1 rule."""
    parent = _cascade_parent("dec-prosecite1")
    child = _done_child("work-prosecite1",
                         text="This restates dec-prosecite1 directly and completely.")
    state = _base_state(decisions=[parent], done=[child])

    result = cascade.apply(state, SESSION_ID, TS)

    assert child["status"] == "archived"
    assert any(a["child"] == "work-prosecite1" for a in result["archived"])


def test_partial_edge_suppresses_prose_u1_opens_review():
    """NEW, disposition C5. Same citation text as the row above, but the
    child already carries a recorded scope=='partial' edge for this exact
    parent -- direct contrast: does not archive, does not mint a whole
    edge, opens a review instead of falling through to the prose U1 scan."""
    parent = _cascade_parent("dec-prosecite2")
    child = _done_child(
        "work-prosecite2",
        text="This restates dec-prosecite2 directly and completely.",
        decision_links=[{
            "decision_id": "dec-prosecite2", "relation": "implements_current_claim",
            "scope": "partial", "evidence_source": "extractor", "written_in": "sess0",
        }],
    )
    state = _base_state(decisions=[parent], done=[child])

    result = cascade.apply(state, SESSION_ID, TS)

    assert child["status"] == "active"
    assert len(child["decision_links"]) == 1
    assert child["decision_links"][0]["scope"] == "partial"
    assert not any(a["child"] == "work-prosecite2" for a in result["archived"])

    review = next(r for r in state["cascade_reviews"] if r["child"] == "work-prosecite2")
    assert review["candidate_parents"] == ["dec-prosecite2"]
    assert review["reason_code"] == "partial_scope_edge"
    assert review["proposed_test"] == "U1_PARTIAL"
    assert review["status"] == "open"


def test_confirmed_partial_review_promotes_and_archives_same_apply_delta():
    """NEW-C5, corrected C5-a (VERDICT-rev1 §5.3) -- doubly load-bearing:
    confirming promotes the existing partial entry IN PLACE (count stays 1,
    not a second entry), the immediately-following cascade.apply archives
    the child, and no new 'open' row is appended for the pair (the
    unbounded-growth regression guard). Contrast: rejecting a U1_PARTIAL
    review is permanent -- the pair never re-proposes at any tier."""
    parent = _cascade_parent("dec-partial01")
    child = _done_child("work-partial01", decision_links=[{
        "decision_id": "dec-partial01", "relation": "implements_current_claim",
        "scope": "partial", "evidence_source": "extractor", "written_in": "sess0",
    }])
    state = _base_state(decisions=[parent], done=[child])
    item_fp = cascade.item_fingerprint(child)
    parent_fp = cascade.parent_set_fingerprint([parent])
    state["cascade_reviews"] = [_review_row(
        child["id"], parent["id"], item_fp, parent_fp,
        reason_code="partial_scope_edge", proposed_test="U1_PARTIAL",
    )]

    review_result = cascade.apply_review_resolutions(
        state, {"cascade_confirm": [{"child": child["id"], "parent": parent["id"]}]},
        SESSION_ID, TS,
    )
    assert review_result["confirmed"] == [child["id"]]

    cascade_result = cascade.apply(state, SESSION_ID, TS)

    links = child["decision_links"]
    assert len(links) == 1  # promoted in place, not a second entry
    assert links[0]["scope"] == "whole"
    assert links[0]["evidence_source"] == "review_confirmed"
    assert links[0]["proposed_test"] == "U1_PARTIAL"
    assert child["status"] == "archived"
    assert any(a["child"] == child["id"] and a["parent"] == parent["id"]
               for a in cascade_result["archived"])

    open_rows = [r for r in state["cascade_reviews"]
                 if r["child"] == child["id"] and r["status"] == "open"]
    assert open_rows == []  # no new open row minted by this cascade.apply call

    # Non-trigger: rejecting a U1_PARTIAL review is permanent.
    parent2 = _cascade_parent("dec-partial02")
    child2 = _done_child("work-partial02", decision_links=[{
        "decision_id": "dec-partial02", "relation": "implements_current_claim",
        "scope": "partial", "evidence_source": "extractor", "written_in": "sess0",
    }])
    state2 = _base_state(decisions=[parent2], done=[child2])
    fp2 = cascade.item_fingerprint(child2)
    pfp2 = cascade.parent_set_fingerprint([parent2])
    state2["cascade_reviews"] = [_review_row(
        child2["id"], parent2["id"], fp2, pfp2,
        reason_code="partial_scope_edge", proposed_test="U1_PARTIAL",
    )]
    cascade.apply_review_resolutions(
        state2, {"cascade_reject": [{"child": child2["id"], "parent": parent2["id"],
                                       "reason": "not a whole restatement"}]},
        SESSION_ID, TS,
    )
    for _ in range(2):
        cascade.apply(state2, SESSION_ID, TS)
    assert child2["status"] == "active"
    assert len(child2["decision_links"]) == 1
    assert child2["decision_links"][0]["scope"] == "partial"
    assert len(state2["cascade_reviews"]) == 1
    assert state2["cascade_reviews"][0]["status"] == "rejected"


def test_regrade_lifecycle_parents_never_archive():
    """Full corpus, pure negative control -- every archived-in-this-pass
    child's parent must be cascade-class; regrade/lifecycle parents are
    structurally excluded from cascade's own parent set."""
    state = load_live_state()

    result = cascade.apply(state, SESSION_ID, TS)
    archived_parent_ids = {a["parent"] for a in result["archived"]}
    for pid in archived_parent_ids:
        parent = find(state, "decisions", pid)
        cls = parent.get("archive_class") or archive_class.classify_archive_reason(
            parent.get("archived_reason"))
        assert cls == "cascade", pid


def test_rejected_pair_permanent():
    parent = _cascade_parent("dec-rejperm1")
    child = _done_child("work-rejperm1",
                         text="This restates dec-rejperm1 directly and completely.")
    state = _base_state(decisions=[parent], done=[child])
    state["cascade_reviews"] = [{
        "child": child["id"], "candidate_parents": [parent["id"]],
        "reason_code": "id_link", "proposed_test": "U1",
        "status": "rejected", "first_seen_render": 0,
        "item_fingerprint": "sha256:whatever", "parent_set_fingerprint": "sha256:whatever",
        "resolved_in": "sess0", "resolution_reason": "not a real restatement",
    }]

    for _ in range(2):
        result = cascade.apply(state, SESSION_ID, TS)
        assert not any(a["child"] == child["id"] for a in result["archived"])
        assert not any(p["child"] == child["id"] for p in result["proposed"])

    assert child["status"] == "active"
    assert len(state["cascade_reviews"]) == 1
    assert state["cascade_reviews"][0]["status"] == "rejected"


def test_invalidated_review_permits_fresh_open_row():
    parent = _cascade_parent("dec-invalfp1",
                              archived_reason="reversed -- 'six token span right here now'")
    child = _done_child("work-invalfp1",
                         text="unrelated except the six token span right here now")
    state = _base_state(decisions=[parent], done=[child])
    state["cascade_reviews"] = [_review_row(
        child["id"], parent["id"], "sha256:stale-item-fp",
        cascade.parent_set_fingerprint([parent]),
        reason_code="quoted_span_6tok", proposed_test="U2",
    )]

    review_result = cascade.apply_review_resolutions(
        state, {"cascade_confirm": [{"child": child["id"], "parent": parent["id"]}]},
        SESSION_ID, TS,
    )
    assert review_result["invalidated"] == [child["id"]]
    assert state["cascade_reviews"][0]["status"] == "invalidated"
    assert child["decision_links"] == []

    cascade.apply(state, SESSION_ID, TS)
    rows_for_pair = [r for r in state["cascade_reviews"]
                     if r["child"] == child["id"] and r["candidate_parents"] == [parent["id"]]]
    assert any(r["status"] == "open" for r in rows_for_pair)
    assert any(r["status"] == "invalidated" for r in rows_for_pair)

    # Non-trigger: a rejected row's fingerprint changing does not reopen it.
    parent2 = _cascade_parent("dec-invalfp2",
                               archived_reason="reversed -- 'six token span right here now'")
    child2 = _done_child("work-invalfp2",
                          text="unrelated except the six token span right here now")
    state2 = _base_state(decisions=[parent2], done=[child2])
    state2["cascade_reviews"] = [_review_row(
        child2["id"], parent2["id"], "sha256:stale-item-fp-2",
        cascade.parent_set_fingerprint([parent2]),
        reason_code="quoted_span_6tok", proposed_test="U2", status="rejected",
        resolved_in="sess0",
    )]
    cascade.apply(state2, SESSION_ID, TS)
    rows_for_pair2 = [r for r in state2["cascade_reviews"]
                      if r["child"] == child2["id"] and r["candidate_parents"] == [parent2["id"]]]
    assert len(rows_for_pair2) == 1
    assert rows_for_pair2[0]["status"] == "rejected"


def test_two_cascade_parents_same_child_lower_id_wins():
    lower = _cascade_parent("dec-1111aaaa")
    higher = _cascade_parent("dec-2222bbbb")
    child = _done_child(
        "work-twoparent1",
        text="This restates dec-1111aaaa and dec-2222bbbb directly.",
    )
    state = _base_state(decisions=[higher, lower], done=[child])

    result = cascade.apply(state, SESSION_ID, TS)

    assert len(result["archived"]) == 1
    assert result["archived"][0]["parent"] == "dec-1111aaaa"
    assert child["decision_links"][0]["decision_id"] == "dec-1111aaaa"


def test_cascade_idempotent_no_duplicates():
    parent = _cascade_parent("dec-idempotent1")
    child = _done_child("work-idempotent1",
                         text="This restates dec-idempotent1 directly and completely.")
    state = _base_state(decisions=[parent], done=[child])

    first = cascade.apply(state, SESSION_ID, TS)
    assert len(first["archived"]) == 1

    second = cascade.apply(state, SESSION_ID, TS)
    assert second["archived"] == []
    assert len(child["decision_links"]) == 1

    # A genuinely new parent-archive event produces exactly one new row.
    parent2 = _cascade_parent("dec-idempotent2")
    child2 = _done_child("work-idempotent2",
                          text="This restates dec-idempotent2 directly and completely.")
    state["decisions"].append(parent2)
    state["done"].append(child2)
    third = cascade.apply(state, SESSION_ID, TS)
    assert len(third["archived"]) == 1
    assert third["archived"][0]["child"] == "work-idempotent2"


def test_cycle_and_self_link_rejected():
    shared_id = "cyc-00000001"
    parent = _cascade_parent(shared_id)
    child = _done_child(shared_id)
    state = _base_state(decisions=[parent], done=[child])

    result = cascade.apply(state, SESSION_ID, TS)

    assert child["status"] == "active"
    assert result["archived"] == []
    assert result["rejected"] == [
        {"child": shared_id, "parent": shared_id, "reason_code": "cycle_or_self"},
    ]


def test_single_pass_no_recursion():
    """A child archived this pass must never be treated as a parent within
    the same call. Structurally, `done` items can never enter cascade's own
    decision-only parent pool -- this regression-guards that boundary: even
    after `work-singlepass1` is cascaded and stamped `archive_class ==
    'cascade'`, a second child that cites its id by name triggers no
    cascade action at all."""
    parent = _cascade_parent("dec-singlepass1")
    archived_child = _done_child(
        "work-singlepass1",
        text="This restates dec-singlepass1 directly and completely.",
    )
    citer = _done_child(
        "work-singlepass2",
        text="This restates work-singlepass1 directly and completely.",
    )
    state = _base_state(decisions=[parent], done=[archived_child, citer])

    result = cascade.apply(state, SESSION_ID, TS)

    assert archived_child["status"] == "archived"
    assert archived_child["archive_class"] == "cascade"
    assert citer["status"] == "active"
    assert not any(a["child"] == "work-singlepass2" for a in result["archived"])
    assert not any(p["child"] == "work-singlepass2" for p in result["proposed"])


# --- review resolution (§7.2, disposition #16, #18, #20, C4) ---------------

def test_confirm_writes_edge_with_proposed_test():
    """Regression guard for B's reason_code.split() bug (disposition #20):
    proposed_test on the written edge must be the review row's own value,
    verbatim, never derived by parsing reason_code."""
    parent = _cascade_parent("dec-confirmtest1")
    child = _done_child("work-confirmtest1")
    state = _base_state(decisions=[parent], done=[child])
    state["cascade_reviews"] = [_review_row(
        child["id"], parent["id"], cascade.item_fingerprint(child),
        cascade.parent_set_fingerprint([parent]),
        reason_code="lcs_92", proposed_test="U3",
    )]

    cascade.apply_review_resolutions(
        state, {"cascade_confirm": [{"child": child["id"], "parent": parent["id"]}]},
        SESSION_ID, TS,
    )

    links = child["decision_links"]
    assert len(links) == 1
    assert links[0]["proposed_test"] == "U3"
    assert links[0]["evidence_source"] == "review_confirmed"


def test_confirm_then_archive_same_apply_delta():
    parent = _cascade_parent("dec-confirmarchive1")
    child = _done_child("work-confirmarchive1")
    state = _base_state(decisions=[parent], done=[child])
    state["cascade_reviews"] = [_review_row(
        child["id"], parent["id"], cascade.item_fingerprint(child),
        cascade.parent_set_fingerprint([parent]),
        reason_code="lcs_92", proposed_test="U3",
    )]

    cascade.apply_review_resolutions(
        state, {"cascade_confirm": [{"child": child["id"], "parent": parent["id"]}]},
        SESSION_ID, TS,
    )
    result = cascade.apply(state, SESSION_ID, TS)

    assert child["status"] == "archived"
    assert any(a["child"] == child["id"] for a in result["archived"])


def test_reject_permanent_no_reproposal():
    parent = _cascade_parent("dec-rejectperm1",
                              archived_reason="reversed -- 'six token span right here now'")
    child = _done_child("work-rejectperm1",
                         text="unrelated except the six token span right here now")
    state = _base_state(decisions=[parent], done=[child])
    state["cascade_reviews"] = [_review_row(
        child["id"], parent["id"], cascade.item_fingerprint(child),
        cascade.parent_set_fingerprint([parent]),
        reason_code="quoted_span_6tok", proposed_test="U2",
    )]

    cascade.apply_review_resolutions(
        state, {"cascade_reject": [{"child": child["id"], "parent": parent["id"],
                                      "reason": "coincidental phrase overlap"}]},
        SESSION_ID, TS,
    )
    for _ in range(2):
        cascade.apply(state, SESSION_ID, TS)

    rows = [r for r in state["cascade_reviews"] if r["child"] == child["id"]]
    assert len(rows) == 1
    assert rows[0]["status"] == "rejected"


def test_stale_item_fingerprint_invalidates_row_not_whole_delta():
    """CORRECTED, disposition C4 -- one policy, code/docstring/test agree:
    per-row invalidation, never whole-delta rejection."""
    stale_parent = _cascade_parent("dec-stalefp1")
    stale_child = _done_child("work-stalefp1")
    valid_parent = _cascade_parent("dec-stalefp2")
    valid_child = _done_child("work-stalefp2")
    state = _base_state(decisions=[stale_parent, valid_parent],
                         done=[stale_child, valid_child])
    state["cascade_reviews"] = [
        _review_row(stale_child["id"], stale_parent["id"], "sha256:stale-value",
                    cascade.parent_set_fingerprint([stale_parent, valid_parent])),
        _review_row(valid_child["id"], valid_parent["id"],
                    cascade.item_fingerprint(valid_child),
                    cascade.parent_set_fingerprint([stale_parent, valid_parent])),
    ]

    result = cascade.apply_review_resolutions(
        state, {"cascade_confirm": [
            {"child": stale_child["id"], "parent": stale_parent["id"]},
            {"child": valid_child["id"], "parent": valid_parent["id"]},
        ]}, SESSION_ID, TS,
    )

    assert result["invalidated"] == [stale_child["id"]]
    assert result["confirmed"] == [valid_child["id"]]
    assert stale_child["decision_links"] == []
    assert len(valid_child["decision_links"]) == 1
    stale_review = next(r for r in state["cascade_reviews"] if r["child"] == stale_child["id"])
    assert stale_review["status"] == "invalidated"
    valid_review = next(r for r in state["cascade_reviews"] if r["child"] == valid_child["id"])
    assert valid_review["status"] == "confirmed"


def test_stale_parent_set_fingerprint_invalidates_row_not_whole_delta():
    """NEW-C4. item_fingerprint matches, but the cascade-relevant parent
    pool changed underneath the review (a new cascade-class decision
    appeared since proposal) -- invalidates the row, writes no edge."""
    parent = _cascade_parent("dec-staleparentfp1")
    child = _done_child("work-staleparentfp1")
    state = _base_state(decisions=[parent], done=[child])
    # Fingerprint captured at "proposal time", before the second parent
    # below existed in the cascade-relevant pool.
    proposal_parent_fp = cascade.parent_set_fingerprint([parent])
    state["cascade_reviews"] = [_review_row(
        child["id"], parent["id"], cascade.item_fingerprint(child), proposal_parent_fp,
    )]
    # A new cascade-class decision appears before resolution.
    state["decisions"].append(_cascade_parent("dec-staleparentfp2"))

    result = cascade.apply_review_resolutions(
        state, {"cascade_confirm": [{"child": child["id"], "parent": parent["id"]}]},
        SESSION_ID, TS,
    )

    assert result["invalidated"] == [child["id"]]
    assert child["decision_links"] == []

    # Non-trigger: both fingerprints matching proceeds normally.
    parent_b = _cascade_parent("dec-staleparentfp3")
    child_b = _done_child("work-staleparentfp3")
    state_b = _base_state(decisions=[parent_b], done=[child_b])
    state_b["cascade_reviews"] = [_review_row(
        child_b["id"], parent_b["id"], cascade.item_fingerprint(child_b),
        cascade.parent_set_fingerprint([parent_b]),
    )]
    result_b = cascade.apply_review_resolutions(
        state_b, {"cascade_confirm": [{"child": child_b["id"], "parent": parent_b["id"]}]},
        SESSION_ID, TS,
    )
    assert result_b["confirmed"] == [child_b["id"]]
    assert len(child_b["decision_links"]) == 1


def test_missing_or_duplicate_review_rejects_whole_delta():
    """Malformed input is a different failure class from staleness
    (disposition C4): unknown or duplicate (child, parent) keys raise
    before any write, rejecting the whole delta."""
    parent = _cascade_parent("dec-malformed1")
    child = _done_child("work-malformed1")
    state = _base_state(decisions=[parent], done=[child])
    state["cascade_reviews"] = [_review_row(
        child["id"], parent["id"], cascade.item_fingerprint(child),
        cascade.parent_set_fingerprint([parent]),
    )]

    with pytest.raises(ValueError):
        cascade.apply_review_resolutions(
            state, {"cascade_confirm": [
                {"child": "work-does-not-exist", "parent": parent["id"]},
            ]}, SESSION_ID, TS,
        )
    assert child["decision_links"] == []  # rejected before any write

    # Duplicate: confirm the same (child, parent) pair twice in one delta.
    with pytest.raises(ValueError):
        cascade.apply_review_resolutions(
            state, {"cascade_confirm": [
                {"child": child["id"], "parent": parent["id"]},
                {"child": child["id"], "parent": parent["id"]},
            ]}, SESSION_ID, TS,
        )


def test_res_summary_carries_four_new_keys():
    """Integration-level, exercised through merger.apply_delta (§8.1):
    cascade_confirm/cascade_reject/cascaded/cascade_invalidated all
    populate on relevant activity."""
    parent = _cascade_parent("dec-ressummary1")
    child = _done_child("work-ressummary1")
    state = _base_state(decisions=[parent], done=[child])
    state["cascade_reviews"] = [_review_row(
        child["id"], parent["id"], cascade.item_fingerprint(child),
        cascade.parent_set_fingerprint([parent]),
    )]

    delta = {
        "session_id": "sess-ressummary",
        "started": TS, "ended": TS,
        "ledger_delta": {"introduced": {}},
        "resolutions": {"cascade_confirm": [
            {"child": child["id"], "parent": parent["id"]},
        ]},
    }
    state = merger.apply_delta(state, delta)

    applied = state["sessions"][-1]["ledger_delta_applied"]["resolutions"]
    assert applied["cascade_confirm"] == [child["id"]]
    assert applied["cascade_reject"] == []
    assert applied["cascade_invalidated"] == []
    assert applied["cascaded"] == [child["id"]]

    # Non-trigger: a delta with none of the four activities leaves all four [].
    parent2 = _cascade_parent("dec-ressummary2")
    other_child = _done_child("work-ressummary2")
    state2 = _base_state(decisions=[parent2], done=[other_child])
    delta2 = {
        "session_id": "sess-ressummary-none",
        "started": TS, "ended": TS,
        "ledger_delta": {"introduced": {}},
    }
    state2 = merger.apply_delta(state2, delta2)
    applied2 = state2["sessions"][-1]["ledger_delta_applied"]["resolutions"]
    assert applied2["cascade_confirm"] == []
    assert applied2["cascade_reject"] == []
    assert applied2["cascade_invalidated"] == []
    assert applied2["cascaded"] == []
