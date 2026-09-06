"""Frozen tests — self-running extraction, amendment 1 to the round-1 suite
(opus judge verdict 01788665750875978275-selfrun-judge-opus-9927e3b6 on
llm-memory-pipeline; docs/design/self-running-extraction-2026-09-06.md).

The round-1 candidate (ed73969b531ca124827c8ebf7455305f4c559120) computes the
worker's final-coverage success proof and then discards it
(extraction_worker.py:247-248, `remaining` computed once, never branched on),
never re-checks requests/coverage before releasing its project lock, silently
writes `idle` while a request is still pending, drops SessionStart's entire
narrative injection whenever a project is degraded, resolves SessionEnd's
project by an import that always fails over to a diverging basename rule, and
lets contended-lock and delta-session-id-mismatch runs through untested.

RED on ed73969: test_final_coverage_gates_retirement_..., test_request_
enqueued_mid_drain_..., test_run_once_yields_to_a_held_project_lock_...,
test_session_start_degraded_status_still_injects_..., test_session_end_
project_resolution_parity_subdirectory_cwd, test_success_status_written_
exactly_once_... . Everything else here is a control or a proof-of-mechanism
that is already GREEN on ed73969 today.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta

from tests.fixtures.selfrun import helpers as H


def _project_state(memory_home, project):
    return json.loads((memory_home / "projects" / f"{project}.json").read_text())


def _status(memory_home, project):
    path = memory_home / "projects" / f"{project}.extraction-status.json"
    return json.loads(path.read_text()) if path.exists() else None


def _requests(memory_home):
    d = memory_home / "runtime" / "extraction-requests"
    return sorted(d.glob("*.json")) if d.is_dir() else []


def _enqueue(env, project, session_id, transcript, source="manual"):
    return H.run_worker(
        ["enqueue", "--project", project, "--session-id", session_id,
         "--transcript", str(transcript), "--source", source], env,
    )


def _write_transcript_ending_at(transcripts_dir, session_id, end_iso, n_turns=5):
    """A transcript whose last record's timestamp is exactly `end_iso`, for
    tests that need exact control over the staleness window
    (server.STALE_TAIL_HOURS = 24h)."""
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    end = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
    records = []
    for i in range(n_turns):
        ts = (end - timedelta(hours=(n_turns - i))).strftime("%Y-%m-%dT%H:%M:%SZ")
        records.append({"type": "user", "timestamp": ts,
                         "message": {"role": "user", "content": f"selfrun fixture turn {i}"}})
        records.append({"type": "assistant", "timestamp": ts, "message": {
            "role": "assistant",
            "content": ("Here is a substantive, multi-sentence assistant reply about "
                        "the selfrun fixture project so the content gate is cleared. "
                        "It discusses a concrete decision and a concrete next step."),
        }})
    path = transcripts_dir / f"{session_id}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return path


# ======================================================================
# B1 — final-coverage success proof (design l.94; acceptance #3)
# ======================================================================

def test_final_coverage_gates_retirement_request_stays_status_waiting_last_success_not_advanced(tmp_path):
    # The worker stamps the applied delta's started/ended from its OWN
    # observation of the transcript, taken before the backend call — a
    # model-echoed timestamp is provenance only (design l.94 ruling). So the
    # real reason a merged session can still be stale is that the transcript
    # kept growing during the backend call, past what the worker observed.
    # DELTA_SESSION_A's own "ended" (2026-09-01) is irrelevant here and is
    # expected to be overwritten.
    home, memory_home = H.make_home(tmp_path)
    H.write_project_state(memory_home, "selfrunproj")
    transcript = H.write_transcript(memory_home / "transcripts", "selfrun-sess-a")
    claude_logs = tmp_path / "claude-calls"
    queue = tmp_path / "queue"
    H.make_response_queue(queue, H.DELTA_SESSION_A)
    env = H.worker_env(home, memory_home, claude_log_dir=claude_logs, response_queue=queue)
    env["FAKE_CLAUDE_SLEEP_ON_CALL"] = "1"
    env["FAKE_CLAUDE_SLEEP_SECONDS"] = "4"

    assert _enqueue(env, "selfrunproj", "selfrun-sess-a", transcript).returncode == 0

    proc = subprocess.Popen(
        [sys.executable, str(H.REPO_DIR / "extraction_worker.py"),
         "run", "--once", "--project", "selfrunproj"],
        env=env,
    )
    try:
        started = claude_logs / "call-1.started"
        for _ in range(200):
            if started.exists():
                break
            time.sleep(0.05)
        assert started.exists(), "backend call never started; cannot open the observation window"

        # The worker already read (observed the bounds of) this transcript
        # before the backend call above started. Append one more substantive
        # record now, while the call is still sleeping, dated well past
        # server.STALE_TAIL_HOURS (24h) past what was just observed, so the
        # session is unambiguously stale regardless of how fast this test runs.
        growth_ts = (datetime.utcnow() + timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with (memory_home / "transcripts" / "selfrun-sess-a.jsonl").open("a") as f:
            f.write(json.dumps({
                "type": "user", "timestamp": growth_ts,
                "message": {"role": "user",
                            "content": "selfrun fixture: one more turn after the worker's read"},
            }) + "\n")

        proc.wait(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
    assert proc.returncode == 0, f"worker did not exit cleanly (returncode={proc.returncode})"

    # Fixture sanity: the merge really happened, and the session really is
    # still stale by the worker's own coverage function immediately after —
    # because the transcript grew past the worker's own observed bounds, not
    # because of any timestamp the backend echoed.
    state = _project_state(memory_home, "selfrunproj")
    assert any(s.get("session_id") == "selfrun-sess-a" for s in state["sessions"]), (
        "fixture setup bug: session must actually merge"
    )
    sys.path.insert(0, str(H.REPO_DIR))
    import server
    server.DB_DIR = memory_home
    coverage = server.compute_narrative_coverage("selfrunproj")
    assert any(s["session_id"] == "selfrun-sess-a" for s in coverage["stale"]), (
        "fixture setup bug: appending past the worker's observed bounds must "
        "still show stale immediately after merge"
    )

    status = _status(memory_home, "selfrunproj")
    assert status is not None
    assert status["state"] == "waiting", (
        f"design l.94: the request must not retire while the session remains in "
        f"unprocessed/stale; got state={status['state']!r} (full status: {status})"
    )
    assert status["last_success"] is None, (
        f"last_success must not advance when the session did not actually clear; "
        f"got {status['last_success']!r}"
    )
    assert len(_requests(memory_home)) == 1, (
        f"the request must stay on disk until coverage proves the session gone, "
        f"found {[str(p) for p in _requests(memory_home)]}"
    )


def test_control_normal_drain_retires_request_and_writes_idle(tmp_path):
    """Control: a session whose transcript does not keep growing past its
    merge point clears normally. DELTA_SESSION_A's own "ended" echo is
    2026-09-01T01:00:00Z, deliberately not matched to anything here — the
    worker overwrites it with its own observed bound, which (with no growth
    at all) sits at the transcript's real tail, `end_iso` below. That the run
    still comes back idle proves the observed timestamp is what governs
    retirement, not the model's echo."""
    home, memory_home = H.make_home(tmp_path)
    H.write_project_state(memory_home, "selfrunproj")
    end_iso = "2026-09-01T01:00:00Z"  # matches DELTA_SESSION_A's "ended"
    transcript = _write_transcript_ending_at(memory_home / "transcripts", "selfrun-sess-a", end_iso)
    claude_logs = tmp_path / "claude-calls"
    queue = tmp_path / "queue"
    H.make_response_queue(queue, H.DELTA_SESSION_A)
    env = H.worker_env(home, memory_home, claude_log_dir=claude_logs, response_queue=queue)

    assert _enqueue(env, "selfrunproj", "selfrun-sess-a", transcript).returncode == 0
    result = H.run_worker(["run", "--once", "--project", "selfrunproj"], env, timeout=30)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    status = _status(memory_home, "selfrunproj")
    assert status is not None and status["state"] == "idle"
    assert status["last_success"] is not None
    assert _requests(memory_home) == []


