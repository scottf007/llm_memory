"""Parity coverage for the Codex SessionStart hook and probe follow-ups."""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS = REPO_ROOT / "hooks"
PROBE = REPO_ROOT / "tools" / "codex_injection_probe.sh"
BASH = shutil.which("bash")
assert BASH


def _write_conversation(root: Path, session_id: str, project: str) -> None:
    (root / "conversations").mkdir(parents=True, exist_ok=True)
    (root / "conversations" / f"{session_id}.md").write_text(
        f"---\nsession_id: {session_id}\nproject: {project}\n---\n\n=== user ===\nhello\n"
    )


def _home_and_memory(tmp_path, project="parity"):
    home = tmp_path / "home"
    memory = tmp_path / "memory"
    (home / ".claude").mkdir(parents=True)
    (memory / "config").mkdir(parents=True)
    (memory / "projects").mkdir()
    (memory / "memory.db").touch()
    (memory / "config" / "no-auto-update").touch()
    (memory / "projects" / f"{project}.narrative.md").write_text("PARITY NARRATIVE\n")
    return home, memory


def _run(hook: str, home: Path, memory: Path, project="parity"):
    env = os.environ.copy()
    env.update({
        "HOME": str(home),
        "LLM_MEMORY_HOME": str(memory),
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"),
    })
    return subprocess.run(
        [BASH, str(HOOKS / hook)],
        input=json.dumps({"source": "startup", "cwd": f"/tmp/projects/{project}", "session_id": "test"}),
        text=True,
        capture_output=True,
        env=env,
        timeout=60,
    )


def test_codex_and_claude_count_only_main_conversations(tmp_path):
    home, memory = _home_and_memory(tmp_path)
    (memory / "projects" / "parity.json").write_text(json.dumps({"sessions": []}))
    for session_id in ("codex-plain-1", "agent-helper-9", "audit-thing-3"):
        _write_conversation(memory, session_id, "parity")

    claude = _run("session_start.sh", home, memory)
    codex = _run("codex_session_start.sh", home, memory)

    assert claude.returncode == codex.returncode == 0
    expected = "AUTOMATIC TASK: 1 new session(s)"
    assert expected in claude.stdout
    assert expected in codex.stdout


def test_codex_surfaces_unparseable_ledger_count_failure(tmp_path):
    home, memory = _home_and_memory(tmp_path)
    (memory / "projects" / "parity.json").write_text("{ unparseable")
    _write_conversation(memory, "codex-plain-1", "parity")

    claude = _run("session_start.sh", home, memory)
    codex = _run("codex_session_start.sh", home, memory)

    for result in (claude, codex):
        assert result.returncode == 0
        assert "LLM_MEMORY_WARN: new-session count unavailable" in result.stdout
        assert "AUTOMATIC TASK:" not in result.stdout


def test_codex_plain_single_conversation_still_requests_narrative(tmp_path):
    home, memory = _home_and_memory(tmp_path)
    (memory / "projects" / "parity.json").write_text(json.dumps({"sessions": []}))
    _write_conversation(memory, "codex-plain-1", "parity")

    result = _run("codex_session_start.sh", home, memory)

    assert result.returncode == 0
    assert "PARITY NARRATIVE" in result.stdout
    assert "AUTOMATIC TASK: 1 new session(s)" in result.stdout


def test_codex_installer_preserves_four_space_format_and_final_newline(tmp_path):
    home, _memory = _home_and_memory(tmp_path)
    codex_dir = home / ".codex"
    codex_dir.mkdir()
    foreign = '''{
    "hooks": {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [
                    {"type": "command", "command": "/opt/foreign.sh"}
                ]
            }
        ],
        "SessionStart": [
            {"matcher": "foreign", "hooks": [{"type": "command", "command": "/opt/foreign-start.sh"}]}
        ]
    }
}
'''
    hooks_file = codex_dir / "hooks.json"
    hooks_file.write_text(foreign)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_codex = bin_dir / "codex"
    fake_codex.write_text("#!/bin/sh\nexit 0\n")
    fake_codex.chmod(0o755)
    env = os.environ.copy()
    env.update({
        "HOME": str(home),
        "LLM_MEMORY_INSTALLING": "1",
        "PATH": str(bin_dir) + os.pathsep + str(Path(sys.executable).parent) + os.pathsep + "/usr/bin:/bin",
    })

    result = subprocess.run([BASH, str(HOOKS / "install_hooks.sh")], text=True, capture_output=True, env=env, timeout=30)

    rendered = hooks_file.read_text()
    assert result.returncode == 0, result.stderr
    assert rendered.endswith("\n")
    assert '        "PreToolUse": [' in rendered
    assert '                    {"type": "command", "command": "/opt/foreign.sh"}' in rendered
    assert '            {"matcher": "foreign", "hooks": [{"type": "command", "command": "/opt/foreign-start.sh"}]}' in rendered
    assert json.loads(rendered)["hooks"]["SessionStart"][-1]["matcher"] == "startup|resume"


def test_probe_dry_run_has_isolated_rows_mcp_setup_and_real_wrapper_command(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = subprocess.run([BASH, str(PROBE), "--dry-run"], text=True, capture_output=True, env={"PATH": str(empty)}, timeout=30)

    assert result.returncode == 0, result.stderr
    stdout = [line for line in result.stdout.splitlines() if line]
    assert stdout[3].startswith("tools/memory_wrap codex ")
    paths = re.search(r"scratch project paths: (.+)", result.stderr)
    assert paths, result.stderr
    assert len(paths.group(1).split()) == 3
    assert len(set(paths.group(1).split())) == 3
    assert result.stderr.count("codex mcp add llm_memory --") == 2
