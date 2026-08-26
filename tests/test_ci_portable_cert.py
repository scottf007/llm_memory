"""CI-portable synthetic equivalents of the 19 live-corpus certification tests.

These exercise the same production paths (classify/backfill, cascade.apply,
certify.evaluate, claim_match U1-U4, merger.apply_delta pairing, renderer
quarantine) on hand-built fixtures. They do not reproduce live-corpus
counts (33/37/3, 397, hash pins of owner files) and they never read
~/.claude/memory.
"""
from __future__ import annotations

import copy
import hashlib
import json

import merger
import renderer
from lib import archive_class, cascade, certify, claim_match


TS = "2026-06-01T00:00:00Z"
SESSION_ID = "sess-synth"


def _state(**kinds):
    state = {
        "project": "synth",
        "summary": {"what": "synthetic certification fixture"},
        "operations": [],
        "decisions": [],
        "goals": [],
        "suggestions": [],
        "learnings": [],
        "done": [],
        "sessions": [],
        "cascade_reviews": [],
    }
    state.update(kinds)
    return state


def _item(iid, kind_text="item", **over):
    item = {
        "id": iid,
        "text": kind_text,
        "rationale": "",
        "status": "active",
        "importance": "standard",
        "last_touched_at": TS,
        "last_touched_in": "sess-old",
        "decision_links": [],
    }
    item.update(over)
    return item


def _archived_dec(iid, reason, **over):
    return _item(
        iid,
        status="archived",
        archived_in="sess1",
        archived_reason=reason,
        **over,
    )


def _delta(resolutions):
    return {
        "session_id": "sess-new",
        "started": TS,
        "ended": TS,
        "ledger_delta": {"introduced": {}, "resolutions": resolutions},
    }


# --- archive_class (never_whole_string / unclassified / split / backfill / evaluate)


def test_synth_mid_string_trigger_does_not_flip_class():
    """Leading-clause-only: a cascade term after the delimiter is not cascade."""
    reason = "not design-shaping -- this was superseded and later retired"
    assert "superseded" in reason and "retired" in reason
    assert archive_class.classify_archive_reason(reason) == "regrade"


def test_synth_unclassified_reasons_and_one_regrade():
    for reason in (
        "no clause this classifier recognizes at all",
        "historical note from an old session",
        "cascade from archived decision dec-aaaa1111 via id_link",
    ):
        assert archive_class.classify_archive_reason(reason) == "unclassified"
    assert archive_class.classify_archive_reason(
        "plumbing -- install.sh copy list"
    ) == "regrade"


def test_synth_corpus_classifies_mixed_reasons():
    state = _state(decisions=[
        _archived_dec("dec-c1", "reversed -- old claim"),
        _archived_dec("dec-c2", "no longer current -- replaced"),
        _archived_dec("dec-r1", "not design-shaping -- ops"),
        _archived_dec("dec-r2", "obvious -- restated in code"),
        _archived_dec("dec-u1", "no clause this classifier recognizes at all"),
    ])
    counts = {"cascade": 0, "regrade": 0, "lifecycle": 0, "unclassified": 0}
    unclassified_ids = []
    for d in state["decisions"]:
        cls = archive_class.classify_archive_reason(d["archived_reason"])
        counts[cls] += 1
        if cls == "unclassified":
            unclassified_ids.append(d["id"])
    assert counts == {"cascade": 2, "regrade": 2, "lifecycle": 0, "unclassified": 1}
    assert unclassified_ids == ["dec-u1"]


def test_synth_lifecycle_only_from_closure_prefix():
    goal = _item("goal-aaaa1111", "a goal")
    closed = merger.apply_delta(_state(goals=[goal]), _delta({
        "closed": [{"id": "goal-aaaa1111", "evidence": "shipped"}],
    }))
    assert closed["goals"][0]["archive_class"] == "lifecycle"

    rejected = merger.apply_delta(
        _state(goals=[_item("goal-bbbb2222", "other")]),
        _delta({"rejected": [{"id": "goal-bbbb2222", "reason": "won't do"}]}),
    )
    assert rejected["goals"][0]["archive_class"] == "lifecycle"

    for reason in (
        "closed: shipped in work-abc12345",
        "rejected: not aligned with current approach",
        "reversed -- the underlying claim no longer holds",
        "plumbing -- ops",
    ):
        assert archive_class.classify_archive_reason(reason) != "lifecycle"


