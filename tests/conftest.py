"""Shared fixtures for llm_memory tests."""

import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest


def generate_uuid():
    return os.urandom(16).hex()


@pytest.fixture
def tmp_memory_dir(tmp_path):
    """Create a temporary memory directory structure."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "records").mkdir()
    (memory_dir / "transcripts").mkdir()
    (memory_dir / "config").mkdir()
    return memory_dir


@pytest.fixture
def db_path(tmp_memory_dir):
    """Return a temporary database path."""
    return tmp_memory_dir / "memory.db"


@pytest.fixture
def db_conn(db_path):
    """Create a fresh database with the UUID schema and return a connection."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
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

        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
            content, type, project, tags,
            content='memories',
            content_rowid='rowid'
        );

        CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
            INSERT INTO memories_fts(rowid, content, type, project, tags)
            VALUES (new.rowid, new.content, new.type, new.project, new.tags);
        END;

        CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, content, type, project, tags)
            VALUES('delete', old.rowid, old.content, old.type, old.project, old.tags);
        END;

        CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, content, type, project, tags)
            VALUES('delete', old.rowid, old.content, old.type, old.project, old.tags);
            INSERT INTO memories_fts(rowid, content, type, project, tags)
            VALUES (new.rowid, new.content, new.type, new.project, new.tags);
        END;

        CREATE TABLE IF NOT EXISTS connections (
            from_uuid TEXT NOT NULL REFERENCES memories(uuid),
            to_uuid TEXT NOT NULL REFERENCES memories(uuid),
            relationship TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (from_uuid, to_uuid, relationship)
        );
    """)
    yield conn
    conn.close()


@pytest.fixture
def sample_memories(db_conn):
    """Insert a set of sample memories and return their UUIDs."""
    uuids = {}

    uuid = generate_uuid()
    db_conn.execute(
        "INSERT INTO memories (uuid, type, content, project, importance, tags) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (uuid, "narrative", "# Project Alpha\n\nA test project about widgets." + " detail" * 200,
         "alpha", 10, "project-narrative"),
    )
    uuids["narrative"] = uuid

    uuid = generate_uuid()
    db_conn.execute(
        "INSERT INTO memories (uuid, type, content, project, importance, tags, session_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (uuid, "note", "Always use pytest for testing.", "alpha", 7, "decision, testing", "sess-001"),
    )
    uuids["note1"] = uuid

    uuid = generate_uuid()
    db_conn.execute(
        "INSERT INTO memories (uuid, type, content, project, importance, tags, session_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (uuid, "note", "The FTS5 index must include tags.", "alpha", 8, "correction, fts", "sess-001"),
    )
    uuids["note2"] = uuid

    uuid = generate_uuid()
    db_conn.execute(
        "INSERT INTO memories (uuid, type, content, project, importance, tags, session_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (uuid, "session_log", "Session sess-001 for alpha, 50 turns.", "alpha", 3, None, "sess-001"),
    )
    uuids["session_log"] = uuid

    uuid = generate_uuid()
    db_conn.execute(
        "INSERT INTO memories (uuid, type, content, project, importance, tags) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (uuid, "note", "Beta uses FastAPI for the dashboard.", "beta", 6, "architecture"),
    )
    uuids["note_beta"] = uuid

    db_conn.commit()
    return uuids


@pytest.fixture
def sample_jsonl(tmp_path):
    """Create a sample JSONL transcript file."""
    transcript = tmp_path / "abc12345.jsonl"
    entries = [
        {"type": "user", "timestamp": "2026-03-08T10:00:00Z",
         "sessionId": "abc12345", "cwd": "/home/scott/projects/testproj",
         "message": {"content": "Hello, let's build something"}},
        {"type": "assistant", "timestamp": "2026-03-08T10:00:05Z",
         "message": {"content": [{"type": "text", "text": "Sure! Let's get started with the project setup."}]}},
        {"type": "user", "timestamp": "2026-03-08T10:01:00Z",
         "message": {"content": "Create a new file called main.py"}},
        {"type": "assistant", "timestamp": "2026-03-08T10:01:05Z",
         "message": {"content": [{"type": "text", "text": "I'll create main.py with a basic structure for you. This includes the entry point and configuration loading."}]}},
        {"type": "user", "timestamp": "2026-03-08T10:02:00Z",
         "message": {"content": "Add error handling"}},
        {"type": "assistant", "timestamp": "2026-03-08T10:02:10Z",
         "message": {"content": [{"type": "text", "text": "Done. I've added try/except blocks around the critical sections and a global exception handler."}]}},
    ]
    with open(transcript, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
    return transcript
