"""Tests for merger.py re-valuation — the write side of the contested-item
pass. The renderer names items near its budget cut line; the extractor
re-grades them; these entries apply that re-grade back into the ledger.

Plus tests for path resolution: which items tree and which FTS index a merge
writes to is derived from where the state JSON lives, so merging a scratch
copy can never mutate the real ~/.claude/memory tree.
"""

import json
from pathlib import Path

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


def test_session_conversation_path_is_relative_to_relocatable_root():
    state = merger.apply_delta(_state_with(_item()), _delta([]))

    assert state["sessions"][0]["conversation_md"] == "conversations/sess-new.md"


# --- Path resolution -------------------------------------------------------
#
# None of these tests may write under the real ~/.claude/memory. Behavioural
# tests relocate HOME to a tmp dir; assertions about the canonical (real)
# paths only ever call the pure resolver.


def _fake_home(tmp_path, monkeypatch):
    """A relocated HOME with a populated-looking memory tree, so an accidental
    write to the 'real' tree is detectable."""
    home = tmp_path / "home"
    (home / ".claude" / "memory" / "projects").mkdir(parents=True)
    (home / ".claude" / "memory" / "items").mkdir(parents=True)
    (home / ".claude" / "memory" / "memory.db").write_bytes(b"SENTINEL-DB")
    monkeypatch.setenv("HOME", str(home))
    return home


def _write_delta(path, session_id="sess-cli", text="a sandboxed decision"):
    path.write_text(json.dumps({
        "session_id": session_id,
        "started": "2026-06-01T00:00:00Z",
        "ended": "2026-06-01T01:00:00Z",
        "topic": "t",
        "ledger_delta": {"introduced": {"decisions": [{"text": text}]}},
    }))
    return path


def _write_state(path, project="example_project"):
    path.write_text(json.dumps({
        "project": project,
        "decisions": [], "goals": [], "suggestions": [],
        "learnings": [], "done": [], "sessions": [],
    }))
    return path


def test_canonical_state_file_resolves_to_the_real_items_root_and_db():
    """Production invocation must be unchanged: no writes here, just the
    resolved paths."""
    canonical = Path.home() / ".claude" / "memory" / "projects" / "example_project.json"
    items_root, db_path, sandboxed = merger.resolve_paths(canonical)
    assert items_root == Path.home() / ".claude" / "memory" / "items"
    assert db_path == Path.home() / ".claude" / "memory" / "memory.db"
    assert sandboxed is False


def test_state_file_outside_canonical_dir_resolves_to_a_sandbox():
    scratch = Path("/tmp/definitely-not-the-memory-tree/example_project.json")
    items_root, db_path, sandboxed = merger.resolve_paths(scratch)
    assert items_root == scratch.parent / "items"
    assert db_path == scratch.parent / "memory.db"
    assert sandboxed is True
    assert items_root != Path.home() / ".claude" / "memory" / "items"


def test_merging_outside_the_canonical_dir_never_touches_the_real_tree(
        tmp_path, monkeypatch, capsys):
    """The footgun: a scratch copy kept under its real project name used to
    fan out into ~/.claude/memory/items/{project} and rebuild the real index."""
    home = _fake_home(tmp_path, monkeypatch)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    state_path = _write_state(scratch / "example_project.json")
    delta_path = _write_delta(scratch / "d.json")

    merger.main([str(state_path), str(delta_path)])

    real_items = home / ".claude" / "memory" / "items"
    assert list(real_items.rglob("*")) == []
    assert (home / ".claude" / "memory" / "memory.db").read_bytes() == b"SENTINEL-DB"
    # ...and it said so, out loud.
    assert "sandbox mode" in capsys.readouterr().err