def test_synth_backfill_is_idempotent_and_does_not_overwrite():
    state = _state(decisions=[
        _archived_dec("dec-c1", "reversed -- old"),
        _archived_dec("dec-r1", "plumbing -- ops", archive_class="regrade"),
        _item("dec-live", "still current"),
    ])
    first = archive_class.backfill(state)
    assert first == 1
    assert state["decisions"][0]["archive_class"] == "cascade"
    assert state["decisions"][1]["archive_class"] == "regrade"
    assert "archive_class" not in state["decisions"][2]
    assert archive_class.backfill(state) == 0


def test_synth_effective_class_uses_explicit_stamp_even_if_wrong():
    """certify._archived_by_class: explicit archive_class wins (disposition #28)."""
    stamped = _archived_dec(
        "dec-wrongclass",
        "reversed -- this text is cascade",
        archive_class="regrade",
    )
    computed = _archived_dec("dec-computed", "reversed -- cascade text")
    state = _state(decisions=[stamped, computed])
    by_class = certify._archived_by_class(state)
    ids = {cls: {d["id"] for d in items} for cls, items in by_class.items()}
    assert "dec-wrongclass" in ids["regrade"]
    assert "dec-wrongclass" not in ids["cascade"]
    assert "dec-computed" in ids["cascade"]


# --- cascade.apply


def test_synth_u1_learning_citation_is_out_of_cascade_scope():
    parent = _archived_dec("dec-cite0001", "reversed -- the underlying claim no longer holds")
    learning = _item(
        "lrn-cite0001",
        "This restates dec-cite0001 directly and completely.",
        importance="load_bearing",
    )
    done_unrelated = _item("work-unrelated1", "no citation here")
    state = _state(decisions=[parent], learnings=[learning], done=[done_unrelated])
    result = cascade.apply(state, SESSION_ID, TS)
    for bucket in ("archived", "proposed", "rejected"):
        assert not any(
            row.get("child") == "lrn-cite0001" or row.get("parent") == "dec-cite0001"
            for row in result[bucket]
        )
    assert learning["status"] == "active"


def test_synth_regrade_and_lifecycle_parents_never_archive():
    cascade_parent = _archived_dec(
        "dec-casc0001", "reversed -- the underlying claim no longer holds"
    )
    regrade_parent = _archived_dec("dec-regr0001", "plumbing -- ops")
    lifecycle_parent = _archived_dec(
        "dec-life0001", "closed: shipped", archive_class="lifecycle"
    )
    child = _item(
        "work-casc0001",
        "This restates dec-casc0001 and also names dec-regr0001 and dec-life0001.",
    )
    state = _state(
        decisions=[cascade_parent, regrade_parent, lifecycle_parent],
        done=[child],
    )
    result = cascade.apply(state, SESSION_ID, TS)
    archived_parents = {a["parent"] for a in result["archived"]}
    assert archived_parents <= {"dec-casc0001"}
    cascade_ids = {p["id"] for p in certify.parent_set_cascade(state)}
    assert cascade_ids == {"dec-casc0001"}


# --- certify.evaluate


def test_synth_u1_learning_citation_is_suspect_not_contradiction():
    parent = _archived_dec("dec-cite0002", "reversed -- the underlying claim no longer holds")
    learning = _item(
        "lrn-cite0002",
        "This restates dec-cite0002 directly and completely.",
        importance="load_bearing",
    )
    state = _state(decisions=[parent], learnings=[learning])
    cert = certify.evaluate(state, certify.eligible_item_ids(state))
    finding = next(
        f for f in cert.findings
        if f["child"] == "lrn-cite0002" and f["parent"] == "dec-cite0002"
    )
    assert finding["severity"] == "SUSPECT"
    assert finding["live_reversal"] == []
    assert finding["child_kind"] == "learnings"
    assert "lrn-cite0002" not in cert.quarantine_ids
    callouts = certify.suspect_callouts(cert)
    assert "lrn-cite0002" in callouts
    assert "U1" in callouts["lrn-cite0002"]


def test_synth_regrade_lifecycle_parents_structurally_excluded_from_certify():
    cascade_parent = _archived_dec("dec-casc0002", "reversed -- old claim")
    regrade_parent = _archived_dec("dec-regr0002", "plumbing -- ops")
    lifecycle_parent = _archived_dec(
        "dec-life0002", "closed: shipped", archive_class="lifecycle"
    )
    child = _item(
        "work-cite0002",
        "This restates dec-casc0002 and dec-regr0002 and dec-life0002.",
    )
    state = _state(
        decisions=[cascade_parent, regrade_parent, lifecycle_parent],
        done=[child],
    )
    cert = certify.evaluate(state, certify.eligible_item_ids(state))
    excluded = {"dec-regr0002", "dec-life0002"}
    assert not any(f["parent"] in excluded for f in cert.findings)
    assert {p["id"] for p in certify.parent_set_cascade(state)}.isdisjoint(excluded)


