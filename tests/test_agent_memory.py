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
    tags TEXT
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
    (memory_dir / "config").mkdir()

    db_path = memory_dir / "memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SERVER_SCHEMA)
    conn.commit()
    return home, conn, db_path


def _run_hook(hook_name, home, input_json, timeout=10):
    """Run a hook script with a fake HOME and return stdout, stderr, rc."""
    env = os.environ.copy()
    env["HOME"] = str(home)
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
        """When project has a narrative, hook should output it in additionalContext."""
        home, conn, _ = _setup_test_home(tmp_path)

        # Insert a narrative for testproj
        conn.execute(
            "INSERT INTO memories (uuid, type, content, project, created_at, importance) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("aaaa" * 8, "narrative",
             "# TestProj\n\nA project for testing agent memory injection.",
             "testproj", "2026-03-10T10:00:00", 10),
        )
        conn.commit()
        conn.close()

        input_json = json.dumps({
            "agent_id": "agent-test-001",
            "cwd": "/home/scott/projects/testproj",
            "session_id": "parent-session-123",
        })

        stdout, stderr, rc = _run_hook("subagent_start.sh", home, input_json)

        assert rc == 0, (
            f"subagent_start.sh exited with rc={rc}. stderr: {stderr}"
        )

        # Output should be valid JSON with hookSpecificOutput
        output = json.loads(stdout)
        assert "hookSpecificOutput" in output, (
            f"Hook output missing 'hookSpecificOutput'. Got: {stdout}"
        )

        hook_output = output["hookSpecificOutput"]
        assert hook_output.get("hookEventName") == "SubagentStart", (
            f"hookEventName should be 'SubagentStart', got: {hook_output.get('hookEventName')}"
        )
        assert "additionalContext" in hook_output, (
            f"hookSpecificOutput missing 'additionalContext'. Got: {hook_output}"
        )

        context = hook_output["additionalContext"]
        assert "TestProj" in context, (
            f"additionalContext should contain the narrative content. Got: {context}"
        )

    def test_hook_includes_important_notes(self, tmp_path):
        """additionalContext should include notes with importance >= 7."""
        home, conn, _ = _setup_test_home(tmp_path)

        # Insert narrative
        conn.execute(
            "INSERT INTO memories (uuid, type, content, project, created_at, importance) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("aaaa" * 8, "narrative", "# TestProj narrative",
             "testproj", "2026-03-10T10:00:00", 10),
        )
        # Insert a high-importance note
        conn.execute(
            "INSERT INTO memories (uuid, type, content, project, importance, tags) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("bbbb" * 8, "note",
             "Always run migrations before deploying",
             "testproj", 8, "correction"),
        )
        # Insert a low-importance note (should NOT appear)
        conn.execute(
            "INSERT INTO memories (uuid, type, content, project, importance, tags) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("cccc" * 8, "note",
             "Minor style preference for tabs",
             "testproj", 3, "preference"),
        )
        conn.commit()
        conn.close()

        input_json = json.dumps({
            "agent_id": "agent-test-002",
            "cwd": "/home/scott/projects/testproj",
        })

        stdout, stderr, rc = _run_hook("subagent_start.sh", home, input_json)

        assert rc == 0, f"Hook failed with rc={rc}. stderr: {stderr}"

        output = json.loads(stdout)
        context = output["hookSpecificOutput"]["additionalContext"]

        assert "migrations" in context.lower(), (
            f"Important note (importance=8) about migrations not found in context. "
            f"Got: {context}"
        )
        assert "tabs" not in context.lower(), (
            f"Low-importance note (importance=3) about tabs should NOT be in context. "
            f"Got: {context}"
        )

    def test_hook_works_with_no_narrative(self, tmp_path):
        """When project has no narrative, hook should still exit 0."""
        home, conn, _ = _setup_test_home(tmp_path)
        conn.close()

        input_json = json.dumps({
            "agent_id": "agent-test-003",
            "cwd": "/home/scott/projects/testproj",
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
            "cwd": "/home/scott/projects/testproj",
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
            "cwd": "/home/scott/projects/testproj",
            "exit_code": 0,
        })

        stdout, stderr, rc = _run_hook("subagent_stop.sh", home, input_json)

        assert rc == 0, f"Hook failed with rc={rc}. stderr: {stderr}"


