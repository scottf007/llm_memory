"""Frozen tests — self-running extraction, area 3: the shared per-project
narrative lock (design doc D1, acceptance #2;
docs/design/self-running-extraction-2026-09-06.md, Appendix).

`narrative_lock.py` (repo root) must provide:
  - `project_lock(home: Path, project: str, *, wait: bool = False)`, a
    context manager over `{home}/runtime/locks/narrative/{project}.lock`
    that raises `NarrativeLockBusy` when contended and `wait=False`.
  - `inherited_lock(home, project)`, which reads
    `LLM_MEMORY_NARRATIVE_LOCK="{project}:{fd}"` and verifies the fd is
    that lock file BY INODE — a forged value (right shape, wrong file) must
    be refused, not trusted by name.

`merger.main`/`renderer.main` must acquire this lock by default (no
`--no-lock` escape hatch); the losing run prints
`LLM_MEMORY_WARN: narrative update already running for {project}; retry
after it finishes` and exits 3, touching no state file. The installed
`skills/narrative/SKILL.md` must document acquiring the same lock.

RED on base c0570b2: `narrative_lock` does not exist (ImportError); merger/
renderer take no lock today (no warning, exit 0, not 3); the SKILL.md has no
lock-acquisition step. The uncontended-run tests are controls that must
already be GREEN on base (today's only behavior) and stay green unchanged
on the candidate — that's also what tests/test_merger.py and
tests/test_renderer.py already assert, unmodified, for the same claim.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.fixtures.selfrun import helpers as H

REPO_DIR = H.REPO_DIR


def test_narrative_lock_module_importable():
    """RED marker: this is the plainest possible signal that the Appendix's
    module does not exist yet on base."""
    import narrative_lock  # noqa: F401  (ImportError on base is the point)
    assert hasattr(narrative_lock, "project_lock")
    assert hasattr(narrative_lock, "inherited_lock")
    assert hasattr(narrative_lock, "NarrativeLockBusy")


def test_project_lock_path_is_the_documented_location(tmp_path):
    import narrative_lock
    home = tmp_path / "memory-home"
    home.mkdir()
    with narrative_lock.project_lock(home, "demoproj"):
        lock_path = home / "runtime" / "locks" / "narrative" / "demoproj.lock"
        assert lock_path.exists(), "holding the lock must materialize the lock file"


def test_second_non_waiting_acquire_raises_busy(tmp_path):
    import narrative_lock
    home = tmp_path / "memory-home"
    home.mkdir()
    with narrative_lock.project_lock(home, "demoproj"):
        with pytest.raises(narrative_lock.NarrativeLockBusy):
            with narrative_lock.project_lock(home, "demoproj", wait=False):
                pass  # pragma: no cover


def test_two_processes_are_serialized_on_the_same_project_lock(tmp_path):
    """Cross-process contention, not just cross-thread: spawn a real holder
    subprocess, confirm a second acquire fails while it holds the lock, and
    succeeds again once it exits."""
    import narrative_lock  # RED gate: fails fast on base
    home = tmp_path / "memory-home"
    home.mkdir()
    marker = tmp_path / "holder-has-lock"

    holder_src = f"""
import sys, time
sys.path.insert(0, {str(REPO_DIR)!r})
import narrative_lock
from pathlib import Path
with narrative_lock.project_lock(Path({str(home)!r}), "demoproj"):
    Path({str(marker)!r}).touch()
    time.sleep(2)