def test_merging_outside_the_canonical_dir_writes_items_to_the_sandbox(
        tmp_path, monkeypatch):
    _fake_home(tmp_path, monkeypatch)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    state_path = _write_state(scratch / "example_project.json")
    delta_path = _write_delta(scratch / "d.json")

    merger.main([str(state_path), str(delta_path)])

    sandbox_items = scratch / "items" / "example_project" / "decisions"
    written = list(sandbox_items.glob("*.json"))
    assert len(written) == 1
    payload = json.loads(written[0].read_text())
    assert payload["text"] == "a sandboxed decision"
    assert payload["project"] == "example_project"
    # The sandbox index lives alongside, not in the real tree.
    assert (scratch / "memory.db").exists()


def test_sandbox_inbox_merge_reads_the_sandbox_items_tree(tmp_path, monkeypatch):
    """The read side is redirected too, so a dry run sees only its own items."""
    _fake_home(tmp_path, monkeypatch)
    scratch = tmp_path / "scratch"
    inbox = scratch / "items" / "example_project" / "learnings"
    inbox.mkdir(parents=True)
    (inbox / "lrn-beef0001.json").write_text(json.dumps({
        "id": "lrn-beef0001", "kind": "learnings", "project": "example_project",
        "text": "arrived via the sandbox inbox", "status": "active",
        "last_touched_at": BASE_TS,
    }))
    state_path = _write_state(scratch / "example_project.json")
    delta_path = _write_delta(scratch / "d.json")

    merger.main([str(state_path), str(delta_path)])

    state = json.loads(state_path.read_text())
    assert [l["id"] for l in state["learnings"]] == ["lrn-beef0001"]


def test_canonical_state_file_fans_out_to_the_canonical_items_root(
        tmp_path, monkeypatch, capsys):
    """Same run, but with the state file where the pipeline puts it: items go
    to {HOME}/.claude/memory/items and no sandbox notice is printed."""
    home = _fake_home(tmp_path, monkeypatch)
    projects = home / ".claude" / "memory" / "projects"
    state_path = _write_state(projects / "example_project.json")
    delta_path = _write_delta(tmp_path / "d.json")

    merger.main([str(state_path), str(delta_path)])

    fanned = home / ".claude" / "memory" / "items" / "example_project" / "decisions"
    assert len(list(fanned.glob("*.json"))) == 1
    assert not (projects / "items").exists()
    assert "sandbox mode" not in capsys.readouterr().err


def test_items_root_override_is_honoured(tmp_path, monkeypatch):
    _fake_home(tmp_path, monkeypatch)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    override = tmp_path / "elsewhere" / "items"
    state_path = _write_state(scratch / "example_project.json")
    delta_path = _write_delta(scratch / "d.json")

    merger.main(["--items-root", str(override), str(state_path), str(delta_path)])

    assert len(list((override / "example_project" / "decisions").glob("*.json"))) == 1
    assert not (scratch / "items").exists()
    assert (tmp_path / "elsewhere" / "memory.db").exists()


def test_items_root_override_equal_to_canonical_is_not_sandboxed(tmp_path, monkeypatch):
    home = _fake_home(tmp_path, monkeypatch)
    canonical_items = home / ".claude" / "memory" / "items"
    items_root, db_path, sandboxed = merger.resolve_paths(
        tmp_path / "scratch" / "example_project.json", canonical_items)
    assert items_root == canonical_items
    assert db_path == home / ".claude" / "memory" / "memory.db"
    assert sandboxed is False


def test_project_name_still_comes_from_the_filename_stem(tmp_path, monkeypatch, capsys):
    """Stem wins over state['project'] (unchanged), but the disagreement is
    reported."""
    _fake_home(tmp_path, monkeypatch)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    state_path = _write_state(scratch / "fn_test.json", project="example_project")
    delta_path = _write_delta(scratch / "d.json")

    merger.main([str(state_path), str(delta_path)])

    assert (scratch / "items" / "fn_test").is_dir()
    assert not (scratch / "items" / "example_project").exists()
    assert "state['project']" in capsys.readouterr().err


def test_positional_cli_still_requires_exactly_two_paths(tmp_path):
    with pytest.raises(SystemExit) as exc:
        merger.main([str(tmp_path / "only-one.json")])
    assert exc.value.code == 1


