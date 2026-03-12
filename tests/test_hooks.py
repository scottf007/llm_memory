"""Tests for hook scripts — session_start.sh behavior around narratives.

These tests verify that:
1. Post-compaction reloads check for stale narratives (not just reload silently)
2. Stale narrative detection triggers a mandatory update (not just "Consider")
3. Cross-project staleness is detected (not just missing narratives)
4. pre_compact tells Claude to update narrative (not just save notes)
5. session_end.sh handles special characters in summaries
6. Project derivation doesn't produce garbage names
"""

import json
import os
import sqlite3
import subprocess
from pathlib import Path

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

    # Create DB
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


def _run_session_start(home, source="startup", cwd="/home/scott/projects/testproj"):
    """Run session_start.sh with a fake HOME and return its stdout."""
    input_json = json.dumps({
        "source": source,
        "trigger": source,
        "cwd": cwd,
        "session_id": "test-session-123",
    })
    stdout, stderr, rc = _run_hook("session_start.sh", home, input_json)
    return stdout, stderr, rc


# ---- Post-compaction narrative staleness ----

class TestPostCompactNarrativeStaleness:
    """After compaction, session_start.sh should detect stale narratives
    and instruct Claude to update them — not just silently reload."""

    def test_compact_with_stale_narrative_should_mention_update(self, tmp_path):
        """Post-compaction with new sessions since narrative should tell Claude to update."""
        home, conn, _ = _setup_test_home(tmp_path)

        # Insert a narrative created "yesterday"
        conn.execute(
            "INSERT INTO memories (uuid, type, content, project, created_at, importance) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("aaaa" * 8, "narrative", "# Old narrative", "testproj",
             "2026-03-10T10:00:00", 10),
        )
        # Insert session_logs AFTER the narrative
        for i in range(3):
            conn.execute(
                "INSERT INTO memories (uuid, type, content, project, session_id, created_at, importance) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (os.urandom(16).hex(), "session_log",
                 f"Session {i}", "testproj", f"sess-{i}",
                 "2026-03-11T10:00:00", 3),
            )
        conn.commit()
        conn.close()

        stdout, _, rc = _run_session_start(home, source="compact",
                                           cwd="/home/scott/projects/testproj")

        # Post-compact output should mention that the narrative needs updating
        assert "update" in stdout.lower() or "stale" in stdout.lower() or "new session" in stdout.lower(), (
            f"Post-compaction with 3 new sessions should warn about stale narrative, "
            f"but output was:\n{stdout}"
        )

    def test_compact_with_current_narrative_no_warning(self, tmp_path):
        """Post-compaction with no new sessions should NOT warn about staleness."""
        home, conn, _ = _setup_test_home(tmp_path)

        # Narrative is the most recent thing
        conn.execute(
            "INSERT INTO memories (uuid, type, content, project, created_at, importance) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("bbbb" * 8, "narrative", "# Fresh narrative", "testproj",
             "2026-03-11T10:00:00", 10),
        )
        # Session log is BEFORE the narrative
        conn.execute(
            "INSERT INTO memories (uuid, type, content, project, session_id, created_at, importance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (os.urandom(16).hex(), "session_log",
             "Old session", "testproj", "old-sess",
             "2026-03-10T10:00:00", 3),
        )
        conn.commit()
        conn.close()

        stdout, _, rc = _run_session_start(home, source="compact",
                                           cwd="/home/scott/projects/testproj")

        # Should not falsely warn
        assert "stale" not in stdout.lower()
        # But should still load the narrative
        assert "Fresh narrative" in stdout


# ---- Stale narrative should be mandatory ----

