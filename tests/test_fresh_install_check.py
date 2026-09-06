"""Frozen tests for the fresh-install proof (docs/design/fresh-install-proof-2026-09-06.md).

Pins the `tools/fresh_install_check.sh` contract the design's seat table (row
`fresh-tests`) asks for: an ordered `--dry-run` check list (T1), a hermetic
run against a fake install root asserting each seeded broken state's failure
message plus the full success path (T2), and a CI workflow job that invokes
the script inside an `ubuntu:24.04` container (T3). A different vendor
(fresh-impl) writes the script and workflow job against these tests,
unmodified.

Contract this module pins for `tools/fresh_install_check.sh` (not specified
verbatim by the design doc, so fixed here by the test author):

  --dry-run   prints the six check ids below, one per line, in this order,
              and exits 0 without touching HOME or LLM_MEMORY_HOME.
  (no args)   reads HOME / LLM_MEMORY_HOME (same defaulting convention as
              install.sh: LLM_MEMORY_HOME defaults to $HOME/.claude/memory),
              runs every check, and for each prints exactly one line:
                "PASS <id>: <message>"  or  "FAIL <id>: <message>"
              If every check passed: exits 0 and prints a final line
                "SUMMARY: VERSION=<sha> TOOLS=<tool1,tool2,tool3,tool4>"
              If any check failed: exits non-zero, no SUMMARY line.

Scope note (test-author decision): the design's D1 also requires that a
missing `claude` binary makes the Claude MCP registration step degrade to a
manual one-liner "without failing the install" -- it says installer does
this already, not that `fresh_install_check.sh` gains a seventh check for
it, so it is not one of the six ids frozen here. That branch is fresh-impl
and fresh-judge's concern via the real container run, not this module.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytest.importorskip("mcp")

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECK_SCRIPT = REPO_ROOT / "tools" / "fresh_install_check.sh"
WORKFLOW_FILE = REPO_ROOT / ".github" / "workflows" / "test.yml"

CHECK_IDS = (
    "lib-version",
    "venv-mcp",
    "mcp-list-tools",
    "hooks-installed",
    "session-narrative",
    "transcript-ingest",
)

FOUR_TOOLS = ("memory_search", "project_lookup", "narrative_coverage", "resume")

PROJECT = "fresh-install-fixture"
PROJECT_CWD = f"/home/fixture/projects/{PROJECT}"

_COPY_IGNORE = shutil.ignore_patterns(
    ".git", ".venv", "tests", "docs", ".agent-messages", ".github", ".am-seat",
    ".claude", "node_modules", "__pycache__", "*.pyc", "process",
    ".pytest_cache", "tmp_before_rerun_*",
)


def _run(*args, env=None):
    return subprocess.run(
        ["bash", str(CHECK_SCRIPT), *args],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT), timeout=60,
    )


def _copy_lib_tree(dest: Path) -> None:
    """Real repo files, flat into `dest`, exactly as install.sh's own
    per-file `cp` puts an extracted tarball into $LIB_DIR -- this repo's
    root is already laid out the way the production lib/ is."""
    shutil.copytree(REPO_ROOT, dest, ignore=_COPY_IGNORE)


def _install_venv_shim(lib_dir: Path, *, with_mcp: bool) -> None:
    venv_bin = lib_dir / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    python3 = venv_bin / "python3"
    if with_mcp:
        python3.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    else:
        # -S skips site initialization entirely, so the mcp package (never
        # part of the stdlib) cannot be imported no matter how the caller
        # probes for it.
        python3.write_text(f'#!/bin/sh\nexec "{sys.executable}" -S "$@"\n')
    python3.chmod(python3.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _seed_transcript(fake_home: Path) -> str:
    session_id = "fixture-session-0001"
    project_dir = fake_home / ".claude" / "projects" / "fixture-encoded"
    project_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps({
            "type": "user", "cwd": PROJECT_CWD,
            "timestamp": "2026-09-06T00:00:00.000Z",
            "message": {"content": "hello from the fresh-install fixture"},
        }),
        json.dumps({
            "type": "assistant",
            "timestamp": "2026-09-06T00:00:01.000Z",
            "message": {"content": [{"type": "text", "text": "hi -- fixture reply"}]},
        }),
    ]
    (project_dir / f"{session_id}.jsonl").write_text("\n".join(lines) + "\n")
    return session_id


def _build_fake_install(
    tmp_path: Path, *, with_mcp: bool = True, with_hooks: bool = True,
) -> tuple[Path, Path, Path]:
    """A full, working install root: real repo files copied flat into lib/,
    a real Python wired in as the venv interpreter (via a shim -- no venv
    creation or pip install needed), hooks registered by the real
    install_hooks.sh, and a seeded project + transcript for the
    narrative/ingest checks. Callers break exactly one property for each
    seeded-broken-state test."""
    fake_home = tmp_path / "home"
    memory_dir = fake_home / ".claude" / "memory"
    lib_dir = memory_dir / "lib"
    fake_home.mkdir()
    _copy_lib_tree(lib_dir)
    _install_venv_shim(lib_dir, with_mcp=with_mcp)

    (lib_dir / "VERSION").write_text("d34db33fd34db33fd34db33fd34db33fd34db33f\n")

    (memory_dir / "config").mkdir(parents=True, exist_ok=True)
    # Must exist: without it a real session_start.sh run would try to hit
    # the GitHub API and spawn a background `install.sh --update`.
    (memory_dir / "config" / "no-auto-update").write_text("")
    (memory_dir / "memory.db").write_text("")

    projects_dir = memory_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    (projects_dir / f"{PROJECT}.narrative.md").write_text(
        "# Fixture narrative\n\nThis is the seeded narrative for the fresh-install check.\n"
    )

    (fake_home / ".claude").mkdir(exist_ok=True)
    if with_hooks:
        env = dict(os.environ, HOME=str(fake_home), LLM_MEMORY_INSTALLING="1")
        result = subprocess.run(
            ["bash", str(lib_dir / "hooks" / "install_hooks.sh")],
            capture_output=True, text=True, env=env, timeout=30,
        )
        assert result.returncode == 0, (
            f"fixture setup: install_hooks.sh failed: {result.stdout}\n{result.stderr}"
        )

    _seed_transcript(fake_home)

    return fake_home, memory_dir, lib_dir


def _env_for(fake_home: Path, memory_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["HOME"] = str(fake_home)
    env["LLM_MEMORY_HOME"] = str(memory_dir)
    return env


# ---------------------------------------------------------------------------
# T1 -- --dry-run prints the ordered check list
# ---------------------------------------------------------------------------

def test_dry_run_prints_ordered_check_list():
    result = _run("--dry-run")
    assert result.returncode == 0, result.stderr
    positions = []
    for check_id in CHECK_IDS:
        idx = result.stdout.find(check_id)
        assert idx != -1, f"--dry-run output is missing check id {check_id!r}:\n{result.stdout}"
        positions.append(idx)
    assert positions == sorted(positions), (
        f"--dry-run must print the six checks in the pinned order {CHECK_IDS}:\n{result.stdout}"
    )


def test_dry_run_requires_no_environment():
    """--dry-run must work with no install present at all -- it only
    announces what it will check, never touches LLM_MEMORY_HOME/HOME."""
    env = dict(os.environ)
    env.pop("LLM_MEMORY_HOME", None)
    env["HOME"] = "/nonexistent-fresh-install-check-home"
    result = subprocess.run(
        ["bash", str(CHECK_SCRIPT), "--dry-run"],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT), timeout=30,
    )
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# T2 -- hermetic run against a fake install root: seeded broken states and
# the success path
# ---------------------------------------------------------------------------

def test_missing_version_fails_lib_version_check(tmp_path):
    fake_home, memory_dir, lib_dir = _build_fake_install(tmp_path)
    (lib_dir / "VERSION").unlink()
    result = _run(env=_env_for(fake_home, memory_dir))
    assert result.returncode != 0
    fail_lines = [l for l in result.stdout.splitlines() if l.startswith("FAIL lib-version")]
    assert fail_lines, f"expected a FAIL lib-version line:\n{result.stdout}"
    assert "VERSION" in fail_lines[0]


def test_venv_without_mcp_fails_venv_mcp_check(tmp_path):
    fake_home, memory_dir, lib_dir = _build_fake_install(tmp_path, with_mcp=False)
    result = _run(env=_env_for(fake_home, memory_dir))
    assert result.returncode != 0
    fail_lines = [l for l in result.stdout.splitlines() if l.startswith("FAIL venv-mcp")]
    assert fail_lines, f"expected a FAIL venv-mcp line:\n{result.stdout}"
    assert "mcp" in fail_lines[0]


def test_missing_hooks_fails_hooks_installed_check(tmp_path):
    fake_home, memory_dir, lib_dir = _build_fake_install(tmp_path, with_hooks=False)
    result = _run(env=_env_for(fake_home, memory_dir))
    assert result.returncode != 0
    fail_lines = [l for l in result.stdout.splitlines() if l.startswith("FAIL hooks-installed")]
    assert fail_lines, f"expected a FAIL hooks-installed line:\n{result.stdout}"
    assert "hooks" in fail_lines[0].lower()


def test_full_fake_install_passes_every_check(tmp_path):
    fake_home, memory_dir, lib_dir = _build_fake_install(tmp_path)
    result = _run(env=_env_for(fake_home, memory_dir))
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    for check_id in CHECK_IDS:
        pass_lines = [l for l in result.stdout.splitlines() if l.startswith(f"PASS {check_id}")]
        assert pass_lines, f"expected a PASS {check_id} line:\n{result.stdout}"
    summary_lines = [l for l in result.stdout.splitlines() if l.startswith("SUMMARY:")]
    assert summary_lines, f"expected a SUMMARY line on full success:\n{result.stdout}"
    summary = summary_lines[-1]
    assert "VERSION=" in summary
    for tool in FOUR_TOOLS:
        assert tool in summary, f"SUMMARY line is missing tool {tool!r}: {summary}"


# ---------------------------------------------------------------------------
# T3 -- the CI workflow invokes the script inside an ubuntu:24.04 container
# ---------------------------------------------------------------------------

def test_workflow_has_fresh_install_job_on_ubuntu_2404():
    data = yaml.safe_load(WORKFLOW_FILE.read_text())
    jobs = data.get("jobs", {}) if isinstance(data, dict) else {}
    assert "fresh-install" in jobs, (
        f"{WORKFLOW_FILE} has no 'fresh-install' job; jobs present: {sorted(jobs)}"
    )
    job_text = json.dumps(jobs["fresh-install"])
    assert "ubuntu:24.04" in job_text, (
        f"fresh-install job does not pin ubuntu:24.04:\n{job_text}"
    )
    assert "fresh_install_check.sh" in job_text, (
        f"fresh-install job does not invoke tools/fresh_install_check.sh:\n{job_text}"
    )
