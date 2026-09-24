"""The extraction status sidecar must track the ledger, not only the worker.

Before this, only the worker wrote {project}.extraction-status.json.  Sessions
merged by /narrative -- and every project once the worker was uninstalled --
left the sidecar frozen at "failed, never succeeded" while the ledger moved on.
"""

import json

import pytest

import extraction_worker as W
import merger


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "memory-home"
    for d in ("projects", "items", "conversations", "transcripts", "runtime"):
        (root / d).mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("LLM_MEMORY_HOME", str(root))
    return root


def _request(home, project, session_id, name=None):
    queue = W.request_dir(home)
    queue.mkdir(parents=True, exist_ok=True)
    path = queue / f"{name or session_id}.json"
    path.write_text(json.dumps({"project": project, "session_id": session_id,
                                "request_id": f"id-{session_id}"}, indent=2))
    return path


def _state(home, project, sessions=()):
    path = home / "projects" / f"{project}.json"
    path.write_text(json.dumps({
        "project": project, "decisions": [], "goals": [], "suggestions": [],
        "learnings": [], "done": [],
        "sessions": [{"session_id": sid} for sid in sessions],
        "last_rebuilt_at": "2026-09-24T00:00:00Z",
    }))
    return path


def _failed(home, project):
    W.status_path(home, project).write_text(json.dumps({
        "state": "failed", "unprocessed": 0, "stale": 0, "last_success": None,
        "error_summary": "systemd-extraction-worker-failed",
    }))


def test_another_projects_request_does_not_block_idle(home):
    _request(home, "other", "s1")
    assert W.save_status(home, "mine", state="idle")["state"] == "idle"


def test_own_request_blocks_idle(home):
    _request(home, "mine", "s1")
    assert W.save_status(home, "mine", state="idle")["state"] == "waiting"


def test_unattributable_request_still_blocks_every_project(home):
    W.request_dir(home).mkdir(parents=True)
    (W.request_dir(home) / "bad.json").write_text("{not json")
    assert W.save_status(home, "mine", state="idle")["state"] == "waiting"


def test_record_merge_retires_only_that_session_and_clears_failure(home):
    _state(home, "mine", sessions=["s1"])
    _failed(home, "mine")
    merged = _request(home, "mine", "s1")
    other_session = _request(home, "mine", "s2")
    other_project = _request(home, "other", "s1", name="other-s1")

    W.record_merge(home, "mine", "s1")

    assert not merged.exists()
    assert other_session.exists() and other_project.exists()
    status = W.status(home, "mine")
    assert status["state"] == "waiting"  # s2 is still queued
    assert status["last_success"]
    assert status["error_summary"] is None
    assert status["request_ids"] == ["id-s2"]


def test_record_merge_without_a_sidecar_creates_none(home):
    W.record_merge(home, "mine", "s1")
    assert not W.status_path(home, "mine").exists()


def test_merger_refreshes_the_sidecar(home, tmp_path):
    state_path = _state(home, "mine")
    _failed(home, "mine")
    _request(home, "mine", "sess-cli")
    delta = tmp_path / "d.json"
    delta.write_text(json.dumps({
        "session_id": "sess-cli", "started": "2026-06-01T00:00:00Z",
        "ended": "2026-06-01T01:00:00Z", "topic": "t",
        "ledger_delta": {"introduced": {"decisions": [{"text": "a decision"}]}},
    }))

    merger.main([str(state_path), str(delta)])

    status = W.status(home, "mine")
    assert status["state"] == "idle"
    assert status["last_success"]
    assert W.request_files(home, "mine") == []


def test_reconcile_retires_merged_requests_and_backfills_last_success(home):
    _state(home, "mine", sessions=["done"])
    _failed(home, "mine")
    _request(home, "mine", "done")

    W.reconcile(home)

    status = W.status(home, "mine")
    assert status["state"] == "idle"
    assert status["last_success"] == "2026-09-24T00:00:00Z"
    assert W.request_files(home, "mine") == []