class TestStaleNarrativeMandatory:
    """When a narrative exists but is stale (new sessions since last update),
    session_start should make the update MANDATORY, not just a suggestion."""

    def test_stale_narrative_must_update_not_consider(self, tmp_path):
        """Stale narrative message should say MUST/AUTOMATIC, not 'Consider'."""
        home, conn, _ = _setup_test_home(tmp_path)

        conn.execute(
            "INSERT INTO memories (uuid, type, content, project, created_at, importance) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("cccc" * 8, "narrative", "# Old narrative", "testproj",
             "2026-03-10T10:00:00", 10),
        )
        for i in range(5):
            conn.execute(
                "INSERT INTO memories (uuid, type, content, project, session_id, created_at, importance) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (os.urandom(16).hex(), "session_log",
                 f"Session {i}", "testproj", f"sess-{i}",
                 "2026-03-11T10:00:00", 3),
            )
        conn.commit()
        conn.close()

        stdout, _, rc = _run_session_start(home, source="startup",
                                           cwd="/home/scott/projects/testproj")

        # Should NOT just say "Consider"
        has_consider_only = "consider" in stdout.lower() and "must" not in stdout.lower()
        assert not has_consider_only, (
            f"Stale narrative message should be mandatory (MUST/AUTOMATIC TASK), "
            f"not a weak suggestion ('Consider'). Output was:\n{stdout}"
        )

        # Should contain a mandatory instruction
        mandatory_words = ["must", "automatic task", "update the narrative now",
                           "you must"]
        has_mandatory = any(w in stdout.lower() for w in mandatory_words)
        assert has_mandatory, (
            f"Stale narrative (5 new sessions) should trigger mandatory update instruction. "
            f"Output was:\n{stdout}"
        )

    def test_missing_narrative_is_mandatory(self, tmp_path):
        """Sanity check: missing narrative should already be mandatory."""
        home, conn, _ = _setup_test_home(tmp_path)

        conn.execute(
            "INSERT INTO memories (uuid, type, content, project, session_id, created_at, importance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (os.urandom(16).hex(), "session_log",
             "Session 1", "testproj", "sess-1",
             "2026-03-11T10:00:00", 3),
        )
        conn.commit()
        conn.close()

        stdout, _, rc = _run_session_start(home, source="startup",
                                           cwd="/home/scott/projects/testproj")

        assert "must" in stdout.lower() or "automatic task" in stdout.lower(), (
            f"Missing narrative should trigger AUTOMATIC TASK. Output:\n{stdout}"
        )


# ---- Stale narrative triggers AUTOMATIC TASK, not NOTE ----

class TestStaleNarrativeAutomaticTask:
    """When a narrative exists but has new sessions since it was written,
    the hook must output 'AUTOMATIC TASK', not a soft 'NOTE: ... Consider'."""

    def test_stale_narrative_says_automatic_task(self, tmp_path):
        """Stale narrative should trigger 'AUTOMATIC TASK' in output."""
        home, conn, _ = _setup_test_home(tmp_path)

        conn.execute(
            "INSERT INTO memories (uuid, type, content, project, created_at, importance) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("dddd" * 8, "narrative", "# Old narrative", "testproj",
             "2026-03-10T10:00:00", 10),
        )
        conn.execute(
            "INSERT INTO memories (uuid, type, content, project, session_id, created_at, importance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (os.urandom(16).hex(), "session_log",
             "New session", "testproj", "new-sess",
             "2026-03-11T10:00:00", 3),
        )
        conn.commit()
        conn.close()

        stdout, _, rc = _run_session_start(home, source="startup",
                                           cwd="/home/scott/projects/testproj")

        assert "AUTOMATIC TASK" in stdout, (
            f"Stale narrative should output 'AUTOMATIC TASK', got:\n{stdout}"
        )
        assert "NOTE:" not in stdout, (
            f"Stale narrative should NOT use soft 'NOTE:', got:\n{stdout}"
        )

    def test_fresh_narrative_no_automatic_task(self, tmp_path):
        """Narrative with no new sessions should NOT trigger AUTOMATIC TASK."""
        home, conn, _ = _setup_test_home(tmp_path)

        conn.execute(
            "INSERT INTO memories (uuid, type, content, project, created_at, importance) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("eeee" * 8, "narrative", "# Fresh narrative", "testproj",
             "2026-03-11T10:00:00", 10),
        )
        conn.execute(
            "INSERT INTO memories (uuid, type, content, project, session_id, created_at, importance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (os.urandom(16).hex(), "session_log",
             "Old session", "testproj", "old-sess",
             "2026-03-10T10:00:00", 3),
        )
        conn.commit()
        conn.close()

        stdout, _, rc = _run_session_start(home, source="startup",
                                           cwd="/home/scott/projects/testproj")

        assert "AUTOMATIC TASK" not in stdout, (
            f"Fresh narrative should NOT trigger AUTOMATIC TASK, got:\n{stdout}"
        )


# ---- Cross-project staleness detection ----

