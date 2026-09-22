"""Retirement of requests that can never succeed.

The existing selfrun contracts cover retirement on the SUCCESS path: drain,
merge, retire.  None of them covered a request for a session that is already
in {project}.json but which this worker did not merge itself -- the case that
occurs whenever a narrative is drained by hand (the /narrative skill) or by
another machine.

That gap is why the defect survived a green suite.  The merger refuses such a
session, _process_project marked it deferred and KEPT the request, and the same
paid extraction was retried on every pass.  The live queue reached 10,720
requests on 2026-09-22, one of them with attempts=141, while the suite passed.

Every test here asserts NO backend call was made, because the cost of the bug
was not the queue length -- it was paying a model to rediscover impossibility.
"""

from __future__ import annotations

import json

from tests.fixtures.selfrun import helpers as H

from tests.test_selfrun_contracts import (  # noqa: E402
    _enqueue,
    _project_state,
    _requests,
    _status,
    _write_transcript_ending_at,
)

PROJECT = "selfrunproj"
SESSION = "selfrun-sess-a"
END_ISO = "2026-09-01T01:00:00Z"


def _merged_state(session_id: str, ended: str = END_ISO) -> dict:
    """Project state that already contains the session, as a hand-run
    /narrative drain or another machine's sync would leave it."""
    return {
        "project": PROJECT,
        "decisions": [], "goals": [], "suggestions": [],
        "learnings": [], "done": [],
        "sessions": [{"session_id": session_id, "ended": ended,
                      "extraction": {"cost_usd": 0.01, "cost_source": "reported"}}],
    }


def _setup(tmp_path, state):
    home, memory_home = H.make_home(tmp_path)
    H.write_project_state(memory_home, PROJECT, state)
    transcript = _write_transcript_ending_at(memory_home / "transcripts", SESSION, END_ISO)
    claude_logs = tmp_path / "claude-calls"
    queue = tmp_path / "queue"
    H.make_response_queue(queue, H.DELTA_SESSION_A)
    env = H.worker_env(home, memory_home, claude_log_dir=claude_logs, response_queue=queue)
    return home, memory_home, transcript, env, claude_logs


def test_request_for_an_already_merged_session_is_retired_without_a_backend_call(tmp_path):
    """The immortal-request case, reproduced.

    Before the fix this left the request on disk and incremented attempts
    forever, one paid extraction per pass."""
    _, memory_home, transcript, env, logs = _setup(tmp_path, _merged_state(SESSION))
    assert _enqueue(env, PROJECT, SESSION, transcript).returncode == 0

    result = H.run_worker(["run", "--once", "--project", PROJECT], env, timeout=30)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    assert _requests(memory_home) == [], "request for an already-merged session must be retired"
    assert list(logs.glob("call-*.argv")) == [], "must not pay a model to rediscover impossibility"


def test_retirement_does_not_invent_a_second_session_entry(tmp_path):
    """Retiring must not touch the ledger: the merged session stays exactly
    once, with the record the earlier drain wrote."""
    _, memory_home, transcript, env, _ = _setup(tmp_path, _merged_state(SESSION))
    assert _enqueue(env, PROJECT, SESSION, transcript).returncode == 0
    H.run_worker(["run", "--once", "--project", PROJECT], env, timeout=30)

    sessions = _project_state(memory_home, PROJECT)["sessions"]
    assert [s["session_id"] for s in sessions] == [SESSION]
    assert sessions[0]["extraction"]["cost_source"] == "reported"


def test_cap_reserved_session_keeps_its_request_as_the_record_of_the_block(tmp_path):
    """The one merged session that must NOT be retired.

    _merge blocks a retry when a session's extraction reserved the per-session
    spend cap, and the pending request is the only record that a retry was
    blocked.  Retiring it erases that evidence and flips the reported state
    from waiting to idle.  _merged_session_ids mirrors _merge's condition; this
    test is what stops the two drifting."""
    state = _merged_state(SESSION)
    state["sessions"][0]["extraction"] = {"cost_usd": None, "cost_source": "unknown"}
    _, memory_home, transcript, env, logs = _setup(tmp_path, state)
    assert _enqueue(env, PROJECT, SESSION, transcript).returncode == 0

    H.run_worker(["run", "--once", "--project", PROJECT], env, timeout=30)

    assert _requests(memory_home) != [], "cap-reserved block must stay visible as a pending request"
    assert list(logs.glob("call-*.argv")) == [], "a blocked retry must still not call the backend"


def test_request_whose_transcript_vanished_is_retired(tmp_path):
    """A request naming a transcript that no longer exists can never be
    extracted.  One such request was live in the 2026-09-22 queue."""
    _, memory_home, transcript, env, logs = _setup(tmp_path, None or {
        "project": PROJECT, "decisions": [], "goals": [], "suggestions": [],
        "learnings": [], "done": [], "sessions": [],
    })
    assert _enqueue(env, PROJECT, SESSION, transcript).returncode == 0
    transcript.unlink()

    result = H.run_worker(["run", "--once", "--project", PROJECT], env, timeout=30)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    assert _requests(memory_home) == [], "request for a missing transcript must be retired"
    assert list(logs.glob("call-*.argv")) == []


