"""Regression tests for the shared LLM_MEMORY_HOME resolver."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import apply_settings
import merger
import renderer
import setup_syncthing
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


def test_merger_helper_defaults_resolve_config_at_call_time(tmp_path, monkeypatch):
    configured = tmp_path / "relocated"
    monkeypatch.setenv("LLM_MEMORY_HOME", str(configured))
    incoming_dir = configured / "items" / "demo" / "learnings"
    incoming_dir.mkdir(parents=True)
    (incoming_dir / "lrn-feed0001.json").write_text(json.dumps({
        "id": "lrn-feed0001",
        "kind": "learnings",
        "project": "demo",
        "text": "read from the relocated inbox",
        "status": "active",
        "last_touched_at": "2026-01-01T00:00:00Z",
    }))
    state = {
        "project": "demo",
        "decisions": [],
        "goals": [],
        "suggestions": [],
        "learnings": [],
        "done": [],
        "sessions": [],
    }

    assert merger.inbox_merge(state, "demo") == 1
    assert state["learnings"][0]["id"] == "lrn-feed0001"

    state["decisions"].append({
        "id": "dec-feed0001",
        "text": "write to the relocated item tree",
        "status": "active",
    })
    assert merger.fan_out_items(state, "demo") == 2
    fanned = configured / "items" / "demo" / "decisions" / "dec-feed0001.json"
    assert json.loads(fanned.read_text())["project"] == "demo"


def test_renderer_drill_down_uses_configured_memory_root(tmp_path, monkeypatch):
    configured = tmp_path / "relocated"
    monkeypatch.setenv("LLM_MEMORY_HOME", str(configured))
    state = {
        "project": "demo",
        "sessions": [
            {
                "session_id": f"session-{index}",
                "started": f"2026-01-{index + 1:02d}T00:00:00Z",
                "topic": "relocation test",
                "status": "active",
            }
            for index in range(11)
        ],
    }

    rendered = renderer._render_source_transcripts(state)

    assert str(configured / "projects" / "demo.json") in rendered
    assert "~/.claude/memory/projects/demo.json" not in rendered


def test_syncthing_second_device_guidance_uses_configured_root(
    tmp_path, monkeypatch, capsys
):
    configured = tmp_path / "relocated"
    config_path = tmp_path / "config.xml"
    config_path.touch()
    monkeypatch.setattr(setup_syncthing, "MEMORY_DIR", configured)
    monkeypatch.setattr(setup_syncthing, "find_syncthing_config", lambda: config_path)
    monkeypatch.setattr(
        setup_syncthing, "parse_config", lambda _path: ("127.0.0.1:8384", "key")
    )
    monkeypatch.setattr(setup_syncthing, "folder_exists_in_xml", lambda _path: False)
    monkeypatch.setattr(setup_syncthing, "get_folder_path", lambda: str(configured))
    monkeypatch.setattr(setup_syncthing, "get_device_id", lambda *_args: "device-id")
    monkeypatch.setattr(setup_syncthing, "get_other_devices", lambda *_args: [])
    monkeypatch.setattr(setup_syncthing, "add_folder", lambda *_args, **_kwargs: True)
    monkeypatch.setattr("sys.argv", ["setup_syncthing.py", "--dry-run"])

    setup_syncthing.main()

    output = capsys.readouterr().out
    assert f"point it to {configured}/" in output
    assert "point it to ~/.claude/memory/" not in output


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
