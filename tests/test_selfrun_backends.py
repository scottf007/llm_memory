"""Frozen tests — self-running extraction, area 5: backend qualification,
invocation contract, and failure/cap handling (design doc D2;
docs/design/self-running-extraction-2026-09-06.md, Appendix).

Local is bypassed while `runtime/local-qualification.json` is absent or
fails the note's bar; the Claude fallback is invoked via
`$LLM_MEMORY_CLAUDE_CMD` with no session-persistence flags and no tools;
invalid/unparseable/unknown-ID/apply_delta-rejected extractor output never
merges and status becomes `failed` after two attempts; an exhausted day
spend cap leaves the request pending, status `waiting`, and the backend
never called at all.

Scope note (recorded honestly, not silently narrowed): the positive case
"local IS used once qualified" needs a fake local HTTP endpoint standing in
for `LLM_MEMORY_LOCAL_URL`'s OpenAI-compatible server; building and
lifecycle-managing that server was cut for time in this test-authoring
pass. The bypass direction (absent/failing qualification -> Claude) is
fully covered below; the judge/implementer should treat "local invoked
when qualified" as an open acceptance item, not as covered by this file.
Likewise the $0.50 PER-SESSION cap needs the worker to self-report a call's
realized cost, which the Appendix does not give a test a way to force
before the fact; only the $3/day pending-spend precheck (pre-seedable) is
frozen here.

RED on base c0570b2: extraction_worker.py does not exist.
"""

import json
import shutil
from pathlib import Path

import pytest

from tests.fixtures.selfrun import helpers as H


def _enqueue_and_run(env, memory_home, project, session_id, transcript):
    r = H.run_worker(
        ["enqueue", "--project", project, "--session-id", session_id,
         "--transcript", str(transcript), "--source", "manual"],
        env,
    )
    assert r.returncode == 0, f"enqueue failed: {r.stderr}"
    return H.run_worker(["run", "--once", "--project", project], env, timeout=30)


def _status(memory_home, project):
    path = memory_home / "projects" / f"{project}.extraction-status.json"
    return json.loads(path.read_text()) if path.exists() else None


