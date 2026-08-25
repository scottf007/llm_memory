"""Tests for lib/archive_class.py — the leading-clause archive classifier
(M1). Spec: SPEC-rev2-certification-cascade.md §4, §14.

`classify_archive_reason` only ever recognizes B's leading-clause vocabulary
(disposition #2); it must never treat "cascade"/"lifecycle" as parser
outputs (disposition C1) — those are call-site overrides written directly
by merger.py's cascade and closed/rejected paths. The corpus-level tests
below assert the frozen, mechanically-measured 33/37/3 split (§4) against
the live ledger, not a hand-estimated split — do not "fix" a failing
corpus test by tuning the vocabulary; that would smuggle a human read into
a frozen, versioned parser (§6.1).
"""

from tests.fixtures.certification.live_ledger import load_live_state, find

from lib import archive_class
import merger


BASE_TS = "2026-01-01T00:00:00Z"
LEDGER_KEYS = ("decisions", "goals", "suggestions", "learnings", "done")


def _state_with_goal(**over):
    goal = {
        "id": "goal-aaaa1111",
        "text": "a goal",
        "status": "active",
        "importance": "standard",
        "last_touched_at": BASE_TS,
        "last_touched_in": "sess-old",
    }
    goal.update(over)
    return {
        "project": "testproj",
        "decisions": [], "goals": [goal], "suggestions": [],
        "learnings": [], "done": [], "sessions": [],
    }


def _delta(resolutions):
    return {
        "session_id": "sess-new",
        "started": "2026-06-01T00:00:00Z",
        "ended": "2026-06-01T01:00:00Z",
        "ledger_delta": {"introduced": {}, "resolutions": resolutions},
    }


# --- pure classifier, synthetic vocabulary coverage -----------------------

def test_cascade_leading_clauses():
    for term in archive_class.CASCADE_LEADING_CLAUSES:
        reason = f"{term} — some free-text explanation of what changed"
        assert archive_class.classify_archive_reason(reason) == "cascade", term


def test_regrade_leading_clauses():
    for term in archive_class.REGRADE_LEADING_CLAUSES:
        reason = f"{term} — some free-text explanation of what changed"
        assert archive_class.classify_archive_reason(reason) == "regrade", term


def test_never_whole_string_contains():
    """Leading-clause-only algorithm: a trigger word appearing mid-string,
    after the delimiter, must never flip the classification — only B's
    split+startswith-on-leading-clause counts (disposition #2)."""
    state = load_live_state()
    dfdf49f6 = find(state, "decisions", "dec-dfdf49f6")
    a1aa8146 = find(state, "decisions", "dec-a1aa8146")
    assert "superseded" in dfdf49f6["archived_reason"]
    assert archive_class.classify_archive_reason(dfdf49f6["archived_reason"]) == "regrade"
    assert "retired" in a1aa8146["archived_reason"]
    assert archive_class.classify_archive_reason(a1aa8146["archived_reason"]) == "regrade"


def test_unclassified_trio_exact():
    state = load_live_state()
    for iid in ("dec-24585827", "dec-2ac9a8c0", "dec-8509c046"):
        d = find(state, "decisions", iid)
        assert archive_class.classify_archive_reason(d["archived_reason"]) == "unclassified", iid
    d57c2dca = find(state, "decisions", "dec-d57c2dca")
    assert archive_class.classify_archive_reason(d57c2dca["archived_reason"]) == "regrade"


# --- full live corpus -------------------------------------------------------

def test_full_corpus_reproduces_33_37_3():
    state = load_live_state()
    archived = [d for d in state.get("decisions", [])
                if d.get("archived_in") or d.get("status") == "archived"]
    assert len(archived) == 73

    counts = {"cascade": 0, "regrade": 0, "lifecycle": 0, "unclassified": 0}
    unclassified_ids = []
    for d in archived:
        cls = archive_class.classify_archive_reason(d.get("archived_reason"))
        counts[cls] += 1
        if cls == "unclassified":
            unclassified_ids.append(d["id"])

    assert counts == {"cascade": 33, "regrade": 37, "lifecycle": 0, "unclassified": 3}
    assert sorted(unclassified_ids) == ["dec-24585827", "dec-2ac9a8c0", "dec-8509c046"]


def test_lifecycle_only_from_closure_prefix():
    """`classify_archive_reason` alone never recognizes closed:/rejected: —
    `archive_class == "lifecycle"` is only reachable via merger.py's two
    inline call-site overrides (§8.2), never via the parser."""
    closed_state = merger.apply_delta(_state_with_goal(), _delta({
        "closed": [{"id": "goal-aaaa1111", "evidence": "shipped"}],
    }))
    assert closed_state["goals"][0]["archive_class"] == "lifecycle"

    rejected_state = merger.apply_delta(
        _state_with_goal(id="goal-bbbb2222"),
        _delta({"rejected": [{"id": "goal-bbbb2222", "reason": "won't do"}]}),
    )
    assert rejected_state["goals"][0]["archive_class"] == "lifecycle"

    state = load_live_state()
    archived_decisions = [d for d in state.get("decisions", [])
                           if d.get("archived_in") or d.get("status") == "archived"]
    assert not any(
        archive_class.classify_archive_reason(d.get("archived_reason")) == "lifecycle"
        for d in archived_decisions
    )


def test_backfill_397_then_zero():
    state = load_live_state()
    for kind in LEDGER_KEYS:
        for item in state.get(kind, []):
            item.pop("archive_class", None)

    first = archive_class.backfill(state)
    assert first == 397

    second = archive_class.backfill(state)
    assert second == 0


def test_evaluate_computes_archive_class_if_absent():
    from lib import certify

    state = load_live_state()
    for d in state.get("decisions", []):
        d.pop("archive_class", None)
    # A decision with an already-correct explicit class must never be
    # recomputed/overwritten (disposition #28).
    pre_stamped = find(state, "decisions", "dec-06ca4291")
    pre_stamped["archive_class"] = "regrade"  # deliberately wrong vs. its real "cascade" text

    by_class = certify._archived_by_class(state)
    assert len(by_class["cascade"]) + len(by_class["regrade"]) + len(by_class["unclassified"]) == 73
    ids_by_class = {cls: {d["id"] for d in items} for cls, items in by_class.items()}
    assert "dec-06ca4291" in ids_by_class["regrade"]
    assert "dec-06ca4291" not in ids_by_class["cascade"]


# --- disposition C1, new -----------------------------------------------------

def test_classify_archive_reason_never_recognizes_call_site_overrides():
    """The classifier's blindness is specific to call-site-override shapes
    (cascade-generated reasons, closed:/rejected: prefixes) — not a general
    parsing failure. This is the exact fact §8.3's inbox_merge fix (C1)
    depends on: a caller MUST NOT recompute archive_class from this
    function alone on an item whose class was set by a call-site override."""
    cascade_reason = "cascade from archived decision dec-51544bf2 via id_link"
    assert archive_class.classify_archive_reason(cascade_reason) == "unclassified"

    closed_reason = "closed: shipped in work-abc12345"
    assert archive_class.classify_archive_reason(closed_reason) == "unclassified"

    rejected_reason = "rejected: not aligned with current approach"
    assert archive_class.classify_archive_reason(rejected_reason) == "unclassified"

    # Contrast: a genuine parser-recognized cascade clause still classifies —
    # the blindness above is specific to the generated-override shapes, not
    # a general failure of the classifier.
    genuine_reason = "reversed -- the underlying claim no longer holds"
    assert archive_class.classify_archive_reason(genuine_reason) == "cascade"
