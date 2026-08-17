"""Tests for tools/memory_wrap — the generic non-Claude injection wrapper (S3, D10).

No real client is required: a fake client script stands in for "any CLI
taking a prompt argument" and just dumps its argv, one per line, so tests
can assert on exactly what memory_wrap handed it.
"""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
WRAP = REPO_ROOT / "tools" / "memory_wrap"

FAKE_CLIENT_SRC = """#!/bin/bash
for a in "$@"; do
    printf '%s\\x1f' "$a"
done
"""


@pytest.fixture
def fake_client(tmp_path):
    script = tmp_path / "fake_client.sh"
    script.write_text(FAKE_CLIENT_SRC)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


@pytest.fixture
def fake_home(tmp_path):
    home = tmp_path / "home"
    (home / ".claude" / "memory" / "projects").mkdir(parents=True)
    return home


def _write_project(home, project, *, journal="did the thing", closure="closed"):
    state = {
        "sessions": [
            {
                "status": "active",
                "session_id": "sess-1",
                "started": "2026-08-16T10:00:00Z",
                "ended": "2026-08-16T11:00:00Z",
                "closure_status": closure,
                "topic": "wrapper test",
                "journal": journal,
            }
        ]
    }
    path = home / ".claude" / "memory" / "projects" / f"{project}.json"
    path.write_text(json.dumps(state))
    return path


def _client_config(tmp_path, fake_client, *, prompt_mode="append", flag=None, extra_args=None):
    entry = {"command": str(fake_client), "args": extra_args or [], "prompt_mode": prompt_mode}
    if flag:
        entry["flag"] = flag
    config = {"fake": entry}
    path = tmp_path / "clients.json"
    path.write_text(json.dumps(config))
    return path


_REAL_INSTALLED_PYTHON = Path.home() / ".claude" / "memory" / "lib" / ".venv" / "bin" / "python3"


def _base_env(fake_home):
    env = dict(os.environ)
    env["HOME"] = str(fake_home)
    # memory_wrap_resume.py imports server.py, which imports the real `mcp`
    # SDK. Point at the installed lib venv (what memory_wrap uses by
    # default in production) rather than this repo's dev .venv, whose `mcp`
    # package is a different, incompatible library of the same name.
    if _REAL_INSTALLED_PYTHON.exists():
        env["MEMORY_WRAP_PYTHON"] = str(_REAL_INSTALLED_PYTHON)
    else:
        env["MEMORY_WRAP_PYTHON"] = sys.executable
    return env


def _run(project, fake_home, clients_json, extra_args=()):
    env = _base_env(fake_home)
    env["MEMORY_WRAP_CLIENTS"] = str(clients_json)
    if project is not None:
        env["MEMORY_WRAP_PROJECT"] = project
    result = subprocess.run(
        [str(WRAP), "fake", "what should I do next?", *extra_args],
        cwd=str(fake_home),  # not a git repo -> project falls back to cwd basename
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result


class TestAppendMode:
    def test_injection_prepended_and_prompt_preserved(self, tmp_path, fake_home, fake_client):
        _write_project(fake_home, "demo-project", journal="fixed the flaky test")
        clients_json = _client_config(tmp_path, fake_client, prompt_mode="append")

        result = _run("demo-project", fake_home, clients_json)

        assert result.returncode == 0, result.stderr
        args = result.stdout.split("\x1f")[:-1]
        final_prompt = args[-1]
        assert "=== MEMORY (resume: demo-project) ===" in final_prompt
        assert "fixed the flaky test" in final_prompt
        assert final_prompt.rstrip("\n").endswith("what should I do next?")
        assert final_prompt.index("=== MEMORY") < final_prompt.index("what should I do next?")

    def test_extra_args_pass_through_before_prompt(self, tmp_path, fake_home, fake_client):
        _write_project(fake_home, "demo-project")
        clients_json = _client_config(tmp_path, fake_client, prompt_mode="append")

        result = _run("demo-project", fake_home, clients_json, extra_args=["--model", "x"])

        assert result.returncode == 0, result.stderr
        args = result.stdout.split("\x1f")[:-1]
        assert args[0] == "--model"
        assert args[1] == "x"
        assert "what should I do next?" in args[-1]


class TestFlagMode:
    def test_prompt_follows_configured_flag(self, tmp_path, fake_home, fake_client):
        _write_project(fake_home, "demo-project", journal="shipped the fix")
        clients_json = _client_config(tmp_path, fake_client, prompt_mode="flag", flag="-p")

        result = _run("demo-project", fake_home, clients_json)

        assert result.returncode == 0, result.stderr
        args = result.stdout.split("\x1f")[:-1]
        assert "-p" in args
        prompt_arg = args[args.index("-p") + 1]
        assert "shipped the fix" in prompt_arg
        assert prompt_arg.rstrip("\n").endswith("what should I do next?")

    def test_flag_mode_without_flag_field_errors(self, tmp_path, fake_home, fake_client):
        clients_json = _client_config(tmp_path, fake_client, prompt_mode="flag", flag=None)

        result = _run("demo-project", fake_home, clients_json)

        assert result.returncode != 0
        assert "prompt_mode=flag" in result.stderr


class TestProjectResolution:
    def test_missing_project_reports_visibly_not_silently(self, tmp_path, fake_home, fake_client):
        clients_json = _client_config(tmp_path, fake_client, prompt_mode="append")

        result = _run("no-such-project", fake_home, clients_json)

        assert result.returncode == 0, result.stderr
        args = result.stdout.split("\x1f")[:-1]
        final_prompt = args[-1]
        assert "memory_wrap:" in final_prompt
        assert "no-such-project" in final_prompt
        # still runs the client, and still carries the original prompt
        assert "what should I do next?" in final_prompt

    def test_default_project_is_cwd_basename(self, tmp_path, fake_home, fake_client):
        # cwd for the subprocess is fake_home itself (see _run) -> basename "home"
        _write_project(fake_home, "home", journal="inferred from cwd")
        clients_json = _client_config(tmp_path, fake_client, prompt_mode="append")

        result = _run(None, fake_home, clients_json)

        assert result.returncode == 0, result.stderr
        args = result.stdout.split("\x1f")[:-1]
        assert "inferred from cwd" in args[-1]


class TestClientConfig:
    def test_unknown_client_key_errors(self, tmp_path, fake_home, fake_client):
        clients_json = _client_config(tmp_path, fake_client, prompt_mode="append")
        env = dict(os.environ)
        env["HOME"] = str(fake_home)
        env["MEMORY_WRAP_CLIENTS"] = str(clients_json)
        env["MEMORY_WRAP_PROJECT"] = "demo-project"

        result = subprocess.run(
            [str(WRAP), "not-a-real-client", "hello"],
            cwd=str(fake_home),
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )

        assert result.returncode != 0
        assert "unknown client" in result.stderr

    def test_too_few_args_is_usage_error(self, fake_home):
        env = dict(os.environ)
        env["HOME"] = str(fake_home)
        result = subprocess.run(
            [str(WRAP), "fake"],
            cwd=str(fake_home),
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 2
        assert "usage:" in result.stderr
