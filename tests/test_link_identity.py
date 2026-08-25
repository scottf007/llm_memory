"""Post-gate follow-up: F-1, F-2 and the §8.4 errno guard.

F-1 is the defect no frozen row could see. `merger.inbox_merge` keyed
decision_links identity on `(decision_id, relation, scope)`, but a C5-a
promotion MUTATES `scope` on an existing entry — so a promoted edge arriving
from another machine looked like a key the receiver had never seen, and was
appended alongside the stale `partial` it was meant to replace. The record
then said both "partial restatement" and "whole restatement" for the same
(child, parent), permanently.

The property under test is CONVERGENCE, not just non-duplication: after any
sequence of local promotions and syncs, in any order, a given (child, parent)
ends with exactly ONE edge, and if any side ever said `whole`, it is `whole`.
Tests are grouped trigger / non-trigger — the non-trigger rows assert the
behaviour that must NOT change, and are the guard against a fix that
over-collapses.
"""

import errno
import json
import os
import stat

import pytest

import merger
from lib import cascade

PROJECT = "example_project"
BASE_TS = "2026-06-01T00:00:00Z"
RELATION = "implements_current_claim"


# --- fixtures ---------------------------------------------------------------

def _link(decision_id="dec-p1", scope="partial", evidence_source="extractor",
          written_in="sess-a", relation=RELATION, **over):
    entry = {
        "decision_id": decision_id, "relation": relation, "scope": scope,
        "evidence_source": evidence_source, "written_in": written_in,
    }
    entry.update(over)
    return entry


def _child(iid="work-sync1", links=None):
    return {
        "id": iid, "status": "active", "importance": "standard",
        "text": "a work item with no citation or quoted content at all",
        "rationale": "", "last_touched_at": BASE_TS,
        "decision_links": [] if links is None else [dict(l) for l in links],
    }


def _state(*children):
    return {"project": PROJECT, "done": [dict(c) for c in children]}


def _publish(state, items_root):
    """Write every ledger item out as a per-item file, as fan_out_items does.

    This is the replication carrier: Syncthing copies these files between
    machines, and the receiving side picks them up via inbox_merge.
    """
    kind_dir = items_root / PROJECT / "done"
    kind_dir.mkdir(parents=True, exist_ok=True)
    for item in state["done"]:
        payload = dict(item, kind="done", project=PROJECT)
        (kind_dir / f"{item['id']}.json").write_text(json.dumps(payload))


def _sync(src_state, dst_state, dst_root):
    """Replicate src's items into dst's inbox and merge them into dst."""
    _publish(src_state, dst_root)
    return merger.inbox_merge(dst_state, PROJECT, dst_root)


def _links_of(state, iid="work-sync1"):
    return next(i for i in state["done"] if i["id"] == iid)["decision_links"]


# --- F-1: the promoted edge must survive replication ------------------------

def test_promoted_edge_does_not_duplicate_on_sync(tmp_path):
    """TRIGGER — the merge-gate judge's exact reproduction.

    Machine A holds a scope="partial" extractor edge. Machine B promotes it
    in place to scope="whole" (review_confirmed, U1_PARTIAL). B's item file
    replicates to A. A must recognise the promotion as the SAME edge.
    """
    a_root = tmp_path / "a-items"
    state_a = _state(_child(links=[_link(scope="partial")]))
    state_b = _state(_child(links=[_link(
        scope="whole", evidence_source="review_confirmed",
        written_in="sess-b", proposed_test="U1_PARTIAL")]))

    _sync(state_b, state_a, a_root)

    links = _links_of(state_a)
    assert len(links) == 1, f"promotion appended instead of reconciling: {links}"
    assert links[0]["scope"] == "whole"
    assert links[0]["evidence_source"] == "review_confirmed"
    assert links[0]["proposed_test"] == "U1_PARTIAL"


def test_sync_convergence_is_order_independent(tmp_path):
    """TRIGGER — the property that actually matters.

    A->B->A and B->A->B must reach the same state. `whole` is absorbing, so
    the union is commutative and idempotent and replication order cannot
    change the answer.
    """
    def run(order):
        roots = {"a": tmp_path / f"{order}-a", "b": tmp_path / f"{order}-b"}
        states = {
            "a": _state(_child(links=[_link(scope="partial")])),
            "b": _state(_child(links=[_link(
                scope="whole", evidence_source="review_confirmed",
                written_in="sess-b", proposed_test="U1_PARTIAL")])),
        }
        for src, dst in order:
            _sync(states[src], states[dst], roots[dst])
        return states

    forward = run([("a", "b"), ("b", "a"), ("a", "b")])
    reverse = run([("b", "a"), ("a", "b"), ("b", "a")])

    for machine in ("a", "b"):
        for states in (forward, reverse):
            links = _links_of(states[machine])
            assert len(links) == 1, f"{machine}: {links}"
            assert links[0]["scope"] == "whole"

    assert _links_of(forward["a"]) == _links_of(reverse["a"])
    assert _links_of(forward["b"]) == _links_of(reverse["b"])


