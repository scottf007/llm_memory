from __future__ import annotations

import json

import pytest

import merger
import renderer
import server
from tools import resolve_cascade_reviews
from tools.project_state import LEDGER_KEYS, _atomic_write_json, load_active, load_full, write_full


def _state(project: str = "demo") -> dict:
    state = {
        "project": project,
        "summary": {"idea": "split archive"},
        "sessions": [{"session_id": "sess-1", "status": "active"}],
    }
    state.update({kind: [] for kind in LEDGER_KEYS})
    state["decisions"] = [
        {"id": "dec-active", "text": "keep me", "status": "active"},
        {
            "id": "dec-archived",
            "text": "old archived mechanism",
            "status": "archived",
            "archived_in": "sess-1",
            "archived_reason": "superseded",
        },
    ]
    return state


def test_legacy_single_file_loaders(tmp_path):
    state = _state()
    (tmp_path / "demo.json").write_text(json.dumps(state))
    assert [i["id"] for i in load_active("demo", tmp_path)["decisions"]] == ["dec-active"]
    assert [i["id"] for i in load_full("demo", tmp_path)["decisions"]] == [
        "dec-active", "dec-archived",
    ]
    write_full("demo", state, tmp_path)
    assert [i["id"] for i in json.loads(
        (tmp_path / "demo.json").read_text()
    )["decisions"]] == ["dec-active"]
    assert [i["id"] for i in json.loads(
        (tmp_path / "demo.archived.json").read_text()
    )["decisions"]] == ["dec-archived"]


def test_archived_first_crash_is_lossless_and_self_heals(tmp_path):
    before = _state()
    before["decisions"][1] = {
        "id": "dec-archived", "text": "old active copy", "status": "active"
    }
    (tmp_path / "demo.json").write_text(json.dumps(before))
    after = _state()
    calls = []

    def crash_after_archive(path, value):
        _atomic_write_json(path, value)
        calls.append(path.name)
        if len(calls) == 1:
            raise RuntimeError("simulated crash between split writes")

    with pytest.raises(RuntimeError, match="between split writes"):
        write_full("demo", after, tmp_path, atomic_write=crash_after_archive)
    assert calls == ["demo.archived.json"]
    active_on_disk = json.loads((tmp_path / "demo.json").read_text())
    archived_on_disk = json.loads((tmp_path / "demo.archived.json").read_text())
    physical_ids = {
        item["id"]
        for source in (active_on_disk, archived_on_disk)
        for item in source["decisions"]
    }
    assert physical_ids == {"dec-active", "dec-archived"}
    assert sum(
        item["id"] == "dec-archived"
        for source in (active_on_disk, archived_on_disk)
        for item in source["decisions"]
    ) == 2

    full = load_full("demo", tmp_path)
    moved = next(i for i in full["decisions"] if i["id"] == "dec-archived")
    assert moved["status"] == "archived"
    assert moved["text"] == "old archived mechanism"

    write_full("demo", full, tmp_path)
    active_ids = {
        i["id"] for i in json.loads((tmp_path / "demo.json").read_text())["decisions"]
    }
    archived_ids = {
        i["id"]
        for i in json.loads((tmp_path / "demo.archived.json").read_text())["decisions"]
    }
    assert active_ids == {"dec-active"}
    assert archived_ids == {"dec-archived"}
    assert active_ids.isdisjoint(archived_ids)


def test_project_lookup_reads_archive_sidecar(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    projects.mkdir()
    state = _state()
    write_full("demo", state, projects)
    monkeypatch.setattr(server, "DB_DIR", tmp_path)

    response = server._handle_project_lookup({
        "project": "demo", "query": "archived mechanism", "status": "archived"
    })
    payload = json.loads(response[0].text)

    assert [row["id"] for row in payload["results"]] == ["dec-archived"]


def test_renderer_drill_down_does_not_name_one_split_file():
    state = _state()
    state["done"] = [{"id": "work-old", "status": "archived", "text": "old"}]
    rendered = renderer.render(state)
    assert "project_lookup(project='demo')" in rendered
    assert "demo.json` `done[]`" not in rendered


def test_merger_reads_full_state_and_splits_on_write(tmp_path):
    projects = tmp_path / "projects"
    projects.mkdir()
    write_full("demo", _state(), projects)
    delta = tmp_path / "delta.json"
    delta.write_text(json.dumps({
        "session_id": "sess-2",
        "started": "2026-08-27T00:00:00Z",
        "ended": "2026-08-27T00:01:00Z",
        "ledger_delta": {"introduced": {}},
    }))

    merger.main([str(projects / "demo.json"), str(delta)])

    active = json.loads((projects / "demo.json").read_text())
    archived = json.loads((projects / "demo.archived.json").read_text())
    assert {i["id"] for i in active["decisions"]} == {"dec-active"}
    assert {i["id"] for i in archived["decisions"]} == {"dec-archived"}
    assert {i["id"] for i in load_full("demo", projects)["decisions"]} == {
        "dec-active", "dec-archived"}


def _cascade_state():
    state = _state()
    state["done"] = [{
        "id": "work-child", "status": "active", "text": "child work",
        "rationale": "", "decision_links": [],
    }]
    state["cascade_reviews"] = [{
        "child": "work-child", "candidate_parents": ["dec-archived"],
        "reason_code": "lcs_92", "proposed_test": "U3", "status": "open",
    }]
    return state


def test_default_cascade_resolution_reads_archived_parent(tmp_path, monkeypatch, capsys):
    projects = tmp_path / "projects"
    projects.mkdir()
    write_full("demo", _cascade_state(), projects)
    monkeypatch.setattr(
        resolve_cascade_reviews,
        "local_llm_extractor_call",
        lambda prompt, model=None: {"decision": "confirm", "reason": "same claim"},
    )

    result = resolve_cascade_reviews.main([
        str(projects / "demo.json"), "--dry-run"
    ])

    output = capsys.readouterr().out
    assert result == 0
    assert '"child": "work-child"' in output
    assert '"parent": "dec-archived"' in output


def test_emit_prompts_reads_archived_parent(tmp_path, capsys):
    projects = tmp_path / "projects"
    projects.mkdir()
    write_full("demo", _cascade_state(), projects)

    result = resolve_cascade_reviews.main([
        str(projects / "demo.json"), "--emit-prompts"
    ])

    prompts = json.loads(capsys.readouterr().out)
    assert result == 0
    assert len(prompts) == 1
    assert prompts[0]["parent"] == "dec-archived"
    assert "ARCHIVED DECISION (dec-archived)" in prompts[0]["prompt"]
