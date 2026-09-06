"""Shared fixtures for the self-running-extraction frozen tests
(T-selfrun, docs/design/self-running-extraction-2026-09-06.md).

Every helper here builds a fake $HOME + $LLM_MEMORY_HOME and fakes every
external the design note names (systemctl, the Claude backend command,
wall-clock time via LLM_MEMORY_NOW). Nothing here touches the real
$HOME, the real systemd user manager, or spends real money.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_DIR = Path(__file__).parent.parent.parent.parent
HOOKS_DIR = REPO_DIR / "hooks"
FIXTURES_DIR = Path(__file__).parent

FAKE_SYSTEMCTL = FIXTURES_DIR / "fake_systemctl.sh"
FAKE_CLAUDE = FIXTURES_DIR / "fake_claude.sh"

DELTA_SESSION_A = FIXTURES_DIR / "delta_session_a.json"
DELTA_SESSION_B = FIXTURES_DIR / "delta_session_b.json"
DELTA_WITH_REVALUATION = FIXTURES_DIR / "delta_with_revaluation.json"
DELTA_INVALID_JSON = FIXTURES_DIR / "delta_invalid_json.txt"
DELTA_MISSING_SESSION_ID = FIXTURES_DIR / "delta_missing_session_id.json"
DELTA_DANGLING_REFERENCE = FIXTURES_DIR / "delta_dangling_reference.json"


def make_home(tmp_path: Path) -> tuple[Path, Path]:
    """Create a fake $HOME and $LLM_MEMORY_HOME (kept distinct, as on a real
    machine post the memory-root relocation work) with the directory shape
    the pipeline expects. Returns (home, memory_home)."""
    home = tmp_path / "home"
    memory_home = tmp_path / "memory-home"
    for d in ("transcripts", "conversations", "config", "projects", "items", "runtime"):
        (memory_home / d).mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    (memory_home / "config" / "no-auto-update").touch()
    # hooks/session_start.sh gates its narrative/warning logic on this file's
    # mere existence (it never opens it for these tests' code paths) — an
    # absent memory.db makes the hook exit right after the sweep step,
    # silently emitting nothing, which would make every visibility control
    # here pass for the wrong reason (empty stdout, not "no warning").
    (memory_home / "memory.db").touch()
    return home, memory_home


def base_env(home: Path, memory_home: Path, *, extra: dict | None = None) -> dict:
    """Environment for subprocess.run: real PATH (so bash/python/jq resolve)
    with the fixture directory prepended so a bare `systemctl` / the backend
    command name resolves to our fakes when a test asks for that, plus
    HOME/LLM_MEMORY_HOME pointed at the fake tree."""
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["LLM_MEMORY_HOME"] = str(memory_home)
    env["PATH"] = (
        str(FIXTURES_DIR) + os.pathsep
        + str(Path(sys.executable).parent) + os.pathsep
        + env.get("PATH", "/usr/bin:/bin")
    )
    if extra:
        env.update(extra)
    return env


def run_hook(hook_name: str, home: Path, memory_home: Path, input_json: str,
             *, timeout: int = 30, extra_env: dict | None = None):
    """Run an installed hook script under a fake HOME/LLM_MEMORY_HOME.
    Returns (stdout, stderr, returncode, wall_seconds)."""
    import time
    env = base_env(home, memory_home, extra=extra_env)
    t0 = time.monotonic()
    result = subprocess.run(
        ["bash", str(HOOKS_DIR / hook_name)],
        input=input_json,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )
    wall = time.monotonic() - t0
    return result.stdout, result.stderr, result.returncode, wall


def write_project_state(memory_home: Path, project: str, state: dict | None = None) -> Path:
    path = memory_home / "projects" / f"{project}.json"
    path.write_text(json.dumps(state if state is not None else {
        "project": project,
        "decisions": [], "goals": [], "suggestions": [],
        "learnings": [], "done": [], "sessions": [],
    }))
    return path


def write_transcript(memory_home_transcripts_dir: Path, session_id: str,
                      n_user_turns: int = 5) -> Path:
    """A transcript with enough substantive turns to clear the narrative
    pipeline's content gate, mirroring tests/test_hooks.py's
    _write_substantive_transcript so selfrun tests exercise the same real
    archive/extract path the production SessionEnd hook already runs."""
    from datetime import datetime, timedelta, timezone
    base = datetime.now(timezone.utc)
    records = []
    for i in range(n_user_turns):
        records.append({
            "type": "user",
            "timestamp": (base - timedelta(hours=n_user_turns - i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "message": {"role": "user", "content": f"selfrun fixture turn {i}: please continue the work"},
        })
        records.append({
            "type": "assistant",
            "timestamp": (base - timedelta(hours=n_user_turns - i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "message": {
                "role": "assistant",
                "content": (
                    "Here is a substantive, multi-sentence assistant reply about "
                    "the selfrun fixture project so the content gate is cleared. "
                    "It discusses a concrete decision and a concrete next step, "
                    "which is exactly the kind of turn the extractor is meant to "
                    "read and the coverage filters are meant to accept as real work."
                ),
            },
        })
    path = memory_home_transcripts_dir / f"{session_id}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return path


def sha256_file(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_response_queue(queue_dir: Path, *delta_paths: Path) -> Path:
    """Populate a FAKE_CLAUDE_RESPONSE_QUEUE directory: response-1.json,
    response-2.json, ... one per call, in order, from the given delta
    fixture files (or raw text files, e.g. the invalid-JSON fixture)."""
    queue_dir.mkdir(parents=True, exist_ok=True)
    for i, src in enumerate(delta_paths, start=1):
        (queue_dir / f"response-{i}.json").write_text(Path(src).read_text())
    return queue_dir


def worker_env(home: Path, memory_home: Path, *, claude_log_dir: Path,
                response_queue: Path, extra: dict | None = None) -> dict:
    """Environment for invoking extraction_worker.py directly (not through a
    hook): fake systemctl/claude on PATH, LLM_MEMORY_CLAUDE_CMD pointed at
    the fake backend, and the fake backend wired to its log dir + response
    queue."""
    env = base_env(home, memory_home, extra=extra)
    env["LLM_MEMORY_CLAUDE_CMD"] = str(FAKE_CLAUDE)
    env["LLM_MEMORY_SYSTEMCTL"] = str(FAKE_SYSTEMCTL)
    env["FAKE_CLAUDE_LOG_DIR"] = str(claude_log_dir)
    env["FAKE_CLAUDE_RESPONSE_QUEUE"] = str(response_queue)
    return env


def run_worker(args: list[str], env: dict, *, timeout: int = 30):
    result = subprocess.run(
        [sys.executable, str(REPO_DIR / "extraction_worker.py")] + args,
        capture_output=True, text=True, env=env, timeout=timeout,
    )
    return result
