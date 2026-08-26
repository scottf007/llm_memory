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
import sys
import time
from datetime import datetime, timezone, timedelta
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


def _run_hook(hook_name, home, input_json, timeout=30):
    """Run a hook script with a fake HOME and return stdout, stderr, rc."""
    env = os.environ.copy()
    env["HOME"] = str(home)
    # The age-signal snippet imports server.py (needs mcp). Prefer the
    # interpreter running this test so a bare `python3` on PATH is not the
    # system one.
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get(
        "PATH", "/usr/bin:/bin"
    )
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


# ---- Roadmap #4: age-based liveness (unprocessed content aging) ----

SUBSTANTIVE_ASSISTANT = (
    "Agreed on eventual consistency for this path. One addition: the "
    "reconciliation pass needs to be idempotent, because a crash mid-pass "
    "and a retry must not double-apply a write. I'll add a version check."
)


def _age_file(path, days):
    ts = time.time() - days * 86400
    os.utime(path, (ts, ts))


def _write_substantive_transcript(home, session_id, n_user_turns=5, last_ts=None):
    """Archive transcript that clears claude's min_user_turns=5 + content gate."""
    records = []
    base = datetime.now(timezone.utc) if last_ts is None else last_ts
    for i in range(n_user_turns):
        records.append({
            "type": "user",
            "timestamp": (base - timedelta(hours=n_user_turns - i)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "message": {"role": "user", "content": f"turn {i}: please continue the work"},
        })
        records.append({
            "type": "assistant",
            "timestamp": (
                base if i == n_user_turns - 1
                else base - timedelta(hours=n_user_turns - i)
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "message": {"role": "assistant", "content": SUBSTANTIVE_ASSISTANT},
        })
    path = home / ".claude" / "memory" / "transcripts" / f"{session_id}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return path


class TestNarrativeLivenessAgeSignal:
    """Acceptance: an N-day-stale narrative with unprocessed real content
    must be visible; a dormant project's old narrative must not."""

    def test_13day_unprocessed_mentions_age(self, tmp_path):
        """13-day incident shape: old narrative + unmerged substantive
        transcripts. Check #2 already fires on count; the age line must
        now also name the days."""
        home, conn, _ = _setup_test_home(tmp_path)
        narr = _write_narrative(home, "testproj", "# narrative body")
        _age_file(narr, 13)
        _write_project_state(home, "testproj", merged_session_ids=[])
        _register_session(home, "unmerged-1", "testproj")
        _write_substantive_transcript(
            home, "unmerged-1",
            last_ts=datetime.now(timezone.utc) - timedelta(days=13),
        )
        conn.close()

        stdout, _, rc = _run_session_start(home, source="startup",
                                           cwd="/home/user/projects/testproj")
        assert rc == 0
        assert "AUTOMATIC TASK" in stdout
        assert "new session(s) since last narrative" in stdout, (
            f"count-based check #2 must still fire. Output:\n{stdout}"
        )
        assert "AGE:" in stdout and "13d stale" in stdout, (
            f"13-day unprocessed content must name the age. Output:\n{stdout}"
        )
        assert "MUST" in stdout

    def test_stale_merged_no_new_sessions_fires_age(self, tmp_path):
        """The gap none of the three existing checks cover: NEW_SESSIONS=0
        (session already in {project}.json) but the transcript grew N+ days
        after merge (_stale_session)."""
        home, conn, _ = _setup_test_home(tmp_path)
        narr = _write_narrative(home, "testproj", "# narrative body")
        _age_file(narr, 13)
        ended = (datetime.now(timezone.utc) - timedelta(days=20)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        path = home / ".claude" / "memory" / "projects" / "testproj.json"
        path.write_text(json.dumps({
            "schema_version": "0.1",
            "project": "testproj",
            "sessions": [{"session_id": "long-run", "ended": ended}],
        }))
        _register_session(home, "long-run", "testproj")
        # Tail sat 13d — wait is last_activity, not grew_days (merge-to-tail).
        _write_substantive_transcript(
            home, "long-run",
            last_ts=datetime.now(timezone.utc) - timedelta(days=13),
        )
        conn.close()

        stdout, _, rc = _run_session_start(home, source="startup",
                                           cwd="/home/user/projects/testproj")
        assert rc == 0
        assert "new session(s) since last narrative" not in stdout, (
            f"count-based check #2 must stay silent (NEW_SESSIONS=0). Output:\n{stdout}"
        )
        assert "AUTOMATIC TASK" in stdout, (
            f"age signal must fire for grown-after-merge content. Output:\n{stdout}"
        )
        assert "stale" in stdout.lower()
        assert "MUST" in stdout

    def test_dormant_old_narrative_no_automatic_task(self, tmp_path):
        """Non-trigger control: 13-day-old narrative, everything merged, no
        grown tail. PM-STATE §3: a dormant project's old narrative is correct."""
        home, conn, _ = _setup_test_home(tmp_path)
        narr = _write_narrative(home, "testproj", "# dormant narrative")
        _age_file(narr, 13)
        ended = (datetime.now(timezone.utc) - timedelta(days=13)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        path = home / ".claude" / "memory" / "projects" / "testproj.json"
        path.write_text(json.dumps({
            "schema_version": "0.1",
            "project": "testproj",
            "sessions": [{"session_id": "only", "ended": ended}],
        }))
        _register_session(home, "only", "testproj")
        # Transcript last ts matches `ended` — no growth after merge.
        ended_dt = datetime.fromisoformat(ended.replace("Z", "+00:00"))
        _write_substantive_transcript(home, "only", last_ts=ended_dt)
        conn.close()

        stdout, _, rc = _run_session_start(home, source="startup",
                                           cwd="/home/user/projects/testproj")
        assert rc == 0
        assert "AUTOMATIC TASK" not in stdout, (
            f"dormant old narrative must not false-alarm. Output:\n{stdout}"
        )
        assert "AGE:" not in stdout

    def test_recent_narrative_with_new_sessions_has_no_age_line(self, tmp_path):
        """Count-based check #2 still fires for a fresh backlog; the age
        line must not, because the content has not been aging N days."""
        home, conn, _ = _setup_test_home(tmp_path)
        _write_narrative(home, "testproj", "# fresh narrative")  # mtime = now
        _write_project_state(home, "testproj", merged_session_ids=[])
        _register_session(home, "today-1", "testproj")
        _write_substantive_transcript(home, "today-1")
        conn.close()

        stdout, _, rc = _run_session_start(home, source="startup",
                                           cwd="/home/user/projects/testproj")
        assert rc == 0
        assert "AUTOMATIC TASK" in stdout
        assert "new session(s) since last narrative" in stdout
        assert "AGE:" not in stdout, (
            f"fresh backlog must not get the age line. Output:\n{stdout}"
        )

    def test_fresh_tail_after_old_merge_no_age(self, tmp_path):
        """Adversarial #1 at the hook: merge 20d ago, tail written now.
        NEW_SESSIONS=0 and wait=0 — must not AUTOMATIC TASK."""
        home, conn, _ = _setup_test_home(tmp_path)
        narr = _write_narrative(home, "testproj", "# narrative body")
        _age_file(narr, 1)
        ended = (datetime.now(timezone.utc) - timedelta(days=20)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        path = home / ".claude" / "memory" / "projects" / "testproj.json"
        path.write_text(json.dumps({
            "schema_version": "0.1",
            "project": "testproj",
            "sessions": [{"session_id": "fresh-tail", "ended": ended}],
        }))
        _register_session(home, "fresh-tail", "testproj")
        _write_substantive_transcript(
            home, "fresh-tail", last_ts=datetime.now(timezone.utc)
        )
        conn.close()

        stdout, _, rc = _run_session_start(home, source="startup",
                                           cwd="/home/user/projects/testproj")
        assert rc == 0
        assert "AUTOMATIC TASK" not in stdout, (
            f"fresh tail after old merge must not false-alarm. Output:\n{stdout}"
        )
        assert "AGE:" not in stdout

    def test_old_wait_after_short_growth_fires_age(self, tmp_path):
        """Adversarial #2 at the hook: grew 1.5d after merge, then quiet 16d.
        NEW_SESSIONS=0, grew_days under 7, wait 16d — must AUTOMATIC TASK."""
        home, conn, _ = _setup_test_home(tmp_path)
        narr = _write_narrative(home, "testproj", "# narrative body")
        _age_file(narr, 1)
        last = datetime.now(timezone.utc) - timedelta(days=16)
        ended = last - timedelta(hours=36)
        path = home / ".claude" / "memory" / "projects" / "testproj.json"
        path.write_text(json.dumps({
            "schema_version": "0.1",
            "project": "testproj",
            "sessions": [{
                "session_id": "quiet-tail",
                "ended": ended.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }],
        }))
        _register_session(home, "quiet-tail", "testproj")
        _write_substantive_transcript(home, "quiet-tail", last_ts=last)
        conn.close()

        stdout, _, rc = _run_session_start(home, source="startup",
                                           cwd="/home/user/projects/testproj")
        assert rc == 0
        assert "new session(s) since last narrative" not in stdout
        assert "AUTOMATIC TASK" in stdout, (
            f"16d-waiting tail must fire even when grew_days is 1.5. Output:\n{stdout}"
        )
        assert "16d stale" in stdout or "AGE:" in stdout
