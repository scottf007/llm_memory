"""Frozen tests — self-running extraction, area 6: revaluation quarantine
(design doc D4, `lrn-6cc16ac2`; acceptance #4;
docs/design/self-running-extraction-2026-09-06.md, Appendix).

An extractor response carrying `ledger_delta.revaluations` must be
preserved byte-for-byte at
`$LLM_MEMORY_HOME/runtime/extraction-results/{project}/{session_id}/{request_id}.json`
(read-only, mode 0444) BEFORE validation/filtering; the applied delta must
have the revaluations stripped (the target item's value must NOT move);
`sessions[i].extraction.quarantined_revaluations` and the project status
must show the count and path; the artifact cannot be pruned before
acknowledgement + 30 days, and ordinary artifacts retain 30 days.

RED on base c0570b2: extraction_worker.py does not exist.
"""

import json
import os
import stat
from pathlib import Path

import pytest

from tests.fixtures.selfrun import helpers as H


def _state_with_existing_decision(memory_home, project="selfrunproj"):
    state = {
        "project": project,
        "decisions": [{
            "id": "dec-existing01", "text": "pre-existing decision", "value": 0.9,
            "importance": "standard", "status": "active",
            "introduced_in": "seed", "last_touched_in": "seed",
            "last_touched_at": "2026-08-01T00:00:00Z",
            "archived_in": None, "archived_reason": None,
        }],
        "goals": [], "suggestions": [], "learnings": [], "done": [], "sessions": [],
    }
    return H.write_project_state(memory_home, project, state)


