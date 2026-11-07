"""U1a re-land: no SessionStart hook runs the transcript sweep in the foreground.

On 8 Nov 2026, 94f0968 went live and routed agent-messaging startups through
the inline `process_transcripts.py` sweep.  archive_transcript re-copies every
transcript whose source has grown, so every live seat transcript was copied
and re-extracted on every startup.  One run took 359 s and read 6.5 GB,
against a 15 s hook timeout.  The sweep still matters: it recovers sessions
whose SessionEnd never ran, and transcripts synced from other machines.  So
it runs detached, nice'd and single-flight, never inline.

The hook tests run the real hooks from a copy of the repo (every top-level
entry symlinked) in which process_transcripts.py is a stub.  The stub records
its pid, niceness and argv, then sleeps.  The single-flight tests drive the
real process_transcripts.run_startup_sweep from separate processes.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.test_hooks import (
    REPO_DIR,
    _register_session,
    _setup_test_home,
    _write_narrative,
    _write_project_state,
)

STUB = '''\
import os, sys, time
from pathlib import Path

def work():
    with open(os.environ["SWEEP_STUB_LOG"], "a") as fh:
        fh.write(f"{os.getpid()} nice={os.nice(0)} argv={' '.join(sys.argv[1:])}\\n")
    time.sleep(float(os.environ.get("SWEEP_STUB_SLEEP", "30")))
    return 0

# Honour the real CLI contract: --startup-sweep wraps the work in the real
# single-flight guard; without it (trees before 2696f5e, whose shell script
# held the lock) the entry just works.
if "--startup-sweep" in sys.argv:
    import importlib.util
    spec = importlib.util.spec_from_file_location("pt_real", os.environ["SWEEP_REAL_PT"])
    pt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pt)
    sys.exit(pt.run_startup_sweep(work, Path(os.environ["LLM_MEMORY_HOME"]) / "runtime"))
work()
'''


def _stub_lib(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    for entry in REPO_DIR.iterdir():
        if entry.name in ("process_transcripts.py", ".git"):
            continue
        (lib / entry.name).symlink_to(entry)
    (lib / "process_transcripts.py").write_text(STUB)
    return lib


def _home(tmp_path):
    home, conn, _ = _setup_test_home(tmp_path)
    conn.close()
    _write_narrative(home, "sweepproj", "# sweepproj narrative")
    _write_project_state(home, "sweepproj", merged_session_ids=[])
    _register_session(home, "s1", "sweepproj")
    (home / ".llm-memory" / "config" / "no-auto-update").touch()
    return home


def _run(lib, home, log, source="startup", hook="session_start.sh", extra=None):
    env = os.environ.copy()
    env.update({
        "HOME": str(home),
        "LLM_MEMORY_HOME": str(home / ".llm-memory"),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "SWEEP_STUB_LOG": str(log),
        "SWEEP_REAL_PT": str(REPO_DIR / "process_transcripts.py"),
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
    })
    env.update(extra or {})
    t0 = time.monotonic()
    result = subprocess.run(
        ["bash", str(lib / "hooks" / hook)],
        input=json.dumps({"source": source, "trigger": source,
                          "cwd": "/home/user/projects/sweepproj", "session_id": "sweep-sess"}),
        capture_output=True, text=True, env=env, timeout=60,
    )
    return result, time.monotonic() - t0


def _stub_lines(log, want, wait=10.0):
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        lines = log.read_text().splitlines() if log.exists() else []
        if len(lines) >= want:
            return lines
        time.sleep(0.1)
    return log.read_text().splitlines() if log.exists() else []


def _kill_tagged(tag):
    """SIGKILL every process whose environment carries HOME=<tag>: the hook's
    detached sweeps, stubs and lock shims for one test."""
    needle = f"HOME={tag}".encode() + b"\0"
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            if needle in (proc / "environ").read_bytes():
                os.kill(int(proc.name), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass


@pytest.fixture
def stub_env(tmp_path):
    lib = _stub_lib(tmp_path)
    home = _home(tmp_path)
    log = tmp_path / "stub.log"
    yield lib, home, log
    # The stub sleeps in a detached session; never leave it behind.
    _kill_tagged(str(home))


def test_trigger_startup_does_not_wait_for_the_sweep(stub_env):
    lib, home, log = stub_env
    result, wall = _run(lib, home, log)
    assert result.returncode == 0, result.stderr
    assert wall < 2.0, f"SessionStart waited {wall:.1f}s for the transcript sweep"
    assert "=== LOADED MEMORIES" in result.stdout, result.stdout


def test_control_the_sweep_still_runs_detached_and_nice(stub_env):
    """The recovery role survives: the sweep is launched, in the background,
    at the lowest CPU priority."""
    lib, home, log = stub_env
    _run(lib, home, log)
    lines = _stub_lines(log, 1)
    assert len(lines) == 1, f"the background sweep never ran: {lines}"
    assert "nice=19" in lines[0], lines[0]
    assert "--startup-sweep" in lines[0] and "--quiet" in lines[0], lines[0]


def test_compact_and_codex_start_no_sweep(stub_env):
    lib, home, log = stub_env
    for source, hook in (("compact", "session_start.sh"), ("startup", "codex_session_start.sh")):
        result, wall = _run(lib, home, log, source=source, hook=hook)
        assert result.returncode == 0 and wall < 2.0, (source, hook, wall)
    time.sleep(1.0)
    assert not log.exists(), log.read_text()


# --- single-flight and rate limit: process_transcripts.run_startup_sweep -----
#
# One kernel lock (fcntl.flock) held by the process doing the work.  It
# replaced a shell fallback lock that was broken by age (llm-u1a-judge2 F2)
# and then raced when two processes broke the same stale lock
# (llm-u1a-judge3).

DRIVER = """
import os, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import process_transcripts as pt
def sweep():
    with open(os.environ["PROBE_LOG"], "a") as f:
        f.write(str(os.getpid()) + "\\n")
    time.sleep(float(os.environ.get("PROBE_SLEEP", "20")))
    return 0