# ============================================================================
# 3. process_transcripts.py — Subagent Transcript Discovery
# ============================================================================

class TestSubagentTranscriptDiscovery:
    """process_transcripts.find_transcripts should discover subagent transcripts
    stored in projects/{project}/{session}/subagents/*.jsonl directories."""

    def test_find_transcripts_discovers_subagent_transcripts(self, tmp_path):
        """Subagent transcripts in subagents/ directories should be found."""
        projects_dir = tmp_path / "projects"
        # Main session transcript
        proj = projects_dir / "-home-user-projects-myproj"
        proj.mkdir(parents=True)
        (proj / "main-session.jsonl").write_text(
            '{"type":"user","message":{"content":"hi"}}\n'
        )

        # Subagent transcript nested under a session directory
        subagent_dir = proj / "main-session" / "subagents"
        subagent_dir.mkdir(parents=True)
        (subagent_dir / "agent-abc123.jsonl").write_text(
            '{"type":"user","message":{"content":"agent work"}}\n'
        )

        archive_dir = tmp_path / "archive"
        archive_dir.mkdir()

        with patch.object(process_transcripts, "PROJECTS_DIR", projects_dir), \
             patch.object(process_transcripts, "ARCHIVE_DIR", archive_dir):
            results = process_transcripts.find_transcripts()
            session_ids = [r[1] for r in results]

            assert "main-session" in session_ids, (
                f"Main session transcript not found. Got: {session_ids}"
            )
            assert "agent-abc123" in session_ids, (
                f"Subagent transcript 'agent-abc123' not found by find_transcripts. "
                f"It should scan subagents/ directories. Got: {session_ids}"
            )

    def test_subagent_transcripts_no_duplicates(self, tmp_path):
        """If a subagent transcript exists in subagents/ dir AND archive, include once.
        This test requires find_transcripts to scan subagents/ dirs first."""
        projects_dir = tmp_path / "projects"
        proj = projects_dir / "-home-user-projects-myproj"
        # Only put it in the subagents dir (NOT in archive)
        subagent_dir = proj / "sess-001" / "subagents"
        subagent_dir.mkdir(parents=True)
        (subagent_dir / "agent-only-sub.jsonl").write_text(
            '{"type":"user","message":{"content":"agent work"}}\n'
        )

        archive_dir = tmp_path / "archive"
        archive_dir.mkdir()

        with patch.object(process_transcripts, "PROJECTS_DIR", projects_dir), \
             patch.object(process_transcripts, "ARCHIVE_DIR", archive_dir):
            results = process_transcripts.find_transcripts()
            session_ids = [r[1] for r in results]
            # This file ONLY exists in subagents/ dir — find_transcripts must scan there
            assert "agent-only-sub" in session_ids, (
                f"Subagent transcript 'agent-only-sub' only exists in subagents/ dir "
                f"but was not found. find_transcripts must scan subagents/ dirs. "
                f"Got: {session_ids}"
            )


