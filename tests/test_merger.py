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