def test_incoming_partial_never_demotes_a_local_whole(tmp_path):
    """TRIGGER — `whole` is absorbing in the other direction too.

    A machine that has already promoted must not be walked back by a peer
    still carrying the pre-promotion `partial`.
    """
    root = tmp_path / "items"
    state_local = _state(_child(links=[_link(
        scope="whole", evidence_source="review_confirmed",
        written_in="sess-b", proposed_test="U1_PARTIAL")]))
    state_stale = _state(_child(links=[_link(scope="partial")]))

    _sync(state_stale, state_local, root)

    links = _links_of(state_local)
    assert len(links) == 1, f"stale partial was appended: {links}"
    assert links[0]["scope"] == "whole"
    assert links[0]["evidence_source"] == "review_confirmed"


def test_later_confirm_after_sync_makes_no_duplicate_whole(tmp_path):
    """TRIGGER — the sharper second-order half of F-1.

    `_write_decision_link` takes the FIRST entry matching the parent. Once a
    stale `partial` has been appended by sync, that first match IS the stale
    one, and a later U1_PARTIAL confirm promotes it too — yielding two
    identical `whole` entries, precisely what C5-a exists to prevent.
    """
    root = tmp_path / "items"
    state_a = _state(_child(links=[_link(scope="partial")]))
    state_b = _state(_child(links=[_link(
        scope="whole", evidence_source="review_confirmed",
        written_in="sess-b", proposed_test="U1_PARTIAL")]))

    _sync(state_b, state_a, root)

    child = state_a["done"][0]
    cascade._write_decision_link(
        child, {"id": "dec-p1"}, "review_confirmed", "sess-c",
        proposed_test="U1_PARTIAL")

    links = child["decision_links"]
    whole = [l for l in links if l.get("scope") == "whole"]
    assert len(links) == 1, f"expected a single reconciled edge, got {links}"
    assert len(whole) == 1, f"duplicate whole entries: {whole}"


def test_preexisting_duplicate_pair_collapses_on_merge(tmp_path):
    """TRIGGER — self-healing.

    A machine that already ran the pre-fix union carries a contradictory
    partial+whole pair. The repaired union must collapse it rather than
    preserve it forever, so the invariant is unconditional and not merely
    true going forward.
    """
    root = tmp_path / "items"
    state_local = _state(_child(links=[
        _link(scope="partial"),
        _link(scope="whole", evidence_source="review_confirmed",
              written_in="sess-b", proposed_test="U1_PARTIAL"),
    ]))
    state_peer = _state(_child(links=[_link(
        scope="whole", evidence_source="review_confirmed",
        written_in="sess-b", proposed_test="U1_PARTIAL")]))

    _sync(state_peer, state_local, root)

    links = _links_of(state_local)
    assert len(links) == 1, f"legacy duplicate pair not collapsed: {links}"
    assert links[0]["scope"] == "whole"


# --- F-1 non-triggers: the union must not over-collapse ---------------------

def test_union_still_appends_a_genuinely_new_parent(tmp_path):
    """NON-TRIGGER — reconciling on (decision_id, relation) must not swallow
    an edge to a DIFFERENT parent. Distinct parents stay distinct edges."""
    root = tmp_path / "items"
    state_local = _state(_child(links=[_link(decision_id="dec-p1", scope="partial")]))
    state_peer = _state(_child(links=[
        _link(decision_id="dec-p1", scope="partial"),
        _link(decision_id="dec-p2", scope="whole", written_in="sess-b"),
    ]))

    _sync(state_peer, state_local, root)

    links = _links_of(state_local)
    assert len(links) == 2
    assert {l["decision_id"] for l in links} == {"dec-p1", "dec-p2"}


def test_union_still_refuses_a_foreign_relation_from_a_peer(tmp_path):
    """NON-TRIGGER — the enum validator still runs. A peer cannot introduce
    a relation this codebase does not define, reconciliation or not."""
    root = tmp_path / "items"
    state_local = _state(_child(links=[]))
    state_peer = _state(_child(links=[
        _link(decision_id="dec-p1", relation="supersedes", scope="whole"),
        _link(decision_id="dec-p1", scope="whole", written_in="sess-b"),
    ]))

    _sync(state_peer, state_local, root)

    links = _links_of(state_local)
    assert len(links) == 1
    assert links[0]["relation"] == RELATION


