"""Regression tests for the shared LLM_MEMORY_HOME resolver."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import apply_settings
import merger
from tools.memory_config import memory_root


def test_memory_root_unset_preserves_legacy_location(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_MEMORY_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert memory_root() == tmp_path / ".claude" / "memory"


def test_memory_root_set_uses_configured_location(tmp_path, monkeypatch):
    configured = tmp_path / "generic-store"
    monkeypatch.setenv("LLM_MEMORY_HOME", str(configured))

    assert memory_root() == configured


def test_merger_real_caller_resolves_config_at_call_time(tmp_path, monkeypatch):
    configured = tmp_path / "relocated"
    monkeypatch.setenv("LLM_MEMORY_HOME", str(configured))
    project_path = configured / "projects" / "demo.json"

    items_root, db_path, sandboxed = merger.resolve_paths(project_path)

    assert items_root == configured / "items"
    assert db_path == configured / "memory.db"
    assert sandboxed is False


def test_permission_expansion_uses_configured_memory_root(tmp_path, monkeypatch):
    configured = tmp_path / "generic-store"
    monkeypatch.setenv("LLM_MEMORY_HOME", str(configured))

    assert apply_settings._expand_home(
        "Write(~/.claude/memory/**)", str(tmp_path)
    ) == f"Write({configured}/**)"
    assert apply_settings._expand_home(
        "Read(~/.codex/**)", str(tmp_path)
    ) == f"Read({tmp_path}/.codex/**)"


def test_subagent_hook_reads_relocated_store(tmp_path):
    configured = tmp_path / "generic-store"
    projects = configured / "projects"
    projects.mkdir(parents=True)
    (configured / "memory.db").touch()
    (projects / "demo.narrative.md").write_text("relocated narrative")

    env = os.environ.copy()
    env["LLM_MEMORY_HOME"] = str(configured)
    result = subprocess.run(
        ["bash", str(Path(__file__).parent.parent / "hooks" / "subagent_start.sh")],
        input=json.dumps({"cwd": "/home/user/projects/demo"}),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "relocated narrative" in result.stdout


def test_installer_exports_root_and_deploys_resolver():
    install = (Path(__file__).parent.parent / "install.sh").read_text()

    assert 'MEMORY_DIR="${LLM_MEMORY_HOME:-$HOME/.claude/memory}"' in install
    assert 'export LLM_MEMORY_HOME="$MEMORY_DIR"' in install
    assert 'cp "$EXTRACTED/tools/"*.py "$LIB_DIR/tools/"' in install
    assert "fan_out_items(state, p.stem, memory_root() / 'items')" in install