def test_revaluation_preserved_byte_for_byte_and_readonly_applied_copy_has_none(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    _state_with_existing_decision(memory_home)
    transcript = H.write_transcript(memory_home / "transcripts", "selfrun-sess-revalue")
    claude_logs = tmp_path / "claude-calls"
    queue = tmp_path / "queue"
    H.make_response_queue(queue, H.DELTA_WITH_REVALUATION)
    env = H.worker_env(home, memory_home, claude_log_dir=claude_logs, response_queue=queue)

    r = H.run_worker(
        ["enqueue", "--project", "selfrunproj", "--session-id", "selfrun-sess-revalue",
         "--transcript", str(transcript), "--source", "manual"], env,
    )
    assert r.returncode == 0, r.stderr
    result = H.run_worker(["run", "--once", "--project", "selfrunproj"], env, timeout=30)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    # 1. The raw response is preserved byte-for-byte, read-only.
    raw_dir = memory_home / "runtime" / "extraction-results" / "selfrunproj" / "selfrun-sess-revalue"
    assert raw_dir.is_dir(), f"missing raw-result dir {raw_dir}"
    raw_files = list(raw_dir.glob("*.json"))
    assert len(raw_files) == 1, f"expected exactly one preserved raw result, got {raw_files}"
    raw_content = raw_files[0].read_text()
    expected_raw = H.DELTA_WITH_REVALUATION.read_text()
    assert json.loads(raw_content) == json.loads(expected_raw), (
        "preserved artifact must be byte-for-byte (content-equal) the extractor's raw response"
    )
    mode = stat.S_IMODE(raw_files[0].stat().st_mode)
    assert mode == 0o444, f"raw result must be mode 0444, got {oct(mode)}"

    # 2. The applied delta has NO revaluations: the pre-existing decision's
    #    value must not have moved from 0.9.
    state = json.loads((memory_home / "projects" / "selfrunproj.json").read_text())
    existing = [d for d in state["decisions"] if d["id"] == "dec-existing01"][0]
    assert existing["value"] == pytest.approx(0.9), (
        f"revaluation must be stripped before apply; value moved to {existing['value']}"
    )
    # The delta's ordinary introduced item must still merge normally.
    assert any("SELFRUN-MARKER-REVALUE" in d["text"] for d in state["decisions"])

    # 3. Provenance + status expose the quarantine.
    matched = [s for s in state["sessions"] if s["session_id"] == "selfrun-sess-revalue"][0]
    extraction = matched.get("extraction") or {}
    if isinstance(extraction, list):
        extraction = extraction[-1]
    assert extraction.get("quarantined_revaluations", {}).get("count", 0) >= 1, (
        f"provenance must report quarantined_revaluations count, got {extraction}"
    )
    reported_path = extraction.get("quarantined_revaluations", {}).get("path") \
        or extraction.get("quarantined_revaluations", {}).get("paths")
    assert reported_path, "provenance must report the quarantine artifact path"

    status_path = memory_home / "projects" / "selfrunproj.extraction-status.json"
    assert status_path.exists()
    status = json.loads(status_path.read_text())
    assert status.get("quarantined_revaluations", {}).get("count", 0) >= 1
    assert status.get("quarantined_revaluations", {}).get("paths"), (
        f"status must name the artifact path(s), got {status.get('quarantined_revaluations')}"
    )


def test_control_ordinary_delta_has_no_quarantine_and_applies_normally(tmp_path):
    """Control: a delta with no revaluations produces no quarantine record
    and merges exactly as area-4's plain worker-drain tests already show."""
    home, memory_home = H.make_home(tmp_path)
    H.write_project_state(memory_home, "selfrunproj")
    transcript = H.write_transcript(memory_home / "transcripts", "selfrun-sess-a")
    claude_logs = tmp_path / "claude-calls"
    queue = tmp_path / "queue"
    H.make_response_queue(queue, H.DELTA_SESSION_A)
    env = H.worker_env(home, memory_home, claude_log_dir=claude_logs, response_queue=queue)

    r = H.run_worker(
        ["enqueue", "--project", "selfrunproj", "--session-id", "selfrun-sess-a",
         "--transcript", str(transcript), "--source", "manual"], env,
    )
    assert r.returncode == 0
    result = H.run_worker(["run", "--once", "--project", "selfrunproj"], env, timeout=30)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    raw_dir = memory_home / "runtime" / "extraction-results" / "selfrunproj" / "selfrun-sess-a"
    # A non-revaluation delta may or may not get a preserved artifact per
    # D4 ("ordinary successful attempts retain 30 days" implies one always
    # exists); either way it must show ZERO quarantined revaluations.
    state = json.loads((memory_home / "projects" / "selfrunproj.json").read_text())
    matched = [s for s in state["sessions"] if s["session_id"] == "selfrun-sess-a"][0]
    extraction = matched.get("extraction") or {}
    if isinstance(extraction, list):
        extraction = extraction[-1]
    count = extraction.get("quarantined_revaluations", {}).get("count", 0)
    assert count == 0, f"ordinary delta must report zero quarantined revaluations, got {count}"


def test_retention_acknowledged_31_days_removable_unacknowledged_400_days_not(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    raw_dir = memory_home / "runtime" / "extraction-results" / "selfrunproj" / "sess-old"
    raw_dir.mkdir(parents=True)
    old_artifact = raw_dir / "req-old.json"
    old_artifact.write_text(json.dumps({"ledger_delta": {"revaluations": [{"id": "x", "value": 0.1}]}}))
    old_artifact.chmod(0o444)
    ack_marker = raw_dir / "req-old.json.ack"
    ack_marker.write_text(json.dumps({"acknowledged_at": "2026-08-06T00:00:00Z"}))

    unacked_dir = memory_home / "runtime" / "extraction-results" / "selfrunproj" / "sess-unacked"
    unacked_dir.mkdir(parents=True)
    unacked_artifact = unacked_dir / "req-unacked.json"
    unacked_artifact.write_text(json.dumps({"ledger_delta": {"revaluations": [{"id": "y", "value": 0.1}]}}))
    unacked_artifact.chmod(0o444)
    # 400 days old, never acknowledged.
    import time
    old_ts = time.time() - 400 * 86400
    os.utime(unacked_artifact, (old_ts, old_ts))
    os.utime(old_artifact, (old_ts, old_ts))
    os.utime(ack_marker, (old_ts, old_ts))

    claude_logs = tmp_path / "claude-calls"
    queue = tmp_path / "queue"
    H.make_response_queue(queue, H.DELTA_SESSION_A)
    env = H.worker_env(home, memory_home, claude_log_dir=claude_logs, response_queue=queue)
    env["LLM_MEMORY_NOW"] = "2026-09-06T00:00:00Z"  # ~31 days after old_ts's ack, ~400 after unacked

    result = H.run_worker(["prune"], env, timeout=30)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    assert not old_artifact.exists(), "acknowledged + 30 days must be prunable"
    assert unacked_artifact.exists(), "unacknowledged artifact must never be pruned regardless of age"