class TestSubagentSessionLogs:
    """Subagent transcripts should produce session_log entries
    tagged as agent work and referencing the parent session."""

    def test_subagent_transcript_extracts_parent_session(self, tmp_path):
        """extract_session_data should extract parentSessionId from subagent transcripts."""
        transcript = tmp_path / "agent-xyz.jsonl"
        entries = [
            {"type": "user", "timestamp": "2026-03-10T10:00:00Z",
             "sessionId": "agent-xyz",
             "cwd": "/home/scott/projects/testproj",
             "parentSessionId": "parent-sess-abc",
             "message": {"content": "Update the narrative for testproj"}},
            {"type": "assistant", "timestamp": "2026-03-10T10:01:00Z",
             "message": {"content": [{"type": "text",
                "text": "I'll read the transcripts and update the narrative."}]}},
        ]
        with open(transcript, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

        data = process_transcripts.extract_session_data(transcript)

        assert data["session_id"] == "agent-xyz"
        assert data["turn_count"] >= 1
        # extract_session_data should extract the parent session ID
        assert "parent_session_id" in data, (
            f"extract_session_data should return 'parent_session_id' for subagent "
            f"transcripts that contain parentSessionId. Got keys: {list(data.keys())}"
        )
        assert data["parent_session_id"] == "parent-sess-abc", (
            f"parent_session_id should be 'parent-sess-abc', "
            f"got '{data.get('parent_session_id')}'"
        )

    def test_subagent_session_log_tagged_as_agent(self, tmp_path,
                                                   db_conn, db_path,
                                                   tmp_memory_dir):
        """Session logs from subagent transcripts should be tagged as agent work."""
        # Create and process a subagent transcript
        transcript = tmp_path / "agent-tagged.jsonl"
        entries = [
            {"type": "user", "timestamp": "2026-03-10T10:00:00Z",
             "sessionId": "agent-tagged",
             "cwd": "/home/scott/projects/testproj",
             "parentSessionId": "parent-sess-001",
             "message": {"content": "Do agent work"}},
            {"type": "assistant", "timestamp": "2026-03-10T10:01:00Z",
             "message": {"content": [{"type": "text",
                "text": "Done with agent work."}]}},
        ]
        with open(transcript, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

        data = process_transcripts.extract_session_data(transcript)

        with patch.object(process_transcripts, "DB_DIR", tmp_memory_dir), \
             patch.object(process_transcripts, "DB_PATH", db_path):
            uuid = process_transcripts.store_session_log(
                db_conn, data["session_id"], data["project"],
                data["summary"], str(transcript),
            )

        # Check DB for agent tag
        row = db_conn.execute(
            "SELECT tags FROM memories WHERE uuid = ?", (uuid,)
        ).fetchone()
        assert row is not None
        tags = row[0] or ""
        assert "agent" in tags.lower(), (
            f"Subagent session_log in DB should have 'agent' tag. "
            f"Tags were: '{tags}'. store_session_log should detect subagent "
            f"transcripts (e.g., via parentSessionId) and tag them appropriately."
        )

        # Also check the JSON record file
        record_path = tmp_memory_dir / "records" / f"{uuid}.json"
        assert record_path.exists(), (
            f"Record file not found at {record_path}"
        )
        record = json.loads(record_path.read_text())
        record_tags = record.get("tags") or ""
        assert "agent" in record_tags.lower(), (
            f"Subagent session_log record file should have 'agent' tag. "
            f"Tags were: '{record_tags}'"
        )

    def test_subagent_session_log_references_parent(self, tmp_path,
                                                     db_conn, db_path,
                                                     tmp_memory_dir):
        """Subagent session_log should reference the parent session."""
        transcript = tmp_path / "agent-child.jsonl"
        entries = [
            {"type": "user", "timestamp": "2026-03-10T10:00:00Z",
             "sessionId": "agent-child",
             "cwd": "/home/scott/projects/testproj",
             "parentSessionId": "parent-sess-002",
             "message": {"content": "Child agent task"}},
            {"type": "assistant", "timestamp": "2026-03-10T10:01:00Z",
             "message": {"content": [{"type": "text",
                "text": "Completed child task."}]}},
        ]
        with open(transcript, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

        data = process_transcripts.extract_session_data(transcript)

        with patch.object(process_transcripts, "DB_DIR", tmp_memory_dir), \
             patch.object(process_transcripts, "DB_PATH", db_path):
            uuid = process_transcripts.store_session_log(
                db_conn, data["session_id"], data["project"],
                data["summary"], str(transcript),
            )

        # The session_log content or metadata should reference the parent
        row = db_conn.execute(
            "SELECT content FROM memories WHERE uuid = ?", (uuid,)
        ).fetchone()
        assert row is not None
        content = row[0]

        # Check that parent session is referenced somewhere
        assert "parent-sess-002" in content, (
            f"Subagent session_log should reference parent session 'parent-sess-002' "
            f"in its content. Got: {content}"
        )