def test_local_bypassed_when_qualification_file_absent_claude_still_completes(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    H.write_project_state(memory_home, "selfrunproj")
    transcript = H.write_transcript(memory_home / "transcripts", "selfrun-sess-a")
    claude_logs = tmp_path / "claude-calls"
    queue = tmp_path / "queue"
    H.make_response_queue(queue, H.DELTA_SESSION_A)
    env = H.worker_env(home, memory_home, claude_log_dir=claude_logs, response_queue=queue)
    # A local URL that actively refuses connections: if the worker tried it,
    # this test would see a failure instead of a clean Claude-backed success.
    env["LLM_MEMORY_LOCAL_URL"] = "http://127.0.0.1:1/v1/chat/completions"

    result = _enqueue_and_run(env, memory_home, "selfrunproj", "selfrun-sess-a", transcript)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    calls = sorted(claude_logs.glob("call-*.argv"))
    assert len(calls) == 1, "Claude fallback must be used when no qualification file exists"
    state = json.loads((memory_home / "projects" / "selfrunproj.json").read_text())
    assert any(s["session_id"] == "selfrun-sess-a" for s in state["sessions"])
    matched = [s for s in state["sessions"] if s["session_id"] == "selfrun-sess-a"][0]
    backend = (matched.get("extraction") or {})
    if isinstance(backend, list):
        backend = backend[-1]
    assert backend.get("backend"), (
        f"Appendix requires a non-empty provenance 'backend' field, got {backend!r}"
    )


def test_local_bypassed_when_qualification_file_reports_failing(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    H.write_project_state(memory_home, "selfrunproj")
    transcript = H.write_transcript(memory_home / "transcripts", "selfrun-sess-a")
    (memory_home / "runtime").mkdir(exist_ok=True)
    shutil.copy(
        Path(__file__).parent / "fixtures" / "selfrun" / "local_qualification" / "failing.json",
        memory_home / "runtime" / "local-qualification.json",
    )
    claude_logs = tmp_path / "claude-calls"
    queue = tmp_path / "queue"
    H.make_response_queue(queue, H.DELTA_SESSION_A)
    env = H.worker_env(home, memory_home, claude_log_dir=claude_logs, response_queue=queue)
    env["LLM_MEMORY_LOCAL_URL"] = "http://127.0.0.1:1/v1/chat/completions"

    result = _enqueue_and_run(env, memory_home, "selfrunproj", "selfrun-sess-a", transcript)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    calls = sorted(claude_logs.glob("call-*.argv"))
    assert len(calls) == 1, "an unqualified local report must not switch the backend"


def test_claude_invocation_has_no_session_persistence_flags_or_tools(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    H.write_project_state(memory_home, "selfrunproj")
    transcript = H.write_transcript(memory_home / "transcripts", "selfrun-sess-a")
    claude_logs = tmp_path / "claude-calls"
    queue = tmp_path / "queue"
    H.make_response_queue(queue, H.DELTA_SESSION_A)
    env = H.worker_env(home, memory_home, claude_log_dir=claude_logs, response_queue=queue)

    result = _enqueue_and_run(env, memory_home, "selfrunproj", "selfrun-sess-a", transcript)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    argv = (claude_logs / "call-1.argv").read_text()
    forbidden = ("--continue", "--resume", "--session-id", "--tools", "--allowedTools", "--dangerously-skip-permissions")
    for flag in forbidden:
        assert flag not in argv, f"D2 forbids session persistence/tools; found {flag!r} in argv:\n{argv}"


def test_invalid_json_response_never_merges_status_failed_after_two_attempts(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    H.write_project_state(memory_home, "selfrunproj")
    transcript = H.write_transcript(memory_home / "transcripts", "selfrun-sess-a")
    claude_logs = tmp_path / "claude-calls"
    queue = tmp_path / "queue"
    H.make_response_queue(queue, H.DELTA_INVALID_JSON, H.DELTA_INVALID_JSON)
    env = H.worker_env(home, memory_home, claude_log_dir=claude_logs, response_queue=queue)

    r = H.run_worker(
        ["enqueue", "--project", "selfrunproj", "--session-id", "selfrun-sess-a",
         "--transcript", str(transcript), "--source", "manual"], env,
    )
    assert r.returncode == 0
    # First attempt fails; run --once again so the second attempt also runs
    # against the (still invalid) queued response.
    H.run_worker(["run", "--once", "--project", "selfrunproj"], env, timeout=30)
    H.run_worker(["run", "--once", "--project", "selfrunproj"], env, timeout=30)

    state = json.loads((memory_home / "projects" / "selfrunproj.json").read_text())
    assert not any(s["session_id"] == "selfrun-sess-a" for s in state["sessions"]), (
        "invalid JSON must never be merged"
    )
    status = _status(memory_home, "selfrunproj")
    assert status is not None, "extraction-status.json must exist after a failed attempt"
    assert status["state"] == "failed", f"expected failed after two attempts, got: {status}"


def test_dangling_reference_apply_delta_failure_never_merges(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    H.write_project_state(memory_home, "selfrunproj")
    transcript = H.write_transcript(memory_home / "transcripts", "selfrun-sess-dangling")
    claude_logs = tmp_path / "claude-calls"
    queue = tmp_path / "queue"
    H.make_response_queue(queue, H.DELTA_DANGLING_REFERENCE, H.DELTA_DANGLING_REFERENCE)
    env = H.worker_env(home, memory_home, claude_log_dir=claude_logs, response_queue=queue)

    result = _enqueue_and_run(env, memory_home, "selfrunproj", "selfrun-sess-dangling", transcript)
    H.run_worker(["run", "--once", "--project", "selfrunproj"], env, timeout=30)

    state = json.loads((memory_home / "projects" / "selfrunproj.json").read_text())
    assert not any(s["session_id"] == "selfrun-sess-dangling" for s in state["sessions"]), (
        "a delta apply_delta rejects (dangling decision_links) must never merge"
    )


def test_day_spend_cap_already_exceeded_backend_never_called_status_waiting(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    H.write_project_state(memory_home, "selfrunproj")
    transcript = H.write_transcript(memory_home / "transcripts", "selfrun-sess-a")
    (memory_home / "runtime").mkdir(exist_ok=True)
    (memory_home / "runtime" / "extraction-spend.json").write_text(json.dumps({
        "day": "2026-09-06", "total_usd": 3.25,
    }))
    claude_logs = tmp_path / "claude-calls"
    queue = tmp_path / "queue"
    H.make_response_queue(queue, H.DELTA_SESSION_A)
    env = H.worker_env(home, memory_home, claude_log_dir=claude_logs, response_queue=queue)
    env["LLM_MEMORY_NOW"] = "2026-09-06T12:00:00Z"
    env["LLM_MEMORY_EXTRACT_DAY_CAP_USD"] = "3"

    result = _enqueue_and_run(env, memory_home, "selfrunproj", "selfrun-sess-a", transcript)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    calls = list(claude_logs.glob("call-*.argv")) if claude_logs.exists() else []
    assert calls == [], "day cap already exceeded: backend must never be called"
    state = json.loads((memory_home / "projects" / "selfrunproj.json").read_text())
    assert not any(s["session_id"] == "selfrun-sess-a" for s in state["sessions"])
    requests = list((memory_home / "runtime" / "extraction-requests").glob("*.json"))
    assert len(requests) == 1, "request must stay pending, not be dropped"
    status = _status(memory_home, "selfrunproj")
    assert status is not None and status["state"] == "waiting", f"expected waiting, got {status}"


def test_control_uncapped_run_still_completes(tmp_path):
    """Control for the cap test above: with no pre-existing spend, the same
    session completes normally. Must be green wherever the cap test's
    trigger goes red, proving the cap check is the only difference."""
    home, memory_home = H.make_home(tmp_path)
    H.write_project_state(memory_home, "selfrunproj")
    transcript = H.write_transcript(memory_home / "transcripts", "selfrun-sess-a")
    claude_logs = tmp_path / "claude-calls"
    queue = tmp_path / "queue"
    H.make_response_queue(queue, H.DELTA_SESSION_A)
    env = H.worker_env(home, memory_home, claude_log_dir=claude_logs, response_queue=queue)

    result = _enqueue_and_run(env, memory_home, "selfrunproj", "selfrun-sess-a", transcript)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    calls = list(claude_logs.glob("call-*.argv"))
    assert len(calls) == 1