# --- cascade/certification extensions ---------------------------------------
#
# SPEC-rev2-certification-cascade.md §8, §14. Local helpers for building
# cascade-relevant state, distinct from the plain single-decision `_item`/
# `_state_with`/`_delta` helpers above -- these carry `cascade_reviews` and
# the fields lib.cascade/lib.certify need.


def _cascade_state(**kinds):
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


def _cascade_child(iid, **over):
    c = {
        "id": iid, "status": "active", "importance": "standard",
        "text": "a work item with no citation or quoted content at all",
        "rationale": "", "decision_links": [],
    }
    c.update(over)
    return c


def _res_delta(resolutions=None, revaluations=None, top_level_resolutions=None,
               session_id="sess-new"):
    delta = {
        "session_id": session_id,
        "started": "2026-06-01T00:00:00Z",
        "ended": "2026-06-01T01:00:00Z",
        "ledger_delta": {"introduced": {}},
    }
    if resolutions is not None:
        delta["ledger_delta"]["resolutions"] = resolutions
    if revaluations is not None:
        delta["ledger_delta"]["revaluations"] = revaluations
    if top_level_resolutions is not None:
        delta["resolutions"] = top_level_resolutions
    return delta


def test_closed_rejected_path_sets_lifecycle():
    """merger.py:288-299/:308-313 (disposition #3): the closed/rejected
    loops archive goals/suggestions inline, bypassing _archive_item, and
    must explicitly set archive_class == 'lifecycle' -- the one archive
    path in the system _archive_item's own compute-from-reason would never
    reach (closed:/rejected: prefixes carry no cascade/regrade vocabulary
    term). Contrast: a decision archived via the generic 'archived'
    resolution (which does call _archive_item) never gets 'lifecycle'."""
    goal = {
        "id": "goal-lifecycle1", "text": "a goal", "status": "active",
        "importance": "standard", "last_touched_at": BASE_TS, "last_touched_in": "sess-old",
    }
    suggestion = {
        "id": "sug-lifecycle1", "text": "a suggestion", "status": "active",
        "importance": "standard", "last_touched_at": BASE_TS, "last_touched_in": "sess-old",
    }
    decision = _item(id="dec-lifecycle1", text="a decision that gets archived directly")
    state = {
        "project": "testproj",
        "decisions": [decision], "goals": [goal], "suggestions": [suggestion],
        "learnings": [], "done": [], "sessions": [],
    }
    state = merger.apply_delta(state, _res_delta(resolutions={
        "closed": [{"id": "goal-lifecycle1", "evidence": "shipped"}],
        "rejected": [{"id": "sug-lifecycle1", "reason": "won't do"}],
        "archived": [{"id": "dec-lifecycle1", "reason": "superseded by a new approach"}],
    }))

    assert next(g for g in state["goals"] if g["id"] == "goal-lifecycle1")["archive_class"] == "lifecycle"
    assert next(s for s in state["suggestions"] if s["id"] == "sug-lifecycle1")["archive_class"] == "lifecycle"
    assert next(d for d in state["decisions"] if d["id"] == "dec-lifecycle1")["archive_class"] != "lifecycle"


def test_cascade_runs_after_review_resolution_before_revaluations(monkeypatch):
    """Insertion order instrumented directly: both cascade entry points are
    spied so each asserts, at the moment it's called, that no item in state
    has been touched by the revaluation loop yet -- proving revaluations
    never see a not-yet-applied cascade result."""
    from lib import cascade

    order = []
    real_review = cascade.apply_review_resolutions
    real_apply = cascade.apply

    def _untouched(state):
        return not any(
            item.get("revalued_in")
            for kind in merger.LEDGER_KEYS for item in state.get(kind, [])
        )

    def spy_review(state, resolutions, session_id, ts):
        order.append("review_resolution")
        assert _untouched(state)
        return real_review(state, resolutions, session_id, ts)

    def spy_apply(state, session_id, ts):
        order.append("cascade_apply")
        assert _untouched(state)
        return real_apply(state, session_id, ts)

    monkeypatch.setattr(cascade, "apply_review_resolutions", spy_review)
    monkeypatch.setattr(cascade, "apply", spy_apply)

    parent = _cascade_parent("dec-order99")
    child = _cascade_child("work-order99",
                            text="This restates dec-order99 directly and completely.")
    state = _cascade_state(decisions=[parent], done=[child])

    delta = _res_delta(revaluations=[{"id": "work-order99", "value": 0.9}],
                        session_id="sess-order")
    state = merger.apply_delta(state, delta)

    assert order == ["review_resolution", "cascade_apply"]
    item = next(i for i in state["done"] if i["id"] == "work-order99")
    assert item["status"] == "archived"
    assert item["value"] == pytest.approx(0.9)
    assert item["revalued_in"] == "sess-order"