"""
    proc = subprocess.Popen([sys.executable, "-c", holder_src])
    try:
        for _ in range(100):
            if marker.exists():
                break
            time.sleep(0.05)
        assert marker.exists(), "holder subprocess never signaled it took the lock"

        with pytest.raises(narrative_lock.NarrativeLockBusy):
            with narrative_lock.project_lock(home, "demoproj", wait=False):
                pass  # pragma: no cover
    finally:
        proc.wait(timeout=10)

    # Holder has exited: the lock must be free again.
    with narrative_lock.project_lock(home, "demoproj", wait=False):
        pass


def test_inherited_lock_verifies_by_identity_not_by_name(tmp_path, monkeypatch):
    """A forged env value naming the right project but not actually holding
    (or not even referring to) the real lock file must be refused."""
    import narrative_lock
    home = tmp_path / "memory-home"
    home.mkdir()

    # Forged: points at an unrelated open file, not the project's lock file.
    decoy = tmp_path / "decoy.txt"
    decoy.write_text("not a lock")
    decoy_fd = os.open(decoy, os.O_RDONLY)
    try:
        monkeypatch.setenv("LLM_MEMORY_NARRATIVE_LOCK", f"demoproj:{decoy_fd}")
        with pytest.raises(Exception):
            narrative_lock.inherited_lock(home, "demoproj")
    finally:
        os.close(decoy_fd)


def test_inherited_lock_accepts_a_genuine_handle(tmp_path, monkeypatch):
    import narrative_lock
    home = tmp_path / "memory-home"
    home.mkdir()
    lock_path = home / "runtime" / "locks" / "narrative" / "demoproj.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.touch()
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT)
    try:
        monkeypatch.setenv("LLM_MEMORY_NARRATIVE_LOCK", f"demoproj:{fd}")
        handle = narrative_lock.inherited_lock(home, "demoproj")
        assert handle is not None
    finally:
        os.close(fd)


def _write_state_and_delta(memory_home):
    state_path = H.write_project_state(memory_home, "demoproj")
    delta_path = memory_home / "delta.json"
    delta_path.write_text(json.dumps({
        "session_id": "lock-contend-sess",
        "started": "2026-09-06T00:00:00Z",
        "ended": "2026-09-06T01:00:00Z",
        "topic": "t",
        "ledger_delta": {"introduced": {"decisions": [{"text": "SELFRUN-LOCK-MARKER"}]}},
    }))
    return state_path, delta_path


def test_merger_main_yields_to_a_held_lock_prints_warning_exits_3_touches_nothing(tmp_path, monkeypatch):
    import narrative_lock  # noqa: F401  (RED gate: also drives the holder subprocess below)
    memory_home = tmp_path / "memory-home"
    (memory_home / "projects").mkdir(parents=True)
    state_path, delta_path = _write_state_and_delta(memory_home)
    monkeypatch.setenv("LLM_MEMORY_HOME", str(memory_home))

    sha_before = H.sha256_file(state_path)
    mtime_before = state_path.stat().st_mtime_ns

    holder_src = f"""
import sys, time
sys.path.insert(0, {str(REPO_DIR)!r})
import narrative_lock
from pathlib import Path
with narrative_lock.project_lock(Path({str(memory_home)!r}), "demoproj"):
    time.sleep(3)
"""
    holder = subprocess.Popen([sys.executable, "-c", holder_src])
    try:
        time.sleep(0.5)
        env = os.environ.copy()
        env["LLM_MEMORY_HOME"] = str(memory_home)
        result = subprocess.run(
            [sys.executable, str(REPO_DIR / "merger.py"), str(state_path), str(delta_path)],
            capture_output=True, text=True, env=env, timeout=10,
        )
        assert result.returncode == 3, (
            f"a losing merger.main must exit 3, got {result.returncode}. "
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "LLM_MEMORY_WARN: narrative update already running for demoproj" in (
            result.stdout + result.stderr
        )
    finally:
        holder.wait(timeout=10)

    assert H.sha256_file(state_path) == sha_before, "losing run must not mutate state"
    assert state_path.stat().st_mtime_ns == mtime_before, "losing run must not touch the file"


def test_control_uncontended_merger_run_behaves_as_today(tmp_path, monkeypatch):
    """No lock contention: merger.main must merge normally, same as
    tests/test_merger.py already asserts unmodified. This must be GREEN on
    base and stay GREEN once locking lands."""
    memory_home = tmp_path / "memory-home"
    (memory_home / "projects").mkdir(parents=True)
    state_path, delta_path = _write_state_and_delta(memory_home)
    env = os.environ.copy()
    env["LLM_MEMORY_HOME"] = str(memory_home)

    result = subprocess.run(
        [sys.executable, str(REPO_DIR / "merger.py"), str(state_path), str(delta_path)],
        capture_output=True, text=True, env=env, timeout=10,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    state = json.loads(state_path.read_text())
    assert any(s["session_id"] == "lock-contend-sess" for s in state["sessions"])


def test_narrative_skill_documents_lock_acquisition():
    skill_text = (REPO_DIR / "skills" / "narrative" / "SKILL.md").read_text()
    assert "narrative_lock" in skill_text or "project_lock" in skill_text, (
        "skills/narrative/SKILL.md must pin the lock-acquisition step "
        "(D1: the manual /narrative path shares the same per-project lock)"
    )
