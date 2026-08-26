"""Tests for lib/certify.py — render-time certification (open question 7,
disposition #14, #19, C3). Spec: SPEC-rev2-certification-cascade.md §6, §14.

Certification is reversible and mutates nothing on disk (§0): `evaluate`
never writes to `state`, `copy_with_status_override` is a true copy-on-write,
and `quarantine_set`/`suspect_callouts` are plain accessors over an
already-frozen `Certificate`, never re-derivations. CONTRADICTION requires
`child_kind == "done"` in addition to a cascade-class parent with a named
live reversal (disposition #7) — the certification-layer generalization of
"U1 is scope-safe, not categorically safe" (disposition #6).
"""

from tests.fixtures.certification.live_ledger import load_live_state, find

from lib import certify


LEDGER_KEYS = ("decisions", "goals", "suggestions", "learnings", "done")


def _base_state(**kinds):
    state = {
        "project": "testproj",
        "decisions": [], "goals": [], "suggestions": [],
        "learnings": [], "done": [], "sessions": [], "cascade_reviews": [],
    }
    state.update(kinds)
    return state


def _cascade_parent(pid, reversal_id=None, **over):
    reason = "reversed -- the underlying claim no longer holds"
    if reversal_id:
        reason = f"reversed -- see {reversal_id} for the current claim"
    p = {
        "id": pid, "text": "old claim text", "rationale": "",
        "archived_reason": reason, "status": "archived", "archived_in": "sess1",
    }
    p.update(over)
    return p


def _citing_item(iid, kind_hint_id, importance="standard", **over):
    item = {
        "id": iid, "status": "active", "importance": importance,
        "text": f"This restates {kind_hint_id} directly and completely.",
        "rationale": "",
    }
    item.update(over)
    return item


# --- disposition #7 / C3: child_kind == "done" severity gate ---------------

def test_cascade_parent_done_child_live_reversal_is_contradiction():
    reversal = {"id": "dec-fusereplace", "text": "the current claim",
                "status": "active", "importance": "standard"}
    parent = _cascade_parent("dec-eeee2222", reversal_id="dec-fusereplace")
    done_child = _citing_item("work-dddd3333", "dec-eeee2222")

    state = _base_state(decisions=[parent, reversal], done=[done_child])
    eligible = {reversal["id"], done_child["id"]}
    cert = certify.evaluate(state, eligible)

    finding = next(f for f in cert.findings if f["child"] == "work-dddd3333")
    assert finding["severity"] == "CONTRADICTION"
    assert finding["child_kind"] == "done"
    assert "dec-fusereplace" in finding["live_reversal"]

    # Non-trigger: identical parent/reversal, but the child is a learnings
    # item (not `done`) -> capped at SUSPECT, never CONTRADICTION.
    non_done_child = _citing_item("lrn-cccc4444", "dec-eeee2222")
    state2 = _base_state(decisions=[parent, reversal], learnings=[non_done_child])
    eligible2 = {reversal["id"], non_done_child["id"]}
    cert2 = certify.evaluate(state2, eligible2)
    finding2 = next(f for f in cert2.findings if f["child"] == "lrn-cccc4444")
    assert finding2["severity"] == "SUSPECT"


def test_u1_citation_pair_is_suspect_not_contradiction():
    """dec-06ca4291 -> lrn-18ac47d5 (live). `live_reversal` is empty for
    this parent (corrected, disposition C3) AND child_kind != 'done' --
    two independent reasons this pair is SUSPECT, never CONTRADICTION."""
    state = load_live_state()
    eligible = certify.eligible_item_ids(state)
    cert = certify.evaluate(state, eligible)

    finding = next(f for f in cert.findings
                   if f["child"] == "lrn-18ac47d5" and f["parent"] == "dec-06ca4291")
    assert finding["severity"] == "SUSPECT"
    assert finding["live_reversal"] == []
    assert finding["child_kind"] == "learnings"
    assert "lrn-18ac47d5" not in cert.quarantine_ids

    callouts = certify.suspect_callouts(cert)
    child = find(state, "learnings", "lrn-18ac47d5")
    assert child.get("importance") == "load_bearing"
    assert "lrn-18ac47d5" in callouts
    assert "dec-06ca4291" in callouts["lrn-18ac47d5"]
    assert "U1" in callouts["lrn-18ac47d5"]


def test_regrade_lifecycle_parents_structurally_excluded():
    state = load_live_state()
    eligible = certify.eligible_item_ids(state)
    cert = certify.evaluate(state, eligible)

    regrade_lifecycle_ids = {
        d["id"] for d in state["decisions"]
        if d.get("status") == "archived"
        and (d.get("archive_class") or certify.archive_class.classify_archive_reason(
            d.get("archived_reason"))) in ("regrade", "lifecycle")
    }
    assert not any(f["parent"] in regrade_lifecycle_ids for f in cert.findings)
    cascade_parent_ids = {p["id"] for p in certify.parent_set_cascade(state)}
    assert cascade_parent_ids.isdisjoint(regrade_lifecycle_ids)


def test_unclassified_parent_suspect_only():
    parent = {
        "id": "dec-unclass01", "text": "an old, undated claim", "rationale": "",
        "archived_reason": "no clause this classifier recognizes at all",
        "status": "archived", "archived_in": "sess1",
    }
    assert certify.archive_class.classify_archive_reason(parent["archived_reason"]) == "unclassified"
    done_child = _citing_item("work-unclass01", "dec-unclass01")

    state = _base_state(decisions=[parent], done=[done_child])
    cert = certify.evaluate(state, {done_child["id"]})

    finding = next(f for f in cert.findings if f["child"] == "work-unclass01")
    assert finding["severity"] == "SUSPECT"
    assert cert.verdict != "CONTRADICTION"
    assert "work-unclass01" not in cert.quarantine_ids


