"""Frozen tests — self-running extraction, area 7: persistent failure
visibility (design doc D3; acceptance #5;
docs/design/self-running-extraction-2026-09-06.md, Appendix).

With `$LLM_MEMORY_HOME/projects/{project}.extraction-status.json` showing a
failure/wait, `renderer.py` must emit the literal footer
`⚠ Narrative pipeline: {N} session(s) waiting — see {status path}` and the
installed `hooks/session_start.sh` must print
`LLM_MEMORY_WARN: extraction: {N} session(s) waiting for {project}
({state} since {iso})` on stdout — without running a coverage sweep or a
model subprocess to do it (cheap sidecar read only). An idle status, or no
status file at all, must change nothing.

RED on base c0570b2: renderer.py never reads an extraction-status sidecar
and never emits this footer; session_start.sh never prints this literal.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fixtures.selfrun import helpers as H

FOOTER_MARK = "Narrative pipeline:"
WARN_MARK = "LLM_MEMORY_WARN: extraction"


def _render(memory_home, project, home=None):
    state_path = memory_home / "projects" / f"{project}.json"
    out_path = memory_home / "projects" / f"{project}.narrative.md"
    env = os.environ.copy()
    env["LLM_MEMORY_HOME"] = str(memory_home)
    if home is not None:
        # The footer is gated on the worker unit (U1a); look for it in the
        # fake HOME, never on the machine running the suite.
        env["HOME"] = str(home)
        env.pop("XDG_CONFIG_HOME", None)
    result = subprocess.run(
        [sys.executable, str(H.REPO_DIR / "renderer.py"), str(state_path), str(out_path)],
        capture_output=True, text=True, env=env, timeout=30,
    )
    return result, out_path


def _write_status(memory_home, project, **fields):
    status = {
        "state": "idle", "unprocessed": 0, "stale": 0, "oldest_waiting": None,
        "last_attempt": None, "last_success": None, "backend": None,
        "request_ids": [], "retry_after": None, "error_summary": None,
        "quarantined_revaluations": {"count": 0, "paths": []},
    }
    status.update(fields)
    path = memory_home / "projects" / f"{project}.extraction-status.json"
    path.write_text(json.dumps(status))
    return path


def test_renderer_emits_waiting_footer_from_status_sidecar(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    # U1a: the footer reports the worker's sidecar, so it is baked only while
    # the worker unit is installed (sanctioned amendment, llm-pm-1108).
    H.install_worker_unit(home)
    H.write_project_state(memory_home, "selfrunproj")
    status_path = _write_status(
        memory_home, "selfrunproj",
        state="waiting", unprocessed=3, oldest_waiting="2026-09-01T00:00:00Z",
    )
    result, out_path = _render(memory_home, "selfrunproj", home=home)
    assert result.returncode in (0, 2), f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    md = out_path.read_text()
    assert FOOTER_MARK in md, f"expected the pipeline-waiting footer in rendered output:\n{md[-500:]}"
    assert "3 session(s) waiting" in md
    assert str(status_path) in md, "footer must name the status path"


def test_control_idle_status_emits_no_footer(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    H.write_project_state(memory_home, "selfrunproj")
    _write_status(memory_home, "selfrunproj", state="idle", unprocessed=0)
    result, out_path = _render(memory_home, "selfrunproj")
    assert result.returncode in (0, 2)
    md = out_path.read_text()
    assert FOOTER_MARK not in md


def test_control_no_status_file_identical_to_today(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    H.write_project_state(memory_home, "selfrunproj")
    result, out_path = _render(memory_home, "selfrunproj")
    assert result.returncode in (0, 2)
    md = out_path.read_text()
    assert FOOTER_MARK not in md


def test_session_start_prints_warn_for_failed_status_no_coverage_or_model_subprocess(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    H.install_worker_unit(home)
    H.write_project_state(memory_home, "selfrunproj")
    _write_status(
        memory_home, "selfrunproj",
        state="failed", unprocessed=2, last_attempt="2026-09-06T00:00:00Z",
    )
    systemctl_log = tmp_path / "systemctl.log"
    claude_logs = tmp_path / "claude-calls"

    input_json = json.dumps({
        "source": "startup", "trigger": "startup",
        "cwd": "/home/user/projects/selfrunproj",
        "session_id": "selfrun-visibility-sess",
    })
    stdout, stderr, rc, wall = H.run_hook(
        "session_start.sh", home, memory_home, input_json,
        extra_env={
            "FAKE_SYSTEMCTL_LOG": str(systemctl_log),
            "FAKE_CLAUDE_LOG_DIR": str(claude_logs),
        },
    )
    assert rc == 0, f"stderr:\n{stderr}"
    assert WARN_MARK in stdout, f"expected {WARN_MARK!r} on stdout, got:\n{stdout}"
    assert "selfrunproj" in stdout
    assert "failed" in stdout

    assert not systemctl_log.exists() or systemctl_log.read_text() == "", (
        "SessionStart must never dispatch the extraction worker itself"
    )
    assert not claude_logs.exists() or list(claude_logs.glob("call-*.argv")) == [], (
        "SessionStart must never invoke a model backend"
    )


def _start(home, memory_home, sid):
    input_json = json.dumps({
        "source": "startup", "trigger": "startup",
        "cwd": "/home/user/projects/selfrunproj", "session_id": sid,
    })
    stdout, stderr, rc, _ = H.run_hook("session_start.sh", home, memory_home, input_json)
    assert rc == 0, f"stderr:\n{stderr}"
    return stdout


def test_uninstalled_worker_failed_status_is_a_note_not_a_failure(tmp_path):
    """The worker is opt-in. A "failed" sidecar left behind when its unit was
    removed must not be narrated as a live outage, nor as never-succeeded.

    Amended by U1a (sanctioned by llm-pm-1108, 8 Nov 2026).  The original
    fixture had no transcripts and asserted the sidecar's own "2", which pinned
    the defect: without the worker, nobody keeps that number current.  The
    count printed now is coverage truth (3 here, deliberately not the
    sidecar's 2), and the opt-in NOTE is gone.  The AUTOMATIC TASK line that
    carries the truth count is the signal."""
    from tests.test_codex_hook_parity import write_session_transcript
    home, memory_home = H.make_home(tmp_path)
    H.write_project_state(memory_home, "selfrunproj")
    (memory_home / "projects" / "selfrunproj.narrative.md").write_text("# selfrunproj\n")
    for i in range(3):
        sid = f"selfrun-optin-{i}"
        (memory_home / "conversations" / f"{sid}.md").write_text(
            f"---\nsession_id: {sid}\nproject: selfrunproj\n---\n\n=== user ===\nhi\n")
        write_session_transcript(memory_home, sid, "selfrunproj")
    _write_status(memory_home, "selfrunproj", state="failed", unprocessed=2,
                  last_attempt="2026-09-06T00:00:00Z")
    stdout = _start(home, memory_home, "selfrun-optin-sess")
    assert "LLM_MEMORY_WARN" not in stdout, stdout
    assert "NEVER succeeded" not in stdout, stdout
    assert "LLM_MEMORY_NOTE" not in stdout, stdout
    assert "2 session(s)" not in stdout, stdout
    assert "AUTOMATIC TASK: 3 new session(s) since last narrative update for project 'selfrunproj'." in stdout


def test_never_succeeded_counts_only_this_projects_requests(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    H.install_worker_unit(home)
    H.write_project_state(memory_home, "selfrunproj")
    _write_status(memory_home, "selfrunproj", state="failed", unprocessed=0)
    queue = memory_home / "runtime" / "extraction-requests"
    queue.mkdir(parents=True)
    for i, project in enumerate(["selfrunproj", "other", "other", "other"]):
        (queue / f"r{i}.json").write_text(json.dumps(
            {"project": project, "session_id": f"s{i}", "request_id": f"id{i}"}, indent=2))
    stdout = _start(home, memory_home, "selfrun-count-sess")
    assert "has NEVER succeeded (state=failed, 1 queued for this project)" in stdout, stdout


def test_control_idle_extraction_status_no_warn_on_session_start(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    H.write_project_state(memory_home, "selfrunproj")
    _write_status(memory_home, "selfrunproj", state="idle", unprocessed=0)
    input_json = json.dumps({
        "source": "startup", "trigger": "startup",
        "cwd": "/home/user/projects/selfrunproj",
        "session_id": "selfrun-visibility-sess-2",
    })
    stdout, stderr, rc, wall = H.run_hook("session_start.sh", home, memory_home, input_json)
    assert rc == 0
    assert WARN_MARK not in stdout


def test_control_no_status_file_no_warn_on_session_start(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    H.write_project_state(memory_home, "selfrunproj")
    input_json = json.dumps({
        "source": "startup", "trigger": "startup",
        "cwd": "/home/user/projects/selfrunproj",
        "session_id": "selfrun-visibility-sess-3",
    })
    stdout, stderr, rc, wall = H.run_hook("session_start.sh", home, memory_home, input_json)
    assert rc == 0
    assert WARN_MARK not in stdout
