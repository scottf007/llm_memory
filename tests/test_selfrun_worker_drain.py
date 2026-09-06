"""Frozen tests — self-running extraction, area 4: the extraction worker's
drain semantics (design doc D1, D3; acceptance #2, #3;
docs/design/self-running-extraction-2026-09-06.md, Appendix).

`extraction_worker.py` (repo root) exposes `enqueue`, `run [--once]`,
`status`, `acknowledge`, `prune`. This file freezes: two simultaneous
triggers for one session produce one merge; two sessions drain in
`unprocessed_sorted` order with the second extractor call seeing the first
session's merged state; a stale session reruns and clears from `stale`;
a mid-run kill leaves the request recoverable and a second `--once`
completes it, with no success recorded before final coverage proves it.

RED on base c0570b2: extraction_worker.py does not exist — every subprocess
invocation below fails with "No such file or directory".
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.fixtures.selfrun import helpers as H


def _enqueue(env, project, session_id, transcript_path, source="manual"):
    return H.run_worker(
        ["enqueue", "--project", project, "--session-id", session_id,
         "--transcript", str(transcript_path), "--source", source],
        env,
    )


def _project_state(memory_home, project):
    return json.loads((memory_home / "projects" / f"{project}.json").read_text())


def test_worker_script_exists_and_reports_status(tmp_path):
    """RED marker: plainest signal extraction_worker.py is missing on base."""
    home, memory_home = H.make_home(tmp_path)
    claude_logs = tmp_path / "claude-calls"
    queue = tmp_path / "queue"
    H.make_response_queue(queue, H.DELTA_SESSION_A)
    env = H.worker_env(home, memory_home, claude_log_dir=claude_logs, response_queue=queue)

    result = H.run_worker(["status", "--project", "selfrunproj"], env)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    status = json.loads(result.stdout)
    assert status["state"] in ("idle", "running", "waiting", "failed")


def test_duplicate_trigger_produces_one_merge_and_one_provenance_record(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    H.write_project_state(memory_home, "selfrunproj")
    transcript = H.write_transcript(memory_home / "transcripts", "selfrun-sess-a")
    claude_logs = tmp_path / "claude-calls"
    queue = tmp_path / "queue"
    H.make_response_queue(queue, H.DELTA_SESSION_A)
    env = H.worker_env(home, memory_home, claude_log_dir=claude_logs, response_queue=queue)

    # Two simultaneous triggers for the SAME session (e.g. SessionEnd fired
    # twice, or SessionEnd + the recovery timer both fired).
    r1 = _enqueue(env, "selfrunproj", "selfrun-sess-a", transcript)
    r2 = _enqueue(env, "selfrunproj", "selfrun-sess-a", transcript)
    assert r1.returncode == 0 and r2.returncode == 0, f"{r1.stderr}\n{r2.stderr}"

    result = H.run_worker(["run", "--once", "--project", "selfrunproj"], env, timeout=30)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    state = _project_state(memory_home, "selfrunproj")
    matches = [s for s in state["sessions"] if s.get("session_id") == "selfrun-sess-a"]
    assert len(matches) == 1, f"expected exactly one merged session record, got {matches}"

    provenance_records = matches[0].get("extraction")
    assert provenance_records is not None, "Appendix requires sessions[i].extraction provenance"
    # A single dict (not a list) also satisfies "one provenance record" —
    # accept either shape but insist there is exactly one attempt recorded.
    if isinstance(provenance_records, list):
        assert len(provenance_records) == 1
    calls = sorted((claude_logs).glob("call-*.argv")) if claude_logs.exists() else []
    assert len(calls) == 1, f"backend must be called exactly once for the duplicate pair, got {len(calls)}"

    requests_left = list((memory_home / "runtime" / "extraction-requests").glob("*.json")) \
        if (memory_home / "runtime" / "extraction-requests").exists() else []
    assert requests_left == [], "both requests for the merged session must be retired"


def test_two_sessions_drain_in_order_and_second_extractor_sees_first_merge(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    H.write_project_state(memory_home, "selfrunproj")
    t_a = H.write_transcript(memory_home / "transcripts", "selfrun-sess-a")
    t_b = H.write_transcript(memory_home / "transcripts", "selfrun-sess-b")
    claude_logs = tmp_path / "claude-calls"
    queue = tmp_path / "queue"
    # response-1 -> session A's delta, response-2 -> session B's delta.
    H.make_response_queue(queue, H.DELTA_SESSION_A, H.DELTA_SESSION_B)
    env = H.worker_env(home, memory_home, claude_log_dir=claude_logs, response_queue=queue)

    # Enqueue B before A: `unprocessed_sorted` (alphabetical by session_id,
    # per the design note) must still process A first.
    assert _enqueue(env, "selfrunproj", "selfrun-sess-b", t_b).returncode == 0
    assert _enqueue(env, "selfrunproj", "selfrun-sess-a", t_a).returncode == 0

    result = H.run_worker(["run", "--once", "--project", "selfrunproj"], env, timeout=30)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    state = _project_state(memory_home, "selfrunproj")
    ids_in_order = [s["session_id"] for s in state["sessions"]
                    if s["session_id"] in ("selfrun-sess-a", "selfrun-sess-b")]
    assert ids_in_order == ["selfrun-sess-a", "selfrun-sess-b"], (
        f"expected A before B (unprocessed_sorted), got {ids_in_order}"
    )

    # The prompt for call 2 (session B) must reflect state AFTER session A's
    # merge — i.e. it must be able to see A's just-introduced marker text.
    # The Appendix does not pin whether the prompt travels via argv or
    # stdin, so check both.
    call2_argv = (claude_logs / "call-2.argv").read_text() if (claude_logs / "call-2.argv").exists() else ""
    call2_stdin = (claude_logs / "call-2.stdin").read_text() if (claude_logs / "call-2.stdin").exists() else ""
    assert "SELFRUN-MARKER-A" in (call2_argv + call2_stdin), (
        "extractor N+1 must see merge N: session B's prompt should include "
        "session A's just-merged active decision text"
    )

    assert list((memory_home / "runtime" / "extraction-requests").glob("*.json")) == []


def test_stale_session_reruns_and_clears_from_stale(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    home, memory_home = H.make_home(tmp_path)
    ended = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
    H.write_project_state(memory_home, "selfrunproj", {
        "project": "selfrunproj",
        "decisions": [], "goals": [], "suggestions": [], "learnings": [], "done": [],
        "sessions": [{"session_id": "selfrun-sess-stale", "started": ended, "ended": ended, "topic": "t"}],
    })
    # Transcript tail is now (48h after "ended") -> _stale_session fires (>24h).
    grown = H.write_transcript(memory_home / "transcripts", "selfrun-sess-stale", n_user_turns=8)

    claude_logs = tmp_path / "claude-calls"
    queue = tmp_path / "queue"
    H.make_response_queue(queue, H.DELTA_SESSION_A)  # rerun's delta content is irrelevant to staleness
    env = H.worker_env(home, memory_home, claude_log_dir=claude_logs, response_queue=queue)

    sys.path.insert(0, str(H.REPO_DIR))
    import server  # existing, unmodified
    monkeypatch.setattr(server, "DB_DIR", memory_home)
    compute_narrative_coverage = server.compute_narrative_coverage
    coverage_before = compute_narrative_coverage("selfrunproj")
    assert any(s["session_id"] == "selfrun-sess-stale" for s in coverage_before["stale"]), (
        "fixture setup bug: session must actually be stale before the worker runs"
    )

    result = H.run_worker(["run", "--once", "--project", "selfrunproj"], env, timeout=30)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    calls = sorted(claude_logs.glob("call-*.argv")) if claude_logs.exists() else []
    assert len(calls) >= 1, "a stale session must trigger a rerun extraction call"

    coverage_after = server.compute_narrative_coverage("selfrunproj")
    assert not any(s["session_id"] == "selfrun-sess-stale" for s in coverage_after["stale"]), (
        "stale session must clear from `stale` after the rerun merges"
    )


def test_kill_mid_run_leaves_request_recoverable_second_once_completes_it(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    H.write_project_state(memory_home, "selfrunproj")
    transcript = H.write_transcript(memory_home / "transcripts", "selfrun-sess-a")
    claude_logs = tmp_path / "claude-calls"
    queue = tmp_path / "queue"
    H.make_response_queue(queue, H.DELTA_SESSION_A)
    env = H.worker_env(home, memory_home, claude_log_dir=claude_logs, response_queue=queue)
    env["FAKE_CLAUDE_SLEEP_ON_CALL"] = "1"
    env["FAKE_CLAUDE_SLEEP_SECONDS"] = "5"

    assert _enqueue(env, "selfrunproj", "selfrun-sess-a", transcript).returncode == 0

    proc = subprocess.Popen(
        [sys.executable, str(H.REPO_DIR / "extraction_worker.py"),
         "run", "--once", "--project", "selfrunproj"],
        env=env,
    )
    # Wait until the backend call has started (mid-run), then kill the
    # worker process hard, simulating a crash/OOM/host-reboot.
    started = claude_logs / "call-1.started"
    for _ in range(100):
        if started.exists():
            break
        time.sleep(0.05)
    assert started.exists(), "backend call never started; cannot test a mid-run kill"
    proc.send_signal(signal.SIGKILL)
    proc.wait(timeout=10)

    # No success may be recorded from a process that never got to write it.
    state = _project_state(memory_home, "selfrunproj")
    assert not any(s.get("session_id") == "selfrun-sess-a" for s in state["sessions"]), (
        "a killed worker must not have recorded a merge"
    )
    requests = list((memory_home / "runtime" / "extraction-requests").glob("*.json"))
    assert len(requests) == 1, "the request must survive a mid-run kill for a later --once to pick up"

    # Reset the fake backend's call counter/sleep for the recovery run.
    env2 = dict(env)
    env2["FAKE_CLAUDE_SLEEP_ON_CALL"] = "0"
    result2 = H.run_worker(["run", "--once", "--project", "selfrunproj"], env2, timeout=30)
    assert result2.returncode == 0, f"stdout:\n{result2.stdout}\nstderr:\n{result2.stderr}"

    state_after = _project_state(memory_home, "selfrunproj")
    assert any(s.get("session_id") == "selfrun-sess-a" for s in state_after["sessions"]), (
        "the recovery --once must complete the survived request"
    )
    assert list((memory_home / "runtime" / "extraction-requests").glob("*.json")) == []
