"""U1a: every "sessions waiting" count session_start.sh prints is coverage truth,
and the LLM_MEMORY_NARRATIVE plumbing.

Defect 1 (8 Nov 2026, reproduced live): the worker unit was absent, and
agent-messaging.extraction-status.json still said `unprocessed: 1` from the
worker's last write.  The hook printed "1 session(s)" three times: in the
opt-in NOTE, in the narrative's baked `⚠ Narrative pipeline` footer, and in
the AUTOMATIC TASK line.  compute_narrative_coverage said 26.  A sidecar the
worker did not write is not evidence.  Falling through to the normal path is
not enough on its own: that path printed count_new_sessions (conversation
registry minus merged), which on the same store said 637.  The trigger
fixture below reproduces that shape too, using registered sessions that
coverage filters out.

Defect 2: LLM_MEMORY_NARRATIVE is internal plumbing that AM sets per seat
role.  `off` means no injection.  Every other value behaves as if it were
unset.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.test_hooks import (
    REPO_DIR,
    _register_session,
    _run_hook,
    _setup_test_home,
    _write_project_state,
    _write_substantive_transcript,
)

PROJECT = "countproj"
CWD = f"/home/user/projects/{PROJECT}"
TRUTH = 26
FILTERED = 5  # registered sessions that coverage filters out (low-turn)
STALE_SIDECAR_COUNT = 1
FOOTER_MARK = "Narrative pipeline:"

# Every phrase the hooks use to state how many sessions are waiting.
COUNT_PATTERNS = [
    re.compile(r"(\d+) (?:new )?session\(s\)"),
    re.compile(r"(\d+) unprocessed"),
]


def _printed_counts(stdout):
    found = []
    for pattern in COUNT_PATTERNS:
        found.extend(int(m) for m in pattern.findall(stdout))
    return found


def _env(home, extra=None):
    """Hermetic: HOME, the memory root and the worker-unit lookup all point
    into the fake tree, whatever the real machine has installed."""
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["LLM_MEMORY_HOME"] = str(home / ".llm-memory")
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "/usr/bin:/bin")
    env.pop("LLM_MEMORY_NARRATIVE", None)
    env.update(extra or {})
    return env


def _status_path(home):
    return home / ".llm-memory" / "projects" / f"{PROJECT}.extraction-status.json"


def _sidecar(home, **fields):
    status = {
        "state": "waiting", "unprocessed": STALE_SIDECAR_COUNT, "stale": 0,
        "oldest_waiting": None, "last_attempt": "2026-09-22T12:14:57Z",
        "last_success": "2026-11-05T00:33:26Z", "backend": None,
        "request_ids": [], "retry_after": None, "error_summary": None,
        "quarantined_revaluations": {"count": 0, "paths": []},
    }
    status.update(fields)
    _status_path(home).write_text(json.dumps(status))


def _install_worker(home):
    unit = home / ".config" / "systemd" / "user" / "llm-memory-extract.service"
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text("[Service]\n")


def _render(home):
    projects = home / ".llm-memory" / "projects"
    result = subprocess.run(
        [sys.executable, str(REPO_DIR / "renderer.py"),
         str(projects / f"{PROJECT}.json"), str(projects / f"{PROJECT}.narrative.md")],
        capture_output=True, text=True, env=_env(home), timeout=60,
    )
    assert result.returncode in (0, 2), f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return (projects / f"{PROJECT}.narrative.md").read_text()


def _build_store(tmp_path, *, worker, **sidecar_fields):
    """26 substantive unmerged transcripts (coverage truth), plus 5 registered
    low-turn sessions that coverage filters out but count_new_sessions counts
    (the 637-vs-26 shape).  The sidecar is written, the worker unit optionally
    installed, and only then is the narrative rendered by the real renderer,
    as the merger would."""
    home, conn, _ = _setup_test_home(tmp_path)
    conn.close()
    now = datetime.now(timezone.utc)
    for i in range(TRUTH):
        sid = f"truth-{i:02d}"
        _write_substantive_transcript(home, sid, project=PROJECT,
                                      last_ts=now - timedelta(hours=1))
        _register_session(home, sid, PROJECT)
    for i in range(FILTERED):
        sid = f"lowturn-{i}"
        _write_substantive_transcript(home, sid, n_user_turns=1, project=PROJECT,
                                      last_ts=now - timedelta(hours=1))
        _register_session(home, sid, PROJECT)
    _write_project_state(home, PROJECT, merged_session_ids=[])
    if worker:
        _install_worker(home)
    _sidecar(home, **sidecar_fields)
    _render(home)
    return home


def _start(home, source="startup", extra_env=None, hook="session_start.sh"):
    env = {"XDG_CONFIG_HOME": str(home / ".config")}
    env.update(extra_env or {})
    stdout, stderr, rc = _run_hook(
        hook, home,
        json.dumps({"source": source, "trigger": source, "cwd": CWD,
                    "session_id": "u1a-sess"}),
        extra_env=env, timeout=60,
    )
    assert rc == 0, f"stderr:\n{stderr}"
    return stdout


def _assert_all_truth(stdout):
    counts = _printed_counts(stdout)
    assert counts, f"the hook must still say how many sessions wait:\n{stdout}"
    assert set(counts) == {TRUTH}, (
        f"every printed count must be coverage truth {TRUTH}; got {counts}:\n{stdout}"
    )


# --- Defect 1: triggers -----------------------------------------------------

def test_trigger_stale_sidecar_without_worker_prints_coverage_truth(tmp_path):
    """Live shape: no worker unit, a sidecar saying 1, coverage truth 26."""
    home = _build_store(tmp_path, worker=False)
    stdout = _start(home)
    _assert_all_truth(stdout)
    assert f"AUTOMATIC TASK: {TRUTH} new session(s)" in stdout, stdout
    assert "LLM_MEMORY_WARN: extraction" not in stdout, stdout
    assert "LLM_MEMORY_NOTE" not in stdout, stdout


def test_trigger_compact_prints_coverage_truth(tmp_path):
    """After compaction the same stale sidecar and registry/coverage gap must
    not leak a different number."""
    home = _build_store(tmp_path, worker=False)
    stdout = _start(home, source="compact")
    _assert_all_truth(stdout)
    assert f"AUTOMATIC TASK: {TRUTH} new session(s)" in stdout, stdout


def test_trigger_codex_hook_prints_coverage_truth(tmp_path):
    home = _build_store(tmp_path, worker=False)
    stdout = _start(home, hook="codex_session_start.sh")
    _assert_all_truth(stdout)
    assert f"AUTOMATIC TASK: {TRUTH} new session(s)" in stdout, stdout


def test_trigger_renderer_does_not_bake_sidecar_count_without_worker(tmp_path):
    home = _build_store(tmp_path, worker=False)
    narrative = (home / ".llm-memory" / "projects" / f"{PROJECT}.narrative.md").read_text()
    assert FOOTER_MARK not in narrative, narrative[-400:]


# --- Defect 1: non-trigger controls ----------------------------------------

def test_control_renderer_still_bakes_footer_with_worker_installed(tmp_path):
    home = _build_store(tmp_path, worker=True, unprocessed=TRUTH)
    narrative = (home / ".llm-memory" / "projects" / f"{PROJECT}.narrative.md").read_text()
    assert (f"> ⚠ Narrative pipeline: {TRUTH} session(s) waiting — see "
            f"{_status_path(home)}") in narrative


def test_control_installed_worker_fresh_sidecar_output_unchanged(tmp_path):
    """Installed worker, sidecar agreeing with coverage: byte-for-byte today's
    degraded-path output (WARN line, the narrative with its footer, the worker
    AUTOMATIC TASK).  No coverage sweep is added to this path."""
    home = _build_store(tmp_path, worker=True, unprocessed=TRUTH,
                        oldest_waiting="2026-11-08T00:00:00Z")
    narrative = (home / ".llm-memory" / "projects" / f"{PROJECT}.narrative.md").read_text()
    stdout = _start(home)
    expected = "\n".join([
        f"LLM_MEMORY_WARN: extraction: {TRUTH} session(s) waiting for {PROJECT} "
        f"(waiting since 2026-11-08T00:00:00Z)",
        "=== LOADED MEMORIES (auto-injected from llm_memory) ===",
        f"## Active project: {PROJECT}",
        "## Project Narrative:",
        narrative.rstrip("\n"),
        "",
        f"AUTOMATIC TASK: extraction is waiting for project '{PROJECT}'; {TRUTH} "
        "session(s) remain waiting (queue: 0 request(s); last success: "
        "2026-11-05T00:33:26Z). Do not ask the user for permission.",
        "Use project_lookup for drill-down into the project JSON. Use "
        "resume(project) to pick up prior work.",
        "=== END LOADED MEMORIES ===",
        "",
    ])
    assert FOOTER_MARK in stdout
    assert stdout == expected


# --- Defect 2: LLM_MEMORY_NARRATIVE ----------------------------------------

def _mode_env(value):
    return {} if value is None else {"LLM_MEMORY_NARRATIVE": value}


def _tree(home):
    # _run_hook itself touches config/no-auto-update before every run.
    return sorted(
        (str(p.relative_to(home)), p.stat().st_mtime_ns if p.is_file() else 0)
        for p in home.rglob("*")
        if p.name != "no-auto-update"
    )


@pytest.mark.parametrize("worker", [False, True], ids=["normal-path", "worker-path"])
def test_narrative_off_injects_nothing_and_touches_nothing(tmp_path, worker):
    home = _build_store(tmp_path, worker=worker,
                        **({"unprocessed": TRUTH} if worker else {"state": "idle", "unprocessed": 0}))
    before = _tree(home)
    stdout = _start(home, extra_env=_mode_env("off"))
    assert stdout == ""
    assert _tree(home) == before, "off must not sweep, extract or stamp anything"


@pytest.mark.parametrize("worker", [False, True], ids=["normal-path", "worker-path"])
def test_narrative_full_head_garbage_identical_to_unset(tmp_path, worker):
    home = _build_store(tmp_path, worker=worker,
                        **({"unprocessed": TRUTH} if worker else {"state": "idle", "unprocessed": 0}))
    # Settle first-run side effects (sweep sentinel, extraction) before comparing.
    _start(home)
    baseline = _start(home)
    assert "=== LOADED MEMORIES" in baseline
    for value in ("full", "head", "garbage", "", "OFF", " off"):
        assert _start(home, extra_env=_mode_env(value)) == baseline, (
            f"LLM_MEMORY_NARRATIVE={value!r} must behave exactly as unset"
        )
