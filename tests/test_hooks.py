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

    # Create DB
    db_path = memory_dir / "memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SERVER_SCHEMA)
    conn.commit()
    return home, conn, db_path


def _write_narrative(home, project, content="# narrative"):
    """Write a rendered narrative file to the test home."""
    path = home / ".claude" / "memory" / "projects" / f"{project}.narrative.md"
    path.write_text(content)
    return path


def _write_project_state(home, project, merged_session_ids=()):
    """Write a minimal {project}.json with the given merged session_ids."""
    path = home / ".claude" / "memory" / "projects" / f"{project}.json"
    state = {
        "schema_version": "0.1",
        "project": project,
        "sessions": [{"session_id": sid} for sid in merged_session_ids],
    }
    path.write_text(json.dumps(state))
    return path


def _register_session(home, session_id, project):
    """Write a stub conversation.md with the frontmatter the hooks read as the session registry."""
    path = home / ".claude" / "memory" / "conversations" / f"{session_id}.md"
    path.write_text(
        f"---\nsession_id: {session_id}\nproject: {project}\n---\n\n=== user ===\nhi\n"
    )
    return path


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


def _run_session_start(home, source="startup", cwd="/home/user/projects/testproj"):
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

        # Narrative file exists but {project}.json has no merged sessions yet.
        _write_narrative(home, "testproj", "# Old narrative")
        _write_project_state(home, "testproj", merged_session_ids=[])
        # 3 sessions have happened (conversation.md stubs) that haven't been merged
        for i in range(3):
            _register_session(home, f"sess-{i}", "testproj")
        conn.close()

        stdout, _, rc = _run_session_start(home, source="compact",
                                           cwd="/home/user/projects/testproj")

        # Post-compact output should mention that the narrative needs updating
        assert "update" in stdout.lower() or "stale" in stdout.lower() or "new session" in stdout.lower(), (
            f"Post-compaction with 3 new sessions should warn about stale narrative, "
            f"but output was:\n{stdout}"
        )

    def test_compact_with_current_narrative_no_warning(self, tmp_path):
        """Post-compaction with no new sessions should NOT warn about staleness."""
        home, conn, _ = _setup_test_home(tmp_path)

        # Narrative + project state with the session already merged.
        _write_narrative(home, "testproj", "# Fresh narrative")
        _write_project_state(home, "testproj", merged_session_ids=["old-sess"])
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
                                           cwd="/home/user/projects/testproj")

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

        _write_narrative(home, "testproj", "# Old narrative")
        _write_project_state(home, "testproj", merged_session_ids=[])
        for i in range(5):
            _register_session(home, f"sess-{i}", "testproj")
        conn.close()

        stdout, _, rc = _run_session_start(home, source="startup",
                                           cwd="/home/user/projects/testproj")

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

        _register_session(home, "sess-1", "testproj")
        conn.close()

        stdout, _, rc = _run_session_start(home, source="startup",
                                           cwd="/home/user/projects/testproj")

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

        _write_narrative(home, "testproj", "# Old narrative")
        _write_project_state(home, "testproj", merged_session_ids=[])
        _register_session(home, "new-sess", "testproj")
        conn.commit()
        conn.close()

        stdout, _, rc = _run_session_start(home, source="startup",
                                           cwd="/home/user/projects/testproj")

        assert "AUTOMATIC TASK" in stdout, (
            f"Stale narrative should output 'AUTOMATIC TASK', got:\n{stdout}"
        )
        assert "NOTE:" not in stdout, (
            f"Stale narrative should NOT use soft 'NOTE:', got:\n{stdout}"
        )

    def test_fresh_narrative_no_automatic_task(self, tmp_path):
        """Narrative with all session_logs merged should NOT trigger AUTOMATIC TASK."""
        home, conn, _ = _setup_test_home(tmp_path)

        _write_narrative(home, "testproj", "# Fresh narrative")
        _write_project_state(home, "testproj", merged_session_ids=["old-sess"])
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
                                           cwd="/home/user/projects/testproj")

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

        # Current project (testproj) is fully current: narrative + state with
        # the only session merged, and a matching conversation.md.
        _write_narrative(home, "testproj", "# testproj narrative")
        _write_project_state(home, "testproj", merged_session_ids=["testproj-s1"])
        _register_session(home, "testproj-s1", "testproj")

        # otherproj has a narrative + state but 5 new conversation.md stubs → stale.
        _write_narrative(home, "otherproj", "# otherproj narrative")
        _write_project_state(home, "otherproj", merged_session_ids=[])
        for i in range(5):
            _register_session(home, f"other-sess-{i}", "otherproj")
        conn.close()

        stdout, _, rc = _run_session_start(home, source="startup",
                                           cwd="/home/user/projects/testproj")

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