# ======================================================================
# B2 — re-check requests + coverage before releasing the lock
# (design l.72; acceptance #2)
# ======================================================================

def test_request_enqueued_mid_drain_is_retired_in_the_same_run_not_left_pending(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    H.write_project_state(memory_home, "selfrunproj")
    t_a = H.write_transcript(memory_home / "transcripts", "selfrun-sess-a")
    t_b = H.write_transcript(memory_home / "transcripts", "selfrun-sess-b")
    claude_logs = tmp_path / "claude-calls"
    queue = tmp_path / "queue"
    H.make_response_queue(queue, H.DELTA_SESSION_A, H.DELTA_SESSION_B)
    env = H.worker_env(home, memory_home, claude_log_dir=claude_logs, response_queue=queue)
    env["FAKE_CLAUDE_SLEEP_ON_CALL"] = "1"
    env["FAKE_CLAUDE_SLEEP_SECONDS"] = "4"

    assert _enqueue(env, "selfrunproj", "selfrun-sess-a", t_a).returncode == 0

    proc = subprocess.Popen(
        [sys.executable, str(H.REPO_DIR / "extraction_worker.py"),
         "run", "--once", "--project", "selfrunproj"],
        env=env,
    )
    try:
        started = claude_logs / "call-1.started"
        for _ in range(200):
            if started.exists():
                break
            time.sleep(0.05)
        assert started.exists(), "backend call never started; cannot open the mid-drain window"

        # Enqueue B while A's backend call is still sleeping, i.e. while the
        # worker still holds the project lock for this run.
        assert _enqueue(env, "selfrunproj", "selfrun-sess-b", t_b).returncode == 0

        proc.wait(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
    assert proc.returncode == 0

    state = _project_state(memory_home, "selfrunproj")
    merged = {s["session_id"] for s in state["sessions"]}
    assert {"selfrun-sess-a", "selfrun-sess-b"} <= merged, (
        f"design l.72: a request that arrives mid-drain must be retired in the SAME "
        f"winning run, not left for a later invocation; merged={merged}"
    )
    assert _requests(memory_home) == [], (
        f"expected both sessions drained and no request left, found "
        f"{[str(p) for p in _requests(memory_home)]}"
    )


def test_control_single_request_no_concurrent_arrival_drains_normally(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    H.write_project_state(memory_home, "selfrunproj")
    transcript = H.write_transcript(memory_home / "transcripts", "selfrun-sess-a")
    claude_logs = tmp_path / "claude-calls"
    queue = tmp_path / "queue"
    H.make_response_queue(queue, H.DELTA_SESSION_A)
    env = H.worker_env(home, memory_home, claude_log_dir=claude_logs, response_queue=queue)
    assert _enqueue(env, "selfrunproj", "selfrun-sess-a", transcript).returncode == 0
    result = H.run_worker(["run", "--once", "--project", "selfrunproj"], env, timeout=30)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    state = _project_state(memory_home, "selfrunproj")
    assert any(s["session_id"] == "selfrun-sess-a" for s in state["sessions"])
    assert _requests(memory_home) == []


# ======================================================================
# B3 — idle-with-pending must be impossible (D3 "never silent"; acceptance #5)
# ======================================================================

def test_save_status_idle_refused_while_any_request_file_exists_even_an_unparseable_one(tmp_path):
    sys.path.insert(0, str(H.REPO_DIR))
    import extraction_worker as W
    home = tmp_path / "memory-home"
    (home / "runtime" / "extraction-requests").mkdir(parents=True)
    # A request the worker cannot attribute to any project: not valid JSON, so
    # request_files(home, project) silently excludes it (the (OSError,
    # JSONDecodeError) catch there) while the file still sits on disk -- the
    # honest "worker cannot see it" case: if the safety net only looked at
    # what it could parse, it would be useless for exactly the request that
    # made the worker choke.
    (home / "runtime" / "extraction-requests" / "unreadable-request.json").write_text("{not json")

    W.save_status(home, "selfrunproj", state="idle", unprocessed=0, stale=0,
                  last_success="2026-09-06T00:00:00Z")

    written = json.loads((home / "projects" / "selfrunproj.extraction-status.json").read_text())
    assert written["state"] != "idle", (
        f"an on-disk request file the worker could not parse must still block an idle "
        f"write; got state={written['state']!r}"
    )


def test_control_save_status_idle_allowed_with_no_request_files_at_all(tmp_path):
    sys.path.insert(0, str(H.REPO_DIR))
    import extraction_worker as W
    home = tmp_path / "memory-home"
    home.mkdir()
    W.save_status(home, "selfrunproj", state="idle", unprocessed=0, stale=0,
                  last_success="2026-09-06T00:00:00Z")
    written = json.loads((home / "projects" / "selfrunproj.extraction-status.json").read_text())
    assert written["state"] == "idle"


# ======================================================================
# B6(a) — the worker's project lock must actually gate `run --once`
# ======================================================================

def test_run_once_yields_to_a_held_project_lock_nonzero_exit_exact_warn_touches_nothing(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    H.write_project_state(memory_home, "selfrunproj")
    state_path = memory_home / "projects" / "selfrunproj.json"
    sha_before = H.sha256_file(state_path)
    mtime_before = state_path.stat().st_mtime_ns
    status_path = memory_home / "projects" / "selfrunproj.extraction-status.json"

    transcript = H.write_transcript(memory_home / "transcripts", "selfrun-sess-a")
    claude_logs = tmp_path / "claude-calls"
    queue = tmp_path / "queue"
    H.make_response_queue(queue, H.DELTA_SESSION_A)
    env = H.worker_env(home, memory_home, claude_log_dir=claude_logs, response_queue=queue)
    assert _enqueue(env, "selfrunproj", "selfrun-sess-a", transcript).returncode == 0

    sys.path.insert(0, str(H.REPO_DIR))
    marker = tmp_path / "holder-has-lock"
    holder_src = f"""
import sys, time
sys.path.insert(0, {str(H.REPO_DIR)!r})
import narrative_lock
from pathlib import Path
with narrative_lock.project_lock(Path({str(memory_home)!r}), "selfrunproj"):
    Path({str(marker)!r}).touch()
    time.sleep(3)
"""
    holder = subprocess.Popen([sys.executable, "-c", holder_src])
    try:
        for _ in range(100):
            if marker.exists():
                break
            time.sleep(0.05)
        assert marker.exists(), "holder subprocess never signaled it took the lock"

        result = H.run_worker(["run", "--once", "--project", "selfrunproj"], env, timeout=10)
    finally:
        holder.wait(timeout=10)

    assert result.returncode != 0, (
        f"a lock-contended run --once must exit non-zero, got 0. "
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "LLM_MEMORY_WARN: narrative update already running for selfrunproj" in (
        result.stdout + result.stderr
    ), f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    assert H.sha256_file(state_path) == sha_before, "a losing run must not mutate project state"
    assert state_path.stat().st_mtime_ns == mtime_before
    assert not status_path.exists(), "a losing run must write no status sidecar either"
    assert len(_requests(memory_home)) == 1, "the request must survive for the next run"


def test_control_run_once_uncontended_merges_normally(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    H.write_project_state(memory_home, "selfrunproj")
    transcript = H.write_transcript(memory_home / "transcripts", "selfrun-sess-a")
    claude_logs = tmp_path / "claude-calls"
    queue = tmp_path / "queue"
    H.make_response_queue(queue, H.DELTA_SESSION_A)
    env = H.worker_env(home, memory_home, claude_log_dir=claude_logs, response_queue=queue)
    assert _enqueue(env, "selfrunproj", "selfrun-sess-a", transcript).returncode == 0
    result = H.run_worker(["run", "--once", "--project", "selfrunproj"], env, timeout=30)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    state = _project_state(memory_home, "selfrunproj")
    assert any(s["session_id"] == "selfrun-sess-a" for s in state["sessions"])


# ======================================================================
# B6(c) — no fabricated all-clear: the coverage the worker acts on must be
# the real server.compute_narrative_coverage on the real store (design l.94;
# the branch this proves is pinned RED above in B1 -- this proves the
# mechanism itself is genuine, not stubbed, for the clean case).
# ======================================================================

def test_worker_status_counts_match_an_independently_computed_real_coverage_call(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    H.write_project_state(memory_home, "selfrunproj")
    end_iso = "2026-09-06T00:00:00Z"
    transcript = _write_transcript_ending_at(memory_home / "transcripts", "selfrun-sess-a", end_iso)
    claude_logs = tmp_path / "claude-calls"
    queue = tmp_path / "queue"
    delta = json.loads(H.DELTA_SESSION_A.read_text())
    delta["ended"] = end_iso  # must match the transcript's own anchor: genuinely clean
    custom_delta = tmp_path / "delta-a-clean.json"
    custom_delta.write_text(json.dumps(delta))
    H.make_response_queue(queue, custom_delta)
    env = H.worker_env(home, memory_home, claude_log_dir=claude_logs, response_queue=queue)
    assert _enqueue(env, "selfrunproj", "selfrun-sess-a", transcript).returncode == 0
    result = H.run_worker(["run", "--once", "--project", "selfrunproj"], env, timeout=30)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    sys.path.insert(0, str(H.REPO_DIR))
    import server
    server.DB_DIR = memory_home
    independent = server.compute_narrative_coverage("selfrunproj")
    assert independent["unprocessed_count"] == 0
    assert independent["stale"] == []

    status = _status(memory_home, "selfrunproj")
    assert status is not None
    assert status["unprocessed"] == independent["unprocessed_count"] == 0
    assert status["stale"] == len(independent["stale"]) == 0
    assert status["state"] == "idle"


# ======================================================================
# B4 — SessionStart must never drop its normal injection when degraded
# ======================================================================

def test_session_start_degraded_status_still_injects_narrative_and_normal_body(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    H.write_project_state(memory_home, "selfrunproj")
    (memory_home / "projects" / "selfrunproj.narrative.md").write_text(
        "SELFRUN-NARRATIVE-MARKER: the living story of selfrunproj.\n"
    )
    (memory_home / "projects" / "selfrunproj.extraction-status.json").write_text(json.dumps({
        "state": "failed", "unprocessed": 2, "stale": 0,
        "last_attempt": "2026-09-06T00:00:00Z", "oldest_waiting": None,
    }))
    input_json = json.dumps({
        "source": "startup", "trigger": "startup",
        "cwd": "/home/user/projects/selfrunproj",
        "session_id": "selfrun-degraded-sess",
    })
    stdout, stderr, rc, wall = H.run_hook("session_start.sh", home, memory_home, input_json)
    assert rc == 0, f"stderr:\n{stderr}"
    assert "LLM_MEMORY_WARN: extraction" in stdout, (
        "the degraded warning itself must still be present, unchanged from today"
    )
    assert "=== LOADED MEMORIES" in stdout, (
        f"B4: a degraded status must not drop the rest of SessionStart's normal "
        f"injection (CLAUDE.md sync, narrative, AUTOMATIC TASK); got:\n{stdout}"
    )
    assert "SELFRUN-NARRATIVE-MARKER" in stdout, (
        "the project narrative itself must still be injected when degraded"
    )


def test_control_session_start_healthy_no_status_file_unchanged_from_today(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    H.write_project_state(memory_home, "selfrunproj")
    (memory_home / "projects" / "selfrunproj.narrative.md").write_text(
        "SELFRUN-NARRATIVE-MARKER: the living story of selfrunproj.\n"
    )
    input_json = json.dumps({
        "source": "startup", "trigger": "startup",
        "cwd": "/home/user/projects/selfrunproj",
        "session_id": "selfrun-healthy-sess",
    })
    stdout, stderr, rc, wall = H.run_hook("session_start.sh", home, memory_home, input_json)
    assert rc == 0, f"stderr:\n{stderr}"
    assert "LLM_MEMORY_WARN: extraction" not in stdout
    assert "=== LOADED MEMORIES" in stdout
    assert "SELFRUN-NARRATIVE-MARKER" in stdout


# ======================================================================
# B5 — SessionEnd's project resolution must match SessionStart's
# ======================================================================

def test_session_end_project_resolution_parity_subdirectory_cwd(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    raw_dir = tmp_path / "raw-transcripts"
    raw_dir.mkdir()
    transcript = H.write_transcript(raw_dir, "selfrun-subdir-sess")
    systemctl_log = tmp_path / "systemctl.log"

    input_json = json.dumps({
        "session_id": "selfrun-subdir-sess",
        "transcript_path": str(transcript),
        "cwd": "/home/user/projects/selfrunproj/tests",
    })
    stdout, stderr, rc, wall = H.run_hook(
        "session_end.sh", home, memory_home, input_json,
        extra_env={"FAKE_SYSTEMCTL_LOG": str(systemctl_log)},
    )
    assert rc == 0, f"stderr:\n{stderr}"
    requests = _requests(memory_home)
    assert len(requests) == 1, (
        f"expected exactly one request, found {len(requests)}. "
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    req = json.loads(requests[0].read_text())
    assert req["project"] == "selfrunproj", (
        f"session_end.sh must resolve the project the same way session_start.sh "
        f"does (hooks/lib_session_common.sh resolve_project_from_cwd); "
        f"got {req['project']!r} for cwd=.../selfrunproj/tests"
    )


def test_control_session_end_project_root_cwd_resolves_correctly(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    raw_dir = tmp_path / "raw-transcripts"
    raw_dir.mkdir()
    transcript = H.write_transcript(raw_dir, "selfrun-root-sess")
    systemctl_log = tmp_path / "systemctl.log"

    input_json = json.dumps({
        "session_id": "selfrun-root-sess",
        "transcript_path": str(transcript),
        "cwd": "/home/user/projects/selfrunproj",
    })
    stdout, stderr, rc, wall = H.run_hook(
        "session_end.sh", home, memory_home, input_json,
        extra_env={"FAKE_SYSTEMCTL_LOG": str(systemctl_log)},
    )
    assert rc == 0, f"stderr:\n{stderr}"
    requests = _requests(memory_home)
    assert len(requests) == 1
    req = json.loads(requests[0].read_text())
    assert req["project"] == "selfrunproj"


# ======================================================================
# N6 — delta session_id validation (judge: "replacing raise ValueError(...)
# with pass leaves 37/37 green"; already correct in ed73969, just unpinned)
# ======================================================================

def test_delta_session_id_mismatch_rejected_never_merges_status_failed_after_two_attempts(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    H.write_project_state(memory_home, "selfrunproj")
    transcript = H.write_transcript(memory_home / "transcripts", "selfrun-sess-a")
    claude_logs = tmp_path / "claude-calls"
    queue = tmp_path / "queue"
    # No session_id field at all: delta.get("session_id") is None, which must
    # fail the `!= req["session_id"]` check exactly like any other mismatch.
    H.make_response_queue(queue, H.DELTA_MISSING_SESSION_ID, H.DELTA_MISSING_SESSION_ID)
    env = H.worker_env(home, memory_home, claude_log_dir=claude_logs, response_queue=queue)
    assert _enqueue(env, "selfrunproj", "selfrun-sess-a", transcript).returncode == 0
    H.run_worker(["run", "--once", "--project", "selfrunproj"], env, timeout=30)
    H.run_worker(["run", "--once", "--project", "selfrunproj"], env, timeout=30)

    state = _project_state(memory_home, "selfrunproj")
    assert not any(s["session_id"] == "selfrun-sess-a" for s in state["sessions"]), (
        "a delta whose session_id does not match the request must never merge"
    )
    status = _status(memory_home, "selfrunproj")
    assert status is not None and status["state"] == "failed", (
        f"expected failed after two attempts, got: {status}"
    )


def test_control_delta_session_id_matches_merges_normally(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    H.write_project_state(memory_home, "selfrunproj")
    transcript = H.write_transcript(memory_home / "transcripts", "selfrun-sess-a")
    claude_logs = tmp_path / "claude-calls"
    queue = tmp_path / "queue"
    H.make_response_queue(queue, H.DELTA_SESSION_A)
    env = H.worker_env(home, memory_home, claude_log_dir=claude_logs, response_queue=queue)
    assert _enqueue(env, "selfrunproj", "selfrun-sess-a", transcript).returncode == 0
    result = H.run_worker(["run", "--once", "--project", "selfrunproj"], env, timeout=30)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    state = _project_state(memory_home, "selfrunproj")
    assert any(s["session_id"] == "selfrun-sess-a" for s in state["sessions"])


# ======================================================================
# N5 — the success status must be written exactly once per drain, not once
# per duplicate request file naming the same already-merged session
# ======================================================================

def _poll_idle_inodes(status_path, stop_event, seen):
    """Identify each distinct atomic write to `status_path` whose content has
    state=="idle". Inode and content MUST come from one open file descriptor:
    stat()-then-reopen (or stat()-then-read_text()) races against
    atomic_json's os.replace() and can pair an old inode with new content (or
    vice versa), fabricating a write that never happened."""
    while not stop_event.is_set():
        try:
            fd = os.open(status_path, os.O_RDONLY)
        except FileNotFoundError:
            continue
        try:
            st = os.fstat(fd)
            content = os.read(fd, 1_000_000)
        except OSError:
            os.close(fd)
            continue
        os.close(fd)
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            continue
        if data.get("state") == "idle":
            seen.add((st.st_dev, st.st_ino))


def test_success_status_written_exactly_once_per_drain_not_once_per_duplicate_request(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    H.write_project_state(memory_home, "selfrunproj")
    transcript = H.write_transcript(memory_home / "transcripts", "selfrun-sess-a")
    claude_logs = tmp_path / "claude-calls"
    queue = tmp_path / "queue"
    H.make_response_queue(queue, H.DELTA_SESSION_A)
    env = H.worker_env(home, memory_home, claude_log_dir=claude_logs, response_queue=queue)

    # Three request files naming the SAME session. `enqueue()` itself already
    # dedupes by session_id (it returns early when one exists), so calling it
    # repeatedly cannot produce this -- the only honest way duplicates land
    # on disk is a genuine race between two concurrent enqueuers (SessionEnd
    # firing twice, or SessionEnd racing the recovery timer) both reading the
    # empty request dir before either writes. `_process_project`'s own
    # duplicate-retirement loop explicitly anticipates "coalesced copies for
    # that now-merged session", so constructing the race's aftermath directly
    # is testing a real, reachable state, not an impossible one.
    import secrets
    for i in range(3):
        request_id = secrets.token_hex(8)
        record = {
            "schema": 1, "request_id": request_id, "project": "selfrunproj",
            "session_id": "selfrun-sess-a", "transcript_path": str(transcript),
            "enqueued_at": f"2026-09-06T00:00:0{i}Z", "source": "manual", "attempts": 0,
        }
        (memory_home / "runtime" / "extraction-requests").mkdir(parents=True, exist_ok=True)
        (memory_home / "runtime" / "extraction-requests" / f"race-{i}-{request_id}.json").write_text(
            json.dumps(record)
        )
    assert len(_requests(memory_home)) == 3, "fixture setup bug: need three request files"

    status_path = memory_home / "projects" / "selfrunproj.extraction-status.json"
    seen_idle_inodes: set[tuple[int, int]] = set()
    stop = threading.Event()
    poller = threading.Thread(target=_poll_idle_inodes, args=(status_path, stop, seen_idle_inodes), daemon=True)
    poller.start()
    result = H.run_worker(["run", "--once", "--project", "selfrunproj"], env, timeout=30)
    stop.set()
    poller.join(timeout=5)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    assert len(seen_idle_inodes) <= 1, (
        f"N5: the success status must be written exactly once per drain, not once per "
        f"duplicate request file that happened to name the same session; observed "
        f"{len(seen_idle_inodes)} distinct sidecar inodes with state=idle during the run"
    )


def test_control_single_request_success_status_observed_written(tmp_path):
    """Control for the poller mechanism itself: with exactly one request, the
    watcher must observe exactly one idle-state write -- proving the poller
    is not simply missing every write (which would make the trigger above
    pass for the wrong reason: 0 observed, not <= 1 meaningfully)."""
    home, memory_home = H.make_home(tmp_path)
    H.write_project_state(memory_home, "selfrunproj")
    transcript = H.write_transcript(memory_home / "transcripts", "selfrun-sess-a")
    claude_logs = tmp_path / "claude-calls"
    queue = tmp_path / "queue"
    H.make_response_queue(queue, H.DELTA_SESSION_A)
    env = H.worker_env(home, memory_home, claude_log_dir=claude_logs, response_queue=queue)
    assert _enqueue(env, "selfrunproj", "selfrun-sess-a", transcript).returncode == 0

    status_path = memory_home / "projects" / "selfrunproj.extraction-status.json"
    seen_idle_inodes: set[tuple[int, int]] = set()
    stop = threading.Event()
    poller = threading.Thread(target=_poll_idle_inodes, args=(status_path, stop, seen_idle_inodes), daemon=True)
    poller.start()
    result = H.run_worker(["run", "--once", "--project", "selfrunproj"], env, timeout=30)
    stop.set()
    poller.join(timeout=5)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    assert len(seen_idle_inodes) == 1, (
        f"the poller must observe exactly the one real success write; observed "
        f"{len(seen_idle_inodes)} (0 would mean the mechanism itself is unreliable)"
    )