class TestCrossProjectStaleness:
    """The 'OTHER PROJECTS NEEDING NARRATIVES' check currently only finds
    projects with NO narrative. It should also flag projects with STALE narratives."""

    def test_stale_other_project_should_be_flagged(self, tmp_path):
        """A different project with a stale narrative should appear in the
        cross-project warning, not just projects with zero narratives."""
        home, conn, _ = _setup_test_home(tmp_path)

        # Current project (testproj) has a current narrative
        conn.execute(
            "INSERT INTO memories (uuid, type, content, project, created_at, importance) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (os.urandom(16).hex(), "narrative", "# testproj narrative", "testproj",
             "2026-03-11T10:00:00", 10),
        )

        # Other project (otherproj) has a narrative but it's stale
        conn.execute(
            "INSERT INTO memories (uuid, type, content, project, created_at, importance) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (os.urandom(16).hex(), "narrative", "# otherproj narrative", "otherproj",
             "2026-03-08T10:00:00", 10),
        )
        # 5 sessions after the narrative for otherproj
        for i in range(5):
            conn.execute(
                "INSERT INTO memories (uuid, type, content, project, session_id, created_at, importance) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (os.urandom(16).hex(), "session_log",
                 f"Session {i}", "otherproj", f"other-sess-{i}",
                 "2026-03-11T10:00:00", 3),
            )
        conn.commit()
        conn.close()

        stdout, _, rc = _run_session_start(home, source="startup",
                                           cwd="/home/scott/projects/testproj")

        assert "otherproj" in stdout, (
            f"Project 'otherproj' has a stale narrative (5 new sessions) but was not "
            f"flagged in cross-project check. Output:\n{stdout}"
        )


# ---- pre_compact should mention narrative update ----

class TestPreCompactNarrativeInstruction:
    """pre_compact.sh should tell Claude to update the narrative before
    compaction happens, not just save notes."""

    def test_pre_compact_mentions_narrative_update(self, tmp_path):
        """pre_compact output should instruct Claude to update the narrative."""
        home, _, _ = _setup_test_home(tmp_path)
        input_json = json.dumps({"session_id": "test-123"})

        stdout, _, rc = _run_hook("pre_compact.sh", home, input_json)

        assert "narrative" in stdout.lower(), (
            f"pre_compact should mention updating the narrative, not just saving notes. "
            f"Output:\n{stdout}"
        )

        # Should say to update it NOW, not defer to "next session start"
        deferred = "next session" in stdout.lower() or "will be updated" in stdout.lower()
        assert not deferred, (
            f"pre_compact should NOT defer narrative update to next session. "
            f"It should say to do it NOW before context is lost. Output:\n{stdout}"
        )


# ---- session_end.sh shell injection ----