# --- claim_match U1-U4


def test_synth_u1_citation_matcher_positive():
    parent = {"id": "dec-aaaa1111", "text": "", "rationale": "", "archived_reason": ""}
    child = {
        "id": "lrn-bbbb2222",
        "text": "This restates dec-aaaa1111 directly.",
        "rationale": "",
        "decision_links": [],
    }
    r = claim_match.u1_id_link(parent, child)
    assert r is not None
    assert r.test == "U1" and r.tier == "exact"
    assert r.parent_id == "dec-aaaa1111" and r.child_id == "lrn-bbbb2222"


def test_synth_u2_quoted_span_and_five_token_floor():
    parent = {
        "id": "dec-quote001",
        "text": "",
        "rationale": "",
        "archived_reason": "reversed -- 'six token span right here now'",
    }
    child = {
        "id": "work-quote001",
        "text": "unrelated except the six token span right here now",
        "rationale": "",
        "decision_links": [],
    }
    r = claim_match.u2_quoted_old_claim(parent, child)
    assert r is not None
    assert r.test == "U2" and r.tier == "fuzzy"
    assert r.score == 6

    short_parent = dict(parent, archived_reason="reversed -- 'one two three four five'")
    short_child = dict(child, text="one two three four five is still here")
    assert claim_match.u2_quoted_old_claim(short_parent, short_child) is None

    replacement_only = dict(
        parent,
        archived_reason="reversed -- 'the unconditional load_bearing-always-renders exemption is gone'",
    )
    assert claim_match.u2_quoted_old_claim(replacement_only, child) is None


def test_synth_u2_possessive_apostrophe_false_positive_is_fuzzy_only():
    parent = {
        "id": "dec-apos0001",
        "text": "old claim",
        "rationale": "",
        "archived_reason": "reversed -- GLM's six token span right here now' leftover",
        "decision_links": [],
    }
    child = {
        "id": "dec-apos0002",
        "text": "s six token span right here now is the replacement wording",
        "rationale": "",
        "decision_links": [],
    }
    r = claim_match.u2_quoted_old_claim(parent, child)
    assert r is not None
    assert r.tier == "fuzzy" and r.test == "U2"
    assert claim_match.u1_id_link(parent, child) is None
    via_order = claim_match.match_one(parent, child)
    assert via_order is not None and via_order.tier == "fuzzy"


def test_synth_u3_under_threshold_substring_is_not_a_match():
    shared = "x" * 59
    parent = {"id": "dec-lcs0001", "text": "A" + shared + "Q", "rationale": "", "archived_reason": ""}
    child = {"id": "work-lcs0001", "text": "B" + shared + "Z", "rationale": "", "decision_links": []}
    n = claim_match.longest_common_substring_len(
        claim_match.normalize_text(child["text"]),
        claim_match.normalize_text(parent["text"]),
    )
    assert n == 59
    assert claim_match.u3_long_restatement(parent, child) is None


def test_synth_u4_two_token_minimum_and_parent_scope():
    parent = {
        "id": "dec-dead0001",
        "text": "",
        "rationale": "",
        "archived_reason": "retires records/ and memory_store from the design",
    }
    child = {
        "id": "work-dead0001",
        "text": "still touches records/ and memory_store directly",
        "rationale": "",
        "decision_links": [],
    }
    r = claim_match.u4_dead_substrate(parent, child)
    assert r is not None
    assert r.test == "U4" and r.tier == "fuzzy"
    assert r.score == 2

    one_token_parent = dict(parent, archived_reason="retires records/ from the design")
    assert claim_match.u4_dead_substrate(one_token_parent, child) is None

    unscoped = {
        "id": "dec-dead0002",
        "text": "an unrelated cascade parent",
        "rationale": "",
        "archived_reason": "reversed -- the underlying claim no longer holds",
    }
    surface = " ".join([unscoped["text"], unscoped["rationale"], unscoped["archived_reason"]])
    assert "records/" not in surface and "memory_store" not in surface
    assert claim_match.u4_dead_substrate(unscoped, child) is None
    assert "records/" in child["text"] and "memory_store" in child["text"]


# --- replay oracle apply_delta pairing + renderer omit-before-ranking


