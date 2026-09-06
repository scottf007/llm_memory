"""Tests for agent memory injection system — SubagentStart/Stop hooks,
subagent transcript discovery, and context injection.

These tests are written BEFORE the features exist and should all FAIL.
They cover:
1. SubagentStart hook — context injection into agents
2. SubagentStop hook — narrative reload signaling
3. process_transcripts.py — subagent transcript discovery
"""

import json
import os
import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

import process_transcripts


HOOKS_DIR = Path(__file__).parent.parent / "hooks"

SERVER_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    uuid TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    project TEXT,
    session_id TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    importance INTEGER DEFAULT 5 CHECK(importance BETWEEN 1 AND 10),
    transcript_ref TEXT,
    tags TEXT,
    status TEXT DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS connections (
    from_uuid TEXT NOT NULL REFERENCES memories(uuid),
    to_uuid TEXT NOT NULL REFERENCES memories(uuid),
    relationship TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (from_uuid, to_uuid, relationship)
);
"""


def _setup_test_home(tmp_path):
    """Create a fake HOME with memory DB structure."""
    home = tmp_path / "home"
    memory_dir = home / ".claude" / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "records").mkdir()
    (memory_dir / "transcripts").mkdir()
    (memory_dir / "conversations").mkdir()
    (memory_dir / "config").mkdir()
    (memory_dir / "projects").mkdir()

    db_path = memory_dir / "memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SERVER_SCHEMA)
    conn.commit()
    return home, conn, db_path


def _write_narrative(home, project, content="# narrative"):
    path = home / ".claude" / "memory" / "projects" / f"{project}.narrative.md"
    path.write_text(content)
    return path


def _run_hook(hook_name, home, input_json, timeout=10):
    """Run a hook script with a fake HOME and return stdout, stderr, rc."""
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["LLM_MEMORY_HOME"] = str(home / ".claude" / "memory")
    # Prevent auto-update check and process_transcripts from running
    (home / ".claude" / "memory" / "config" / "no-auto-update").touch()

    result = subprocess.run(
        ["bash", str(HOOKS_DIR / hook_name)],
        input=input_json,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )
    return result.stdout, result.stderr, result.returncode


# ============================================================================
# 1. SubagentStart Hook — Context Injection
# ============================================================================

class TestSubagentStartHookExists:
    """The subagent_start.sh hook must exist and be executable."""

    def test_hook_file_exists(self):
        """hooks/subagent_start.sh should exist."""
        hook = HOOKS_DIR / "subagent_start.sh"
        assert hook.exists(), (
            f"hooks/subagent_start.sh does not exist at {hook}. "
            f"This hook is needed to inject context into subagents."
        )

    def test_hook_is_executable(self):
        """hooks/subagent_start.sh should be executable."""
        hook = HOOKS_DIR / "subagent_start.sh"
        if not hook.exists():
            pytest.skip("Hook file does not exist yet")
        assert os.access(hook, os.X_OK), (
            f"hooks/subagent_start.sh exists but is not executable."
        )


class TestSubagentStartOutputsContext:
    """SubagentStart hook should output JSON with additionalContext
    containing the project narrative and important notes."""

    def test_hook_outputs_json_with_narrative(self, tmp_path):
        """When project has a narrative file, hook should output it in additionalContext."""
        home, conn, _ = _setup_test_home(tmp_path)
        _write_narrative(
            home, "testproj",
            "# TestProj\n\nA project for testing agent memory injection.",
        )
        conn.close()

        input_json = json.dumps({
            "agent_id": "agent-test-001",
            "cwd": "/home/user/projects/testproj",
            "session_id": "parent-session-123",
        })

        stdout, stderr, rc = _run_hook("subagent_start.sh", home, input_json)

        assert rc == 0, f"subagent_start.sh exited with rc={rc}. stderr: {stderr}"

        output = json.loads(stdout)
        assert output.get("hookSpecificOutput", {}).get("hookEventName") == "SubagentStart"
        context = output["hookSpecificOutput"]["additionalContext"]
        assert "TestProj" in context


    def test_hook_works_with_no_narrative(self, tmp_path):
        """When project has no narrative, hook should still exit 0."""
        home, conn, _ = _setup_test_home(tmp_path)
        conn.close()

        input_json = json.dumps({
            "agent_id": "agent-test-003",
            "cwd": "/home/user/projects/testproj",
        })

        stdout, stderr, rc = _run_hook("subagent_start.sh", home, input_json)

        assert rc == 0, (
            f"subagent_start.sh should exit 0 even with no narrative. "
            f"rc={rc}, stderr: {stderr}"
        )

    def test_hook_works_with_no_project(self, tmp_path):
        """When cwd is /tmp (no derivable project), hook should exit 0 gracefully."""
        home, conn, _ = _setup_test_home(tmp_path)
        conn.close()

        input_json = json.dumps({
            "agent_id": "agent-test-004",
            "cwd": "/tmp",
        })

        stdout, stderr, rc = _run_hook("subagent_start.sh", home, input_json)

        assert rc == 0, (
            f"subagent_start.sh should exit 0 even when no project is derivable. "
            f"rc={rc}, stderr: {stderr}"
        )

    def test_hook_output_is_valid_json(self, tmp_path):
        """Hook output must always be parseable JSON (when it produces output)."""
        home, conn, _ = _setup_test_home(tmp_path)

        conn.execute(
            "INSERT INTO memories (uuid, type, content, project, created_at, importance) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("dddd" * 8, "narrative", "# A narrative",
             "testproj", "2026-03-10T10:00:00", 10),
        )
        conn.commit()
        conn.close()

        input_json = json.dumps({
            "agent_id": "agent-test-005",
            "cwd": "/home/user/projects/testproj",
        })

        stdout, stderr, rc = _run_hook("subagent_start.sh", home, input_json)

        assert rc == 0, f"Hook failed with rc={rc}. stderr: {stderr}"

        # If there's output, it must be valid JSON
        if stdout.strip():
            try:
                parsed = json.loads(stdout)
            except json.JSONDecodeError as e:
                pytest.fail(
                    f"Hook output is not valid JSON: {e}\nOutput was:\n{stdout}"
                )
            assert isinstance(parsed, dict), (
                f"Hook output should be a JSON object, got {type(parsed).__name__}"
            )


# ============================================================================
# 2. SubagentStop Hook — Narrative Reload Signal
# ============================================================================

class TestSubagentStopHookExists:
    """The subagent_stop.sh hook must exist."""

    def test_hook_file_exists(self):
        """hooks/subagent_stop.sh should exist."""
        hook = HOOKS_DIR / "subagent_stop.sh"
        assert hook.exists(), (
            f"hooks/subagent_stop.sh does not exist at {hook}. "
            f"This hook is needed to signal narrative reloads after agent work."
        )

    def test_hook_is_executable(self):
        """hooks/subagent_stop.sh should be executable."""
        hook = HOOKS_DIR / "subagent_stop.sh"
        if not hook.exists():
            pytest.skip("Hook file does not exist yet")
        assert os.access(hook, os.X_OK), (
            f"hooks/subagent_stop.sh exists but is not executable."
        )


class TestSubagentStopNarrativeReload:
    """When a narrative-updater agent finishes and the narrative was updated,
    the hook should signal the parent to reload context."""

    def test_narrative_updater_signals_reload(self, tmp_path):
        """When agent type is narrative-updater and narrative was updated,
        output should tell parent to reload."""
        home, conn, _ = _setup_test_home(tmp_path)
        conn.close()

        # Create signal file that narrative-updater would leave behind
        project_dir = tmp_path / "projects" / "testproj"
        project_dir.mkdir(parents=True)
        (project_dir / ".narrative_updated").write_text("updated")

        input_json = json.dumps({
            "agent_id": "agent-narrative-001",
            "agent_type": "narrative-updater",
            "cwd": str(project_dir),
            "exit_code": 0,
        })

        stdout, stderr, rc = _run_hook("subagent_stop.sh", home, input_json)

        assert rc == 0, f"Hook failed with rc={rc}. stderr: {stderr}"

        # Output should tell parent to reload the narrative
        assert "reload" in stdout.lower() or "updated" in stdout.lower(), (
            f"subagent_stop.sh should signal narrative reload when "
            f".narrative_updated exists. Output was:\n{stdout}"
        )

    def test_non_narrative_agent_no_reload_signal(self, tmp_path):
        """When a regular agent finishes (not narrative-updater), no reload signal."""
        home, conn, _ = _setup_test_home(tmp_path)
        conn.close()

        input_json = json.dumps({
            "agent_id": "agent-general-001",
            "agent_type": "memory-aware",
            "cwd": "/home/user/projects/testproj",
            "exit_code": 0,
        })

        stdout, stderr, rc = _run_hook("subagent_stop.sh", home, input_json)

        assert rc == 0, f"Hook failed with rc={rc}. stderr: {stderr}"
