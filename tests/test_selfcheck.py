"""The SessionStart post-update self-check.

Why it exists, concretely. On 2026-08-17 the adapters/ package was added and
deployed. Every upgrade is run by the install.sh already on disk — the *old*
one — so the transition update installed the new `extract_conversation.py`
without the `adapters/` package it imports, then stamped VERSION, which made
re-running `--update` a no-op. Extraction was broken for about fifteen minutes
and nothing said so: `session_end.sh` writes its failure to a log nobody reads,
and a session that produces no conversation .md is simply absent from every
narrative afterwards.

The check is one import at session start. These tests are the trigger and the
control — a guard that cannot fail is not a guard, and this one nearly was: the
first version imported the modules from the *working directory* instead of the
installed lib, so it passed cleanly against a broken lib whenever the session
happened to start inside a checkout of this repository.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "hooks" / "session_start.sh"


@pytest.fixture
def installed_lib(tmp_path):
    """A sandbox HOME holding a complete installed lib."""
    lib = tmp_path / ".claude" / "memory" / "lib"
    lib.mkdir(parents=True)
    (tmp_path / ".claude" / "memory" / "config").mkdir(parents=True)
    # Opt out of the network auto-update; this test is about the local state.
    (tmp_path / ".claude" / "memory" / "config" / "no-auto-update").touch()

    for py in REPO.glob("*.py"):
        shutil.copy2(py, lib / py.name)
    shutil.copytree(REPO / "adapters", lib / "adapters")
    return tmp_path, lib


def _run_hook(home: Path, cwd: Path) -> str:
    result = subprocess.run(
        ["bash", str(HOOK)],
        env={"HOME": str(home), "PATH": "/usr/bin:/bin", "SOURCE": "startup"},
        cwd=str(cwd), capture_output=True, text=True, timeout=120,
    )
    return result.stdout + result.stderr


def test_complete_lib_is_silent(installed_lib, tmp_path):
    home, _ = installed_lib
    assert "LLM_MEMORY_BROKEN" not in _run_hook(home, tmp_path)


def test_missing_adapters_package_is_reported(installed_lib, tmp_path):
    """The incident, reproduced exactly."""
    home, lib = installed_lib
    shutil.rmtree(lib / "adapters")

    output = _run_hook(home, tmp_path)
    assert "LLM_MEMORY_BROKEN" in output
    assert "No module named 'adapters'" in output
    assert "install.sh --update --force" in output, "the message must say how to fix it"


def test_partially_installed_adapters_package_is_reported(installed_lib, tmp_path):
    """A half-copied package, not just a missing one."""
    home, lib = installed_lib
    (lib / "adapters" / "render.py").unlink()

    output = _run_hook(home, tmp_path)
    assert "LLM_MEMORY_BROKEN" in output
    assert "adapters.render" in output


def test_check_ignores_the_working_directory(installed_lib):
    """The bug that made the first version of this check useless.

    Run from a checkout of this repository, with the installed lib broken:
    `python3 -c` puts the working directory on sys.path first, so `import
    adapters` succeeded from the repo and the check passed while extraction
    was broken. It must report on the lib it was pointed at, not on whatever
    happens to be importable.
    """
    home, lib = installed_lib
    shutil.rmtree(lib / "adapters")

    assert "LLM_MEMORY_BROKEN" in _run_hook(home, REPO)


def test_check_recovers_after_repair(installed_lib, tmp_path):
    """Non-trigger control: the guard clears once the lib is whole again."""
    home, lib = installed_lib
    shutil.rmtree(lib / "adapters")
    assert "LLM_MEMORY_BROKEN" in _run_hook(home, tmp_path)

    shutil.copytree(REPO / "adapters", lib / "adapters")
    assert "LLM_MEMORY_BROKEN" not in _run_hook(home, tmp_path)


def test_missing_extract_conversation_is_reported(installed_lib, tmp_path):
    """The gap that made the guard skip itself.

    The check used to run only `if [ -f "$LIB_DIR/extract_conversation.py" ]`,
    so the one failure where *that* file did not copy — the same class of
    partial install as the incident — silently skipped the check entirely.
    """
    home, lib = installed_lib
    (lib / "extract_conversation.py").unlink()

    output = _run_hook(home, tmp_path)
    assert "LLM_MEMORY_BROKEN" in output
    assert "extract_conversation" in output


def test_missing_adapters_base_is_reported(installed_lib, tmp_path):
    """`import adapters` alone is not proof the package is whole.

    Today `adapters/__init__.py` imports `.base`, so its absence surfaces. That
    is a property of the current __init__, not a guarantee — an __init__ that
    stopped importing a submodule would leave a missing base.py undetected, and
    base.py carries the protocol every adapter is validated against. So it is
    imported by name.
    """
    home, lib = installed_lib
    (lib / "adapters" / "base.py").unlink()

    output = _run_hook(home, tmp_path)
    assert "LLM_MEMORY_BROKEN" in output
    assert "base" in output


def test_lib_with_version_but_no_python_is_reported(installed_lib, tmp_path):
    """A wiped-then-not-refilled lib is the worst case: it looks installed."""
    home, lib = installed_lib
    for py in lib.glob("*.py"):
        py.unlink()
    (lib / "VERSION").write_text("deadbeef\n")

    assert "LLM_MEMORY_BROKEN" in _run_hook(home, tmp_path)


def test_absent_lib_is_not_reported_as_broken(tmp_path):
    """A machine with no llm_memory installed is not a failure to shout about."""
    home = tmp_path / "home"
    (home / ".claude" / "memory" / "config").mkdir(parents=True)
    (home / ".claude" / "memory" / "config" / "no-auto-update").touch()

    assert "LLM_MEMORY_BROKEN" not in _run_hook(home, tmp_path)
