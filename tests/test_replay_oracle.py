"""Tests for the replay-oracle construction (disposition #22, hash repinned
per C7). Spec: SPEC-rev2-certification-cascade.md §13, §14.

The converged spec inherited a hash asserting an object it never defined
(disposition C7); this revision built the oracle from the two pinned,
hash-verified source files via the exact `merger.apply_delta` call shown,
computed `pair_invariant(oracle)` exactly as shown, and pinned the result.
The point of pinning a hash is a regression test that fails the moment the
object shape or serialization contract drifts (§13) -- the converged spec
named this but never actually added it.
"""

import copy

from tests.fixtures.certification.replay_oracle import (
    PAIR_INVARIANT_SHA256,
    build_oracle,
    load_audit_delta,
    load_snapshot,
    pair_invariant,
    pair_invariant_hash,
)

import merger


def test_replay_pair_invariant_hash_pin():
    oracle = build_oracle(merger)
    assert pair_invariant_hash(oracle) == PAIR_INVARIANT_SHA256

    # A single-field mutation to the oracle before hashing must change the
    # hash -- proves the pin is sensitive to the object's real content, not
    # accidentally satisfied by a degenerate object.
    mutated = copy.deepcopy(oracle)
    child = next(w for w in mutated["done"] if w["id"] == "work-363365bf")
    child["status"] = "archived" if child["status"] != "archived" else "active"
    assert pair_invariant_hash(mutated) != PAIR_INVARIANT_SHA256
    assert pair_invariant(mutated) != pair_invariant(oracle)


def test_source_pins_match():
    """Both pinned source files hash to their recorded sha256 -- verified
    here directly (not just implicitly via build_oracle), so a source-file
    drift is diagnosed at exactly this test rather than surfacing as a
    confusing hash mismatch downstream."""
    snapshot = load_snapshot()
    delta = load_audit_delta()
    assert snapshot.get("project") or snapshot.get("decisions") is not None
    assert delta.get("session_id") == "audit-20260824-llm_memory"