def test_an_old_request_is_NOT_retired_while_its_project_still_exists(tmp_path):
    """Age is never sufficient. This is the blocker a reviewer caught.

    An earlier version retired on a timestamp compare alone. A session coverage
    cannot yet see -- no conversation.md, so no project attribution -- is
    invisible to coverage and will never be re-enqueued, because session_end
    fires once. Deleting its request on a birthday destroys the only pointer to
    work that was still perfectly doable."""
    _, memory_home, transcript, env, logs = _setup(tmp_path, {
        "project": PROJECT, "decisions": [], "goals": [], "suggestions": [],
        "learnings": [], "done": [], "sessions": [],
    })
    assert _enqueue(env, PROJECT, SESSION, transcript).returncode == 0
    env["LLM_MEMORY_REQUEST_MAX_AGE_DAYS"] = "0"      # everything is "expired"

    result = H.run_worker(["run", "--once", "--project", PROJECT], env, timeout=30)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    assert any(s["session_id"] == SESSION for s in _project_state(memory_home, PROJECT)["sessions"]), \
        "an old but doable request must still be merged, not aged out"
    assert len(list(logs.glob("call-*.argv"))) == 1


def test_orphaned_request_is_retired_when_its_project_state_is_gone(tmp_path):
    """The narrow case age retirement is actually for: no project to merge into,
    which no amount of re-running can fix."""
    _, memory_home, transcript, env, logs = _setup(tmp_path, {
        "project": PROJECT, "decisions": [], "goals": [], "suggestions": [],
        "learnings": [], "done": [], "sessions": [],
    })
    assert _enqueue(env, PROJECT, SESSION, transcript).returncode == 0
    (memory_home / "projects" / f"{PROJECT}.json").unlink()
    env["LLM_MEMORY_REQUEST_MAX_AGE_DAYS"] = "0"

    result = H.run_worker(["run", "--once", "--project", PROJECT], env, timeout=30)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    assert _requests(memory_home) == [], "a request whose project is gone must be retired"
    assert list(logs.glob("call-*.argv")) == []


def test_a_request_whose_recorded_path_is_stale_is_rebased_not_deleted(tmp_path):
    """The store root moved, so every request enqueued before the move carries
    an absolute path that no longer resolves. Treating a dangling path as
    'transcript gone' would unlink the only pointer to that work."""
    _, memory_home, transcript, env, logs = _setup(tmp_path, {
        "project": PROJECT, "decisions": [], "goals": [], "suggestions": [],
        "learnings": [], "done": [], "sessions": [],
    })
    assert _enqueue(env, PROJECT, SESSION, str(tmp_path / "old-root" / "transcripts" / f"{SESSION}.jsonl")).returncode == 0
    assert transcript.is_file(), "the transcript is still present under the CURRENT root"

    result = H.run_worker(["run", "--once", "--project", PROJECT], env, timeout=30)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    assert any(s["session_id"] == SESSION for s in _project_state(memory_home, PROJECT)["sessions"]), \
        "a stale recorded path must be rebased onto the current root, not treated as missing"
    assert len(list(logs.glob("call-*.argv"))) == 1


def test_cap_hold_survives_a_duplicate_under_cap_session_row(tmp_path):
    """sessions[] can carry more than one row per session id. A single
    under-cap row must not cancel a cap-reserved hold."""
    state = _merged_state(SESSION)
    state["sessions"].append({"session_id": SESSION, "ended": END_ISO,
                              "extraction": {"cost_usd": None, "cost_source": "unknown"}})
    _, memory_home, transcript, env, logs = _setup(tmp_path, state)
    assert _enqueue(env, PROJECT, SESSION, transcript).returncode == 0

    H.run_worker(["run", "--once", "--project", PROJECT], env, timeout=30)

    assert _requests(memory_home) != [], "the cap hold must be sticky across duplicate rows"
    assert list(logs.glob("call-*.argv")) == []


def test_a_genuinely_unprocessed_session_is_still_merged_not_retired(tmp_path):
    """Guard against the wrong fix.

    An earlier version retired any session coverage did not list.  Coverage
    cannot attribute a session to a project until its conversation.md exists,
    so "not listed" means "not yet visible", not "settled" -- and that version
    dropped live work, breaking nine tests.  This is the control that keeps the
    retirement rules narrow."""
    _, memory_home, transcript, env, logs = _setup(tmp_path, {
        "project": PROJECT, "decisions": [], "goals": [], "suggestions": [],
        "learnings": [], "done": [], "sessions": [],
    })
    assert _enqueue(env, PROJECT, SESSION, transcript).returncode == 0

    result = H.run_worker(["run", "--once", "--project", PROJECT], env, timeout=30)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    assert any(s["session_id"] == SESSION for s in _project_state(memory_home, PROJECT)["sessions"]), \
        "a real unprocessed session must still merge"
    assert len(list(logs.glob("call-*.argv"))) == 1
