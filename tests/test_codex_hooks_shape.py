"""Codex hooks.json schema and real-client parser controls for T-F13.

Codex's documented file shape is {"hooks": {"SessionStart": [...]}}.  The
client only loads hooks during a session startup, not for ``mcp list`` or
``exec --help``.  The optional real-client check therefore starts ``codex
exec`` under a new unauthenticated HOME and stops it shortly afterward: parser
diagnostics happen before the expected authentication failure, so it cannot
produce a model response or consume a model turn.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLER = REPO_ROOT / "hooks" / "install_hooks.sh"
BASH = shutil.which("bash")
assert BASH


def _fake_codex(bin_dir: Path) -> None:
    bin_dir.mkdir(parents=True)
    command = bin_dir / "codex"
    command.write_text("#!/bin/sh\nexit 0\n")
    command.chmod(0o755)


def _install(home: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    (home / ".claude").mkdir(parents=True)
    fake_bin = tmp_path / "fake-bin"
    _fake_codex(fake_bin)
    env = os.environ.copy()
    env.update({
        "HOME": str(home),
        "LLM_MEMORY_INSTALLING": "1",
        "PATH": str(fake_bin) + os.pathsep + str(Path(sys.executable).parent) + os.pathsep + "/usr/bin:/bin",
    })
    return subprocess.run([BASH, str(INSTALLER)], text=True, capture_output=True, env=env, timeout=30)


def test_installer_writes_documented_three_level_shape_and_safe_session_end(tmp_path):
    home = tmp_path / "home"
    result = _install(home, tmp_path)
    assert result.returncode == 0, result.stderr

    data = json.loads((home / ".codex" / "hooks.json").read_text())
    assert set(data) == {"hooks"}
    assert set(data["hooks"]) == {"SessionStart", "SessionEnd"}
    start = data["hooks"]["SessionStart"][0]
    end = data["hooks"]["SessionEnd"][0]
    assert start["matcher"] == "startup|resume"
    assert start["hooks"][0]["timeout"] == 15
    # Design note §1 documents matcher as a filter string.  Omit it for
    # SessionEnd rather than relying on an undocumented empty-string meaning.
    assert "matcher" not in end
    # The real client clamps SessionEnd to three seconds; a sweep measured 0 s.
    assert end["hooks"][0]["timeout"] == 3


def test_old_top_level_events_migrate_without_clobbering_other_top_level_keys(tmp_path):
    home = tmp_path / "home"
    codex_dir = home / ".codex"
    codex_dir.mkdir(parents=True)
    old = {
        "description": "owner supplied description",
        "SessionStart": [{"matcher": "foreign", "hooks": [{"type": "command", "command": "/opt/start"}]}],
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "/opt/pre"}]}],
        "owner_metadata": {"keep": True},
    }
    (codex_dir / "hooks.json").write_text(json.dumps(old, indent=4) + "\n")

    result = _install(home, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "migrated legacy top-level Codex hook events" in result.stdout

    data = json.loads((codex_dir / "hooks.json").read_text())
    assert data["description"] == old["description"]
    assert data["owner_metadata"] == old["owner_metadata"]
    assert "SessionStart" not in data and "PreToolUse" not in data
    assert old["SessionStart"][0] in data["hooks"]["SessionStart"]
    assert data["hooks"]["PreToolUse"] == old["PreToolUse"]


@pytest.mark.skipif(shutil.which("codex") is None, reason="requires codex on PATH")
def test_real_codex_startup_rejects_old_shape_but_accepts_generated_file_without_hook_warnings(tmp_path):
    """Quote the parser trigger and control from a short, scratch HOME startup."""
    codex = shutil.which("codex")
    assert codex

    def startup(home: Path) -> str:
        env = os.environ.copy()
        env.pop("OPENAI_API_KEY", None)
        env.pop("CODEX_API_KEY", None)
        env["HOME"] = str(home)
        # timeout ends the expected unauthenticated startup after parser load.
        result = subprocess.run(
            ["timeout", "5s", codex, "exec", "--skip-git-repo-check", "parser schema control"],
            text=True,
            capture_output=True,
            env=env,
            timeout=10,
        )
        return result.stdout + result.stderr

    malformed_home = tmp_path / "malformed"
    (malformed_home / ".codex").mkdir(parents=True)
    (malformed_home / ".codex" / "hooks.json").write_text('{"SessionStart": []}\n')
    trigger = startup(malformed_home)
    assert re.search(r"failed to parse hooks config.*unknown field.*SessionStart", trigger, re.I | re.S), trigger

    valid_home = tmp_path / "valid"
    installed = _install(valid_home, tmp_path / "install")
    assert installed.returncode == 0, installed.stderr
    control = startup(valid_home)
    assert "failed to parse hooks config" not in control.lower(), control
    assert not re.search(r"warning:.*hooks\.json", control, re.I), control