def test_union_is_idempotent_and_reports_no_update_on_a_resync(tmp_path):
    """NON-TRIGGER — a second merge of an unchanged inbox changes nothing and
    is reported as zero updates, so a quiet sync stays quiet."""
    root = tmp_path / "items"
    state_local = _state(_child(links=[_link(scope="partial")]))
    state_peer = _state(_child(links=[_link(
        scope="whole", evidence_source="review_confirmed",
        written_in="sess-b", proposed_test="U1_PARTIAL")]))

    assert _sync(state_peer, state_local, root) == 1
    assert _sync(state_peer, state_local, root) == 0
    assert len(_links_of(state_local)) == 1


# --- F-2: _write_decision_link must match on relation too -------------------

def test_write_decision_link_ignores_a_foreign_relation_entry():
    """TRIGGER — matching on decision_id ALONE lets an unrelated link for the
    same parent satisfy the idempotency guard, so the real edge is never
    written while `_archive_cascaded_child` still archives the child: an
    archive with no edge recording why."""
    child = _child(links=[_link(decision_id="dec-p1", relation="supersedes",
                                scope="whole")])

    cascade._write_decision_link(child, {"id": "dec-p1"}, "extractor", "sess-a")

    implements = [l for l in child["decision_links"] if l["relation"] == RELATION]
    assert len(implements) == 1, (
        f"the implements_current_claim edge was never written: "
        f"{child['decision_links']}")
    assert implements[0]["scope"] == "whole"


def test_write_decision_link_still_dedupes_the_same_relation():
    """NON-TRIGGER — the C5-a idempotency guard still fires for a real
    implements_current_claim entry; tightening the match must not turn the
    guard off and start appending duplicates."""
    child = _child(links=[_link(decision_id="dec-p1", scope="whole")])

    cascade._write_decision_link(child, {"id": "dec-p1"}, "extractor", "sess-a")

    assert len(child["decision_links"]) == 1


def test_write_decision_link_still_promotes_a_partial_in_place():
    """NON-TRIGGER — C5-a's promotion still happens in place, and still only
    for a confirmed U1_PARTIAL review."""
    child = _child(links=[_link(decision_id="dec-p1", scope="partial")])

    cascade._write_decision_link(child, {"id": "dec-p1"}, "review_confirmed",
                                 "sess-c", proposed_test="U1_PARTIAL")

    assert len(child["decision_links"]) == 1
    assert child["decision_links"][0]["scope"] == "whole"

    # And a confirm that is NOT U1_PARTIAL leaves the partial alone (N5).
    other = _child(links=[_link(decision_id="dec-p1", scope="partial")])
    cascade._write_decision_link(other, {"id": "dec-p1"}, "review_confirmed",
                                 "sess-c", proposed_test="U1_ID_LINK")
    assert other["decision_links"][0]["scope"] == "partial"


# --- §8.4: the directory-fsync errno guard ----------------------------------

def _fsync_raising_on_dirfd(err):
    """Raise OSError(err) from the directory fsync only, leaving the file
    fsync alone — identifying the target by fstat rather than call order."""
    real_fsync = os.fsync

    def _fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(err, os.strerror(err))
        return real_fsync(fd)

    return _fsync


def test_dirfd_fsync_einval_is_swallowed(tmp_path, monkeypatch):
    """TRIGGER — §8.4 read EINVAL/ENOTSUP/EOPNOTSUPP off `os`, where they do
    not exist, so the tuple resolved to (None, None, None) and EVERY OSError
    re-raised: the exact inverse of the guard's intent. On a filesystem
    without directory-fsync support an ALREADY-SUCCEEDED os.replace was
    reported as a failed merge."""
    path = tmp_path / "example_project.json"
    path.write_text(json.dumps({"project": PROJECT, "decisions": []}))
    monkeypatch.setattr(os, "fsync", _fsync_raising_on_dirfd(errno.EINVAL))

    merger._atomic_write_json(path, {"project": PROJECT, "decisions": ["new"]})

    assert json.loads(path.read_text())["decisions"] == ["new"]


def test_dirfd_fsync_unexpected_errno_still_propagates(tmp_path, monkeypatch):
    """NON-TRIGGER — the guard is narrow. A genuine I/O error is still an
    error; the fix must not become a bare `except OSError: pass`."""
    path = tmp_path / "example_project.json"
    path.write_text(json.dumps({"project": PROJECT, "decisions": []}))
    monkeypatch.setattr(os, "fsync", _fsync_raising_on_dirfd(errno.EIO))

    with pytest.raises(OSError) as excinfo:
        merger._atomic_write_json(path, {"project": PROJECT, "decisions": ["new"]})
    assert excinfo.value.errno == errno.EIO