class TestSessionEndEscaping:
    """session_end.sh injects $SUMMARY into a triple-quoted Python string.
    Content with single quotes breaks the Python code silently."""

    def test_summary_with_single_quotes(self, tmp_path):
        """A transcript whose last assistant message contains single quotes
        should still produce a valid session_log record."""
        home, conn, _ = _setup_test_home(tmp_path)
        conn.close()

        # Create a transcript where the assistant says something with quotes
        transcript = tmp_path / "transcript.jsonl"
        entries = [
            {"type": "user", "timestamp": "2026-03-10T10:00:00Z",
             "sessionId": "quotetest", "cwd": "/home/scott/projects/testproj",
             "message": {"content": "How do I fix this?"}},
            {"type": "assistant", "timestamp": "2026-03-10T10:01:00Z",
             "message": {"content": [{"type": "text",
                "text": "It's a common issue — you'll need to update the config. Here's what I'd suggest for the project's setup."}]}},
        ]
        with open(transcript, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

        input_json = json.dumps({
            "session_id": "quotetest",
            "transcript_path": str(transcript),
        })
        stdout, stderr, rc = _run_hook("session_end.sh", home, input_json)

        # Check that a record file was created
        records_dir = home / ".claude" / "memory" / "records"
        record_files = list(records_dir.glob("*.json"))
        assert len(record_files) >= 1, (
            f"session_end.sh should create a session_log record even when summary "
            f"contains single quotes, but no record files found. "
            f"stderr: {stderr}"
        )

        # Verify the record is valid JSON
        record = json.loads(record_files[0].read_text())
        assert record["type"] == "session_log"
        assert record["session_id"] == "quotetest"


# ---- Project derivation ----

class TestProjectDerivation:
    """Project names should be meaningful, not garbage or empty strings.
    When cwd doesn't match /projects/X, content should be used to derive
    a project name rather than falling back to 'general' for long conversations."""

    def test_non_project_cwd_with_themed_content_should_not_be_general(self, tmp_path):
        """A long conversation about a specific topic from a non-project cwd
        should derive a meaningful project name, not just 'general'."""
        # Create a transcript with clear topic but non-project cwd
        transcript_entries = [
            {"type": "user", "cwd": "/home/scott",
             "message": {"content": "Let's set up Home Assistant with Shelly devices"}},
            {"type": "assistant",
             "message": {"content": [{"type": "text",
                "text": "I'll help you configure Home Assistant for your Shelly devices."}]}},
        ] * 10  # 10 turns about Home Assistant

        # Write to a transcript dir that would produce "general" fallback
        transcript_dir = tmp_path / "transcripts"
        transcript_dir.mkdir()
        path = transcript_dir / "themed-session.jsonl"
        with open(path, "w") as f:
            for entry in transcript_entries:
                f.write(json.dumps(entry) + "\n")

        data = process_transcripts.extract_session_data(path)
        project = data["project"]

        # "general" or "transcripts" is not acceptable for 10 turns about
        # a specific topic — should derive from content
        assert project not in ("general", "transcripts"), (
            f"A 10-turn conversation about Home Assistant from /home/scott "
            f"was classified as '{project}'. Should derive a topic-based project name."
        )

    def test_project_cwd_derivation_works(self):
        """Standard /projects/X derivation should still work."""
        result = process_transcripts.derive_project(
            ["/home/scott/projects/finance_nexus"], Path("/tmp")
        )
        assert result == "finance_nexus"

    def test_non_projects_folder_uses_dirname(self):
        """A cwd like /home/alice/code/myapp should derive project 'myapp',
        not require a 'projects/' parent. The folder IS the project."""
        result = process_transcripts.derive_project(
            ["/home/alice/code/myapp"], Path("/tmp")
        )
        assert result == "myapp", (
            f"cwd '/home/alice/code/myapp' should derive project 'myapp', "
            f"got '{result}'. Don't require a 'projects/' parent folder."
        )

    def test_home_dir_cwd_uses_content_not_dirname(self):
        """A cwd of just /home/scott should not use 'scott' as the project.
        It should fall through to content-based derivation or 'general'."""
        result = process_transcripts.derive_project(
            ["/home/scott"], Path("/tmp")
        )
        # 'scott' is a user's home dir, not a project name
        assert result != "scott", (
            f"cwd '/home/scott' should not use 'scott' as project name — "
            f"that's a home directory, not a project."
        )

    def test_empty_project_never_returned(self):
        """derive_project should never return an empty string."""
        result = process_transcripts.derive_project([], Path("/tmp"))
        assert result, "derive_project should never return empty string"


class TestOrphanProjectMatching:
    """Sessions from non-project cwds (e.g. /home/scott) that are clearly about
    a known project should be matched to that project, not classified as 'general'."""

    def test_content_about_known_project_matches(self, tmp_path):
        """A conversation about 'finance nexus' from /home/scott should match
        the existing finance_nexus project in the DB."""
        # Set up a DB with known projects
        db_dir = tmp_path / "memory"
        db_dir.mkdir()
        db_path = db_dir / "memory.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(SERVER_SCHEMA)
        conn.execute(
            "INSERT INTO memories (uuid, type, content, project, importance) "
            "VALUES (?, ?, ?, ?, ?)",
            ("aaaa" * 8, "narrative", "Finance app", "finance_nexus", 10),
        )
        conn.execute(
            "INSERT INTO memories (uuid, type, content, project, importance) "
            "VALUES (?, ?, ?, ?, ?)",
            ("bbbb" * 8, "narrative", "Memory system", "llm_memory", 10),
        )
        conn.commit()
        conn.close()

        # Monkey-patch DB_DIR to use our test DB
        original_db_dir = process_transcripts.DB_DIR
        process_transcripts.DB_DIR = db_dir
        try:
            user_texts = [
                "Let's work on the finance nexus dashboard",
                "The finance nexus needs better transaction categorization",
                "Can you check the finance nexus API endpoints?",
                "I want to add a new report to finance nexus",
            ]
            result = process_transcripts.derive_project(
                ["/home/scott"], Path("/tmp"), user_texts
            )
            assert result == "finance_nexus", (
                f"4 messages about 'finance nexus' from /home/scott should match "
                f"known project 'finance_nexus', got '{result}'"
            )
        finally:
            process_transcripts.DB_DIR = original_db_dir

    def test_unrelated_content_stays_general(self, tmp_path):
        """Content that doesn't match any known project should remain 'general'."""
        db_dir = tmp_path / "memory"
        db_dir.mkdir()
        db_path = db_dir / "memory.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(SERVER_SCHEMA)
        conn.execute(
            "INSERT INTO memories (uuid, type, content, project, importance) "
            "VALUES (?, ?, ?, ?, ?)",
            ("aaaa" * 8, "narrative", "Finance app", "finance_nexus", 10),
        )
        conn.commit()
        conn.close()

        original_db_dir = process_transcripts.DB_DIR
        process_transcripts.DB_DIR = db_dir
        try:
            user_texts = [
                "How do I cook pasta?",
                "What temperature for baking bread?",
            ]
            result = process_transcripts.derive_project(
                ["/home/scott"], Path("/home/scott/.claude/memory/transcripts"), user_texts
            )
            assert result == "general", (
                f"Unrelated cooking content should be 'general', got '{result}'"
            )
        finally:
            process_transcripts.DB_DIR = original_db_dir