def test_new_delta_missing_decision_link_ref_rejects():
    state = _cascade_state()
    delta = {
        "session_id": "sess-badref",
        "started": BASE_TS, "ended": BASE_TS,
        "ledger_delta": {"introduced": {"done": [{
            "text": "a new done item",
            "decision_links": [{
                "decision_id": "dec-does-not-exist",
                "relation": "implements_current_claim", "scope": "whole",
                "evidence_source": "extractor", "written_in": "sess-badref",
            }],
        }]}},
    }
    with pytest.raises(ValueError):
        merger.apply_delta(state, delta)

    # Non-trigger: a legacy delta with no decision_links field merges normally.
    legacy_delta = {
        "session_id": "sess-legacy",
        "started": BASE_TS, "ended": BASE_TS,
        "ledger_delta": {"introduced": {"done": [{"text": "a plain done item"}]}},
    }
    state2 = merger.apply_delta(_cascade_state(), legacy_delta)
    assert len(state2["done"]) == 1


def test_atomic_fault_before_replace(tmp_path, monkeypatch):
    path = tmp_path / "example_project.json"
    original = json.dumps({"project": "example_project", "decisions": []})
    path.write_text(original)

    def _boom(*a, **k):
        raise OSError("simulated crash before os.replace")
    monkeypatch.setattr("os.replace", _boom)

    with pytest.raises(OSError):
        merger._atomic_write_json(path, {"project": "example_project", "decisions": ["new"]})

    assert path.read_text() == original
    assert list(tmp_path.glob(".tmp-*")) == []