sys.exit(pt.run_startup_sweep(sweep, Path(sys.argv[1])))
"""


def _driver_env(tmp_path, log, sleep):
    env = os.environ.copy()
    env.update(LLM_MEMORY_HOME=str(tmp_path / "mem"), PROBE_LOG=str(log),
               PROBE_SLEEP=str(sleep))
    return env


def _start_driver(tmp_path, log, sleep):
    runtime = tmp_path / "mem" / "runtime"
    return subprocess.Popen([sys.executable, "-c", DRIVER, str(runtime), str(REPO_DIR)],
                            env=_driver_env(tmp_path, log, sleep), start_new_session=True)


def _killpg_all(procs):
    for proc in procs:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)


def _age(paths, seconds):
    t = time.time() - seconds
    for p in paths:
        os.utime(p, (t, t))


def test_lock_held_by_a_live_sweep_older_than_an_hour_is_not_stolen(tmp_path):
    """Structure ported from llm-u1a-judge2 test_mkdir_live_lock_older_than_hour_is_not_stolen."""
    log = tmp_path / "runs.log"
    procs = []
    try:
        procs.append(_start_driver(tmp_path, log, 20))
        assert len(_stub_lines(log, 1, wait=10)) == 1
        artifacts = list((tmp_path / "mem" / "runtime").glob("startup-sweep*"))
        assert artifacts
        _age(artifacts, 7200)
        second = _start_driver(tmp_path, log, 0)
        procs.append(second)
        assert second.wait(timeout=15) == 0
        lines = log.read_text().splitlines()
        assert len(lines) == 1, f"live sweep lock stolen; concurrent sweeps pids={lines}"
    finally:
        _killpg_all(procs)


def test_sigkilled_holder_releases_the_lock(tmp_path):
    """The kernel releases the lock on any exit; no stale state to break."""
    log = tmp_path / "runs.log"
    procs = []
    try:
        first = _start_driver(tmp_path, log, 20)
        procs.append(first)
        assert len(_stub_lines(log, 1, wait=10)) == 1
        os.killpg(first.pid, signal.SIGKILL)
        first.wait(timeout=5)
        second = _start_driver(tmp_path, log, 0)
        procs.append(second)
        assert second.wait(timeout=15) == 0
        assert len(log.read_text().splitlines()) == 2, "a dead holder's lock blocked the sweep"
    finally:
        _killpg_all(procs)


def test_simultaneous_starts_run_one_sweep(tmp_path):
    log = tmp_path / "runs.log"
    procs = [_start_driver(tmp_path, log, 3) for _ in range(6)]
    try:
        for proc in procs:
            assert proc.wait(timeout=30) == 0
        lines = log.read_text().splitlines()
        assert len(lines) == 1, f"sweeps stacked: {lines}"
    finally:
        _killpg_all(procs)


def test_completed_sweep_is_not_repeated_within_ten_minutes(tmp_path):
    log = tmp_path / "runs.log"
    runtime = tmp_path / "mem" / "runtime"
    for expect in (1, 1):
        proc = _start_driver(tmp_path, log, 0)
        assert proc.wait(timeout=15) == 0
        assert len(log.read_text().splitlines()) == expect
    _age([runtime / "startup-sweep.done"], 11 * 60)
    proc = _start_driver(tmp_path, log, 0)
    assert proc.wait(timeout=15) == 0
    assert len(log.read_text().splitlines()) == 2


def test_startup_sweep_rejects_selectors():
    result = subprocess.run(
        [sys.executable, str(REPO_DIR / "process_transcripts.py"), "--startup-sweep",
         "--client", "claude"], capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert "takes no other selector" in result.stderr, result.stderr


# --- llm-u1a-judge3 reproductions (verdict 4d4b7bc9), at hook level ---------
#
# Ported from test_two_stale_breakers, test_timezone_holder_identity and
# test_killed_shell_live_child.  They run through session_start.sh, which
# both trees have, with flock(1) removed from PATH.  On 44a8489 that drove
# the hand-rolled shell fallback lock, and each of these fails there.  On the
# kernel-lock tree PATH does not matter, and each passes.


def _no_flock_bin(tmp_path, pause_dir):
    bindir = tmp_path / "noflock-bin"
    bindir.mkdir()
    seen = set()
    for d in [str(Path(sys.executable).parent)] + os.environ.get("PATH", "").split(os.pathsep):
        if not d or not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            if name in ("flock", "rm") or name in seen:
                continue
            seen.add(name)
            (bindir / name).symlink_to(os.path.join(d, name))
    # rm shim: under PAUSE_RM, an rm of the sweep lock waits for a release file.
    real_rm = shutil.which("rm")
    (bindir / "rm").write_text(
        "#!" + sys.executable + "\n"
        "import os, sys, time\n"
        "from pathlib import Path\n"
        "if os.environ.get('PAUSE_RM') and any('startup-sweep.lock' in a for a in sys.argv[1:]):\n"
        f"    Path({str(pause_dir / 'rm-ready')!r}).touch()\n"
        f"    while not Path({str(pause_dir / 'rm-release')!r}).exists(): time.sleep(.01)\n"
        f"os.execv({real_rm!r}, [{real_rm!r}] + sys.argv[1:])\n")
    (bindir / "rm").chmod(0o755)
    return str(bindir)


def _wait_for(fn, timeout=10.0):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if fn():
            return True
        time.sleep(0.02)
    return fn()


def _lines(log):
    return log.read_text().splitlines() if log.exists() else []


@pytest.fixture
def judge3_env(stub_env, tmp_path):
    lib, home, log = stub_env
    path = _no_flock_bin(tmp_path, tmp_path)
    yield lib, home, log, path, tmp_path
    (tmp_path / "rm-release").touch()


def test_two_stale_breakers_do_not_run_two_sweeps(judge3_env):
    lib, home, log, path, tmp = judge3_env
    runtime = home / ".llm-memory" / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "startup-sweep.lock.held").symlink_to("99999999|dead")
    _run(lib, home, log, extra={"PATH": path, "PAUSE_RM": "1"})
    _wait_for(lambda: (tmp / "rm-ready").exists() or len(_lines(log)) >= 1, timeout=5)
    _run(lib, home, log, extra={"PATH": path})
    assert _wait_for(lambda: len(_lines(log)) >= 1)
    (tmp / "rm-release").touch()
    time.sleep(1.0)
    assert len(_lines(log)) == 1, f"two stale breakers launched concurrent sweeps: {_lines(log)}"


def test_timezone_does_not_change_the_holder_identity(judge3_env):
    lib, home, log, path, _ = judge3_env
    _run(lib, home, log, extra={"PATH": path, "TZ": "UTC"})
    assert _wait_for(lambda: len(_lines(log)) == 1)
    _run(lib, home, log, extra={"PATH": path, "TZ": "EST5EDT"})
    time.sleep(1.0)
    assert len(_lines(log)) == 1, f"TZ changed identity of live holder: {_lines(log)}"


def test_killed_launcher_with_live_worker_keeps_the_lock(judge3_env):
    """Kill everything in the sweep's session except the process doing the
    work.  The worker still runs, so no second sweep may start beside it."""
    lib, home, log, path, _ = judge3_env
    _run(lib, home, log, extra={"PATH": path})
    assert _wait_for(lambda: len(_lines(log)) == 1)
    worker = int(_lines(log)[0].split()[0])
    sid = os.getsid(worker)
    for proc in Path("/proc").iterdir():
        if proc.name.isdigit() and int(proc.name) != worker:
            try:
                if os.getsid(int(proc.name)) == sid:
                    os.kill(int(proc.name), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    time.sleep(0.3)
    os.kill(worker, 0)  # the worker survived
    _run(lib, home, log, extra={"PATH": path})
    time.sleep(1.0)
    assert len(_lines(log)) == 1, f"orphan sweep overlaps replacement: {_lines(log)}"