def _pair_invariant(oracle):
    parent = next(d for d in oracle["decisions"] if d["id"] == "dec-pair0001")
    child = next(w for w in oracle["done"] if w["id"] == "work-pair0001")
    return {
        "parent_status": parent["status"],
        "parent_class": parent.get("archive_class"),
        "child_status": child["status"],
        "child_class": child.get("archive_class"),
        "child_text_contains": "synthetic founding phrase" in (child.get("text") or ""),
    }


def test_synth_apply_delta_pair_invariant_is_hash_stable():
    snapshot = _state(
        decisions=[_item("dec-pair0001", "old claim that will be reversed")],
        done=[_item(
            "work-pair0001",
            "This restates dec-pair0001. synthetic founding phrase stays on the item.",
        )],
    )
    delta = _delta({
        "archived": [{
            "id": "dec-pair0001",
            "reason": "reversed -- the underlying claim no longer holds",
        }],
    })
    oracle = merger.apply_delta(copy.deepcopy(snapshot), delta)
    invariant = _pair_invariant(oracle)
    assert invariant["parent_status"] == "archived"
    assert invariant["parent_class"] == "cascade"
    assert invariant["child_status"] == "archived"
    assert invariant["child_class"] == "cascade"
    digest = hashlib.sha256(json.dumps(invariant, sort_keys=True).encode()).hexdigest()
    mutated = copy.deepcopy(oracle)
    child = next(w for w in mutated["done"] if w["id"] == "work-pair0001")
    child["status"] = "active"
    mutated_digest = hashlib.sha256(
        json.dumps(_pair_invariant(mutated), sort_keys=True).encode()
    ).hexdigest()
    assert mutated_digest != digest
    assert _pair_invariant(mutated) != invariant


def test_synth_source_hash_pins_are_verified_on_constructed_files(tmp_path):
    """Same contract as test_source_pins_match: hash the two source files,
    then load them — a byte change must miss the pin."""
    snapshot = {"project": "synth", "decisions": [{"id": "dec-x"}]}
    delta = {"session_id": "audit-synth", "ledger_delta": {"introduced": {}}}
    snap_bytes = json.dumps(snapshot, sort_keys=True).encode()
    delta_bytes = json.dumps(delta, sort_keys=True).encode()
    snap_path = tmp_path / "snapshot.json"
    delta_path = tmp_path / "delta.json"
    snap_path.write_bytes(snap_bytes)
    delta_path.write_bytes(delta_bytes)
    snap_pin = hashlib.sha256(snap_bytes).hexdigest()
    delta_pin = hashlib.sha256(delta_bytes).hexdigest()
    assert hashlib.sha256(snap_path.read_bytes()).hexdigest() == snap_pin
    assert hashlib.sha256(delta_path.read_bytes()).hexdigest() == delta_pin
    assert json.loads(snap_path.read_bytes())["project"] == "synth"
    assert json.loads(delta_path.read_bytes())["session_id"] == "audit-synth"
    snap_path.write_bytes(snap_bytes + b"\n")
    assert hashlib.sha256(snap_path.read_bytes()).hexdigest() != snap_pin


def test_synth_founding_phrase_omitted_before_ranking_via_quarantine():
    marker = "synthetic founding phrase never rendered after quarantine"
    reversal = _item("dec-newclaim1", "the current claim")
    parent = _archived_dec(
        "dec-oldclaim1",
        "reversed -- see dec-newclaim1 for the current claim",
        text="old claim text",
    )
    child = _item(
        "work-oldclaim1",
        f"This restates dec-oldclaim1 directly and completely. {marker}",
        importance="load_bearing",
    )
    state = _state(decisions=[parent, reversal], done=[child])
    md = renderer.render(state)
    assert marker not in md
    cert = certify.evaluate(state, certify.eligible_item_ids(state))
    finding = next(f for f in cert.findings if f["child"] == "work-oldclaim1")
    assert finding["severity"] == "CONTRADICTION"
    assert "work-oldclaim1" in cert.quarantine_ids

    # Non-trigger: no live reversal -> SUSPECT, load-bearing child still renders.
    suspect_parent = _archived_dec(
        "dec-oldclaim2",
        "reversed -- the underlying claim no longer holds",
        text="old claim text",
    )
    suspect_child = _item(
        "work-oldclaim2",
        f"This restates dec-oldclaim2 directly and completely. {marker}",
        importance="load_bearing",
    )
    suspect_state = _state(decisions=[suspect_parent], done=[suspect_child])
    suspect_md = renderer.render(suspect_state)
    assert marker in suspect_md