def test_atomic_fault_after_replace_before_fanout(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude" / "memory" / "projects").mkdir(parents=True)
    (home / ".claude" / "memory" / "items").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    state_path = home / ".claude" / "memory" / "projects" / "example_project.json"
    state_path.write_text(json.dumps({
        "project": "example_project",
        "decisions": [], "goals": [], "suggestions": [],
        "learnings": [], "done": [], "sessions": [],
    }))
    delta_path = tmp_path / "d.json"
    delta_path.write_text(json.dumps({
        "session_id": "sess-crash1",
        "started": BASE_TS, "ended": BASE_TS,
        "ledger_delta": {"introduced": {"decisions": [{"text": "a decision"}]}},
    }))

    real_fan_out = merger.fan_out_items

    def _boom(*a, **k):
        raise RuntimeError("simulated crash before fan-out")
    monkeypatch.setattr(merger, "fan_out_items", _boom)

    with pytest.raises(RuntimeError):
        merger.main([str(state_path), str(delta_path)])

    # The state JSON is new (os.replace already completed); items/ is stale.
    written_state = json.loads(state_path.read_text())
    assert len(written_state["decisions"]) == 1
    assert list((home / ".claude" / "memory" / "items").rglob("*")) == []

    # The next merge (self-healing) repairs items/ without re-applying the delta.
    monkeypatch.setattr(merger, "fan_out_items", real_fan_out)
    merger.main([str(state_path), str(delta_path)])

    fanned = home / ".claude" / "memory" / "items" / "example_project" / "decisions"
    assert len(list(fanned.glob("*.json"))) == 1


def test_inbox_merge_imports_decision_links_union(tmp_path):
    items_root = tmp_path / "items"
    kind_dir = items_root / "example_project" / "done"
    kind_dir.mkdir(parents=True)
    local_item = _cascade_child("work-union1", last_touched_at=BASE_TS, decision_links=[])
    state = _cascade_state(done=[local_item])

    (kind_dir / "work-union1.json").write_text(json.dumps({
        "id": "work-union1", "kind": "done", "project": "example_project",
        "status": "active", "text": local_item["text"], "rationale": "",
        "last_touched_at": BASE_TS,
        "decision_links": [{
            "decision_id": "dec-union1", "relation": "implements_current_claim",
            "scope": "partial", "evidence_source": "extractor", "written_in": "sess-remote",
        }],
    }))

    updates = merger.inbox_merge(state, "example_project", items_root)
    assert updates == 1
    assert local_item["decision_links"] == [{
        "decision_id": "dec-union1", "relation": "implements_current_claim",
        "scope": "partial", "evidence_source": "extractor", "written_in": "sess-remote",
    }]

    # Non-trigger: an entry with the same (decision_id, relation, scope)
    # identity as an existing local entry is not duplicated.
    updates2 = merger.inbox_merge(state, "example_project", items_root)
    assert updates2 == 0
    assert len(local_item["decision_links"]) == 1


def test_inbox_merge_preserves_explicit_cascade_class_on_sync(tmp_path):
    items_root = tmp_path / "items"
    kind_dir = items_root / "example_project" / "done"
    kind_dir.mkdir(parents=True)

    local_item = _cascade_child("work-c1a", last_touched_at=BASE_TS)
    local_item.pop("archive_class", None)
    state = _cascade_state(done=[local_item])

    (kind_dir / "work-c1a.json").write_text(json.dumps({
        "id": "work-c1a", "kind": "done", "project": "example_project",
        "status": "archived", "archive_class": "cascade",
        "archived_in": "sess-remote",
        "archived_reason": "cascade from archived decision dec-x via id_link",
        "text": local_item["text"], "rationale": "", "last_touched_at": BASE_TS,
    }))

    merger.inbox_merge(state, "example_project", items_root)
    assert local_item["status"] == "archived"
    assert local_item["archive_class"] == "cascade"

    # Non-trigger: a genuine parser-output label ("regrade") on the incoming
    # side is still safely recomputed locally from the merged
    # archived_reason, never trusted verbatim -- here the incoming label is
    # deliberately wrong ("regrade") against a genuinely cascade-flavored
    # reason, and the correct recomputed value wins.
    other_local = _cascade_child("work-c1b", last_touched_at=BASE_TS)
    other_local.pop("archive_class", None)
    state2 = _cascade_state(done=[other_local])
    (kind_dir / "work-c1b.json").write_text(json.dumps({
        "id": "work-c1b", "kind": "done", "project": "example_project",
        "status": "archived", "archive_class": "regrade",
        "archived_in": "sess-remote",
        "archived_reason": "reversed -- the underlying claim no longer holds",
        "text": other_local["text"], "rationale": "", "last_touched_at": BASE_TS,
    }))
    merger.inbox_merge(state2, "example_project", items_root)
    assert other_local["archive_class"] == "cascade"


def test_inbox_merge_preserves_explicit_lifecycle_class_on_sync(tmp_path):
    items_root = tmp_path / "items"
    kind_dir = items_root / "example_project" / "goals"
    kind_dir.mkdir(parents=True)

    local_goal = {
        "id": "goal-c1life1", "text": "a goal", "status": "active",
        "importance": "standard", "last_touched_at": BASE_TS, "last_touched_in": "sess-old",
    }
    state = _cascade_state(goals=[local_goal])

    (kind_dir / "goal-c1life1.json").write_text(json.dumps({
        "id": "goal-c1life1", "kind": "goals", "project": "example_project",
        "status": "archived", "archive_class": "lifecycle",
        "archived_in": "sess-remote", "archived_reason": "closed: shipped",
        "text": "a goal", "last_touched_at": BASE_TS,
    }))

    merger.inbox_merge(state, "example_project", items_root)
    assert local_goal["status"] == "archived"
    assert local_goal["archive_class"] == "lifecycle"


def test_inbox_merge_legacy_archived_item_no_class_gets_locally_classified(tmp_path):
    items_root = tmp_path / "items"
    kind_dir = items_root / "example_project" / "done"
    kind_dir.mkdir(parents=True)

    local_item = _cascade_child(
        "work-legacy1", status="archived", archived_in="sess-old",
        archived_reason="reversed -- the underlying claim no longer holds",
        last_touched_at=BASE_TS,
    )
    local_item.pop("archive_class", None)
    state = _cascade_state(done=[local_item])

    (kind_dir / "work-legacy1.json").write_text(json.dumps({
        "id": "work-legacy1", "kind": "done", "project": "example_project",
        "status": "archived", "archived_in": "sess-old",
        "archived_reason": "reversed -- the underlying claim no longer holds",
        "text": local_item["text"], "rationale": "", "last_touched_at": BASE_TS,
    }))

    merger.inbox_merge(state, "example_project", items_root)
    assert local_item["archive_class"] == "cascade"


def test_inbox_merge_equal_timestamp_unions_evidence_without_erasing(tmp_path):
    items_root = tmp_path / "items"
    kind_dir = items_root / "example_project" / "done"
    kind_dir.mkdir(parents=True)

    local_item = _cascade_child("work-equalts1", last_touched_at=BASE_TS, decision_links=[])
    state = _cascade_state(done=[local_item])

    (kind_dir / "work-equalts1.json").write_text(json.dumps({
        "id": "work-equalts1", "kind": "done", "project": "example_project",
        "status": "active", "text": local_item["text"], "rationale": "",
        "last_touched_at": BASE_TS,  # equal, not newer
        "decision_links": [{
            "decision_id": "dec-equalts1", "relation": "implements_current_claim",
            "scope": "partial", "evidence_source": "extractor", "written_in": "sess-remote",
        }],
    }))

    updates = merger.inbox_merge(state, "example_project", items_root)
    assert updates == 1
    assert local_item["decision_links"] == [{
        "decision_id": "dec-equalts1", "relation": "implements_current_claim",
        "scope": "partial", "evidence_source": "extractor", "written_in": "sess-remote",
    }]


def test_inbox_stale_active_file_cannot_erase_decision_links(tmp_path):
    items_root = tmp_path / "items"
    kind_dir = items_root / "example_project" / "done"
    kind_dir.mkdir(parents=True)

    local_item = _cascade_child(
        "work-stale1", last_touched_at="2026-06-01T00:00:00Z",
        decision_links=[{
            "decision_id": "dec-stale1", "relation": "implements_current_claim",
            "scope": "whole", "evidence_source": "review_confirmed", "written_in": "sess-confirm",
        }],
    )
    state = _cascade_state(done=[local_item])

    # A stale, pre-confirmation per-item file synced after the real
    # confirmation -- older timestamp, carries no decision_links at all.
    (kind_dir / "work-stale1.json").write_text(json.dumps({
        "id": "work-stale1", "kind": "done", "project": "example_project",
        "status": "active", "text": local_item["text"], "rationale": "",
        "last_touched_at": BASE_TS,
    }))

    merger.inbox_merge(state, "example_project", items_root)
    assert local_item["decision_links"] == [{
        "decision_id": "dec-stale1", "relation": "implements_current_claim",
        "scope": "whole", "evidence_source": "review_confirmed", "written_in": "sess-confirm",
    }]


def test_apply_delta_no_item_file_writes(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude" / "memory" / "projects").mkdir(parents=True)
    (home / ".claude" / "memory" / "items").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    state = _cascade_state()
    delta = {
        "session_id": "sess-noio",
        "started": BASE_TS, "ended": BASE_TS,
        "ledger_delta": {"introduced": {"decisions": [{"text": "a decision"}]}},
    }
    merger.apply_delta(state, delta)

    assert list((home / ".claude" / "memory" / "items").rglob("*")) == []
    assert not (home / ".claude" / "memory" / "memory.db").exists()