# --- fuse -------------------------------------------------------------------

def _fuse_state_with_k_contradictions(k):
    reversal = {"id": "dec-fusereplace", "text": "the current claim",
                "status": "active", "importance": "standard"}
    parent = _cascade_parent("dec-fuseparent", reversal_id="dec-fusereplace")
    children = [
        _citing_item(f"work-fusechild{i}", "dec-fuseparent")
        for i in range(k)
    ]
    state = _base_state(decisions=[parent, reversal], done=children)
    eligible = {reversal["id"]} | {c["id"] for c in children}
    filler = {f"filler-{i}" for i in range(187 - len(eligible))}
    return state, eligible | filler


def test_fuse_boundary():
    state4, eligible4 = _fuse_state_with_k_contradictions(4)
    cert4 = certify.evaluate(state4, eligible4)
    assert cert4.counts["rendered_eligible"] == 187
    assert cert4.verdict == "UNCERTIFIED"
    assert cert4.quarantine_ids == frozenset()

    state3, eligible3 = _fuse_state_with_k_contradictions(3)
    cert3 = certify.evaluate(state3, eligible3)
    assert cert3.counts["rendered_eligible"] == 187
    assert cert3.verdict != "UNCERTIFIED"
    assert len(cert3.quarantine_ids) == 3


def test_fuse_open_renders_all_findings_empties_quarantine():
    state, eligible = _fuse_state_with_k_contradictions(4)
    cert = certify.evaluate(state, eligible)
    assert cert.fuse_reason == "fuse_tripped"
    assert cert.quarantine_ids == frozenset()
    contradiction_findings = [f for f in cert.findings if f["severity"] == "CONTRADICTION"]
    assert len(contradiction_findings) == 4
    assert all(f["action"] == "rendered_fuse_open" for f in contradiction_findings)


def test_quarantine_set_is_frozen_not_rederived():
    state, eligible = _fuse_state_with_k_contradictions(3)
    cert = certify.evaluate(state, eligible)
    before = certify.quarantine_set(cert)
    assert len(before) == 3

    for item in state["done"]:
        item["status"] = "archived"
    state["done"] = []

    after = certify.quarantine_set(cert)
    assert after == before


def test_certificate_never_emits_pass():
    valid = {"NO_KNOWN_FALSEHOOD", "SUSPECT", "CONTRADICTION", "UNCERTIFIED"}

    clean = certify.evaluate(_base_state(), set())
    assert clean.verdict in valid

    state, eligible = _fuse_state_with_k_contradictions(1)
    dirty = certify.evaluate(state, eligible)
    assert dirty.verdict in valid

    fused_state, fused_eligible = _fuse_state_with_k_contradictions(4)
    fused = certify.evaluate(fused_state, fused_eligible)
    assert fused.verdict in valid

    for cert in (clean, dirty, fused):
        assert cert.verdict != "PASS"


def test_not_checked_names_all_three_classes():
    cert = certify.evaluate(_base_state(), set())
    classes = {n["class"] for n in cert.not_checked}
    assert classes == {
        "active_vs_active_contradiction", "unclassified_parents", "uncertified_regions",
    }


def test_copy_with_status_override_true_cow():
    quarantined = {"id": "work-q1", "status": "active", "text": "will be quarantined"}
    untouched = {"id": "work-u1", "status": "active", "text": "left alone"}
    state = _base_state(done=[quarantined, untouched])

    new_state = certify.copy_with_status_override(state, {"work-q1"})

    assert new_state["done"][0] is not state["done"][0]
    assert new_state["done"][0]["status"] == "quarantined"
    assert state["done"][0]["status"] == "active"  # source never mutated

    assert new_state["done"][1] is state["done"][1]  # untouched keeps identity


def test_dirty_verdict_plus_open_backlog():
    state, eligible = _fuse_state_with_k_contradictions(1)
    state["cascade_reviews"] = [{
        "child": "work-open01", "candidate_parents": ["dec-other01"],
        "reason_code": "lcs_92", "proposed_test": "U3", "status": "open",
        "first_seen_render": 0, "item_fingerprint": "sha256:x",
        "parent_set_fingerprint": "sha256:y", "resolved_in": None, "resolution_reason": None,
    }]
    dirty_cert = certify.evaluate(state, eligible)
    assert dirty_cert.verdict == "CONTRADICTION"
    assert dirty_cert.resolution_backlog["open_reviews"] > 0

    # Same open backlog, zero findings -> clean verdict, backlog still reported.
    clean_state = _base_state(cascade_reviews=state["cascade_reviews"])
    clean_cert = certify.evaluate(clean_state, set())
    assert clean_cert.verdict == "NO_KNOWN_FALSEHOOD"
    assert clean_cert.resolution_backlog["open_reviews"] > 0


def test_suspect_callouts_filters_to_load_bearing():
    parent = {
        "id": "dec-unclass02", "text": "an old, undated claim", "rationale": "",
        "archived_reason": "no clause this classifier recognizes at all",
        "status": "archived", "archived_in": "sess1",
    }
    lb_child = _citing_item("work-lb01", "dec-unclass02", importance="load_bearing")
    std_child = _citing_item("work-std01", "dec-unclass02", importance="standard")
    state = _base_state(decisions=[parent], done=[lb_child, std_child])
    eligible = {lb_child["id"], std_child["id"]}
    cert = certify.evaluate(state, eligible)

    assert {f["child"] for f in cert.findings if f["severity"] == "SUSPECT"} == {"work-lb01", "work-std01"}

    callouts = certify.suspect_callouts(cert)
    assert "work-lb01" in callouts
    assert "work-std01" not in callouts
