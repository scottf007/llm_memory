"""Tests for transcript processing."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

import process_transcripts


class TestExtractSessionData:
    def test_basic_extraction(self, sample_jsonl):
        data = process_transcripts.extract_session_data(sample_jsonl)
        assert data["session_id"] == "abc12345"
        assert data["project"] == "testproj"
        assert data["turn_count"] == 3
        assert data["summary"] != ""

    def test_empty_file(self, tmp_path):
        empty = tmp_path / "empty.jsonl"
        empty.write_text("")
        data = process_transcripts.extract_session_data(empty)
        assert data["turn_count"] == 0


class TestDeriveProject:
    def test_from_cwd(self):
        cwds = ["/home/scott/projects/myproj"]
        result = process_transcripts.derive_project(cwds, Path("/tmp"))
        assert result == "myproj"

    def test_from_dir_name(self):
        dir_path = Path("/home/scott/.claude/projects/-home-scott-projects-finance-nexus")
        result = process_transcripts.derive_project([], dir_path)
        assert result == "finance-nexus"

    def test_fallback_to_general(self):
        result = process_transcripts.derive_project([], Path("/tmp/random"))
        assert result == "random" or result == "general"


class TestStoreSessionLog:
    def test_creates_json_file(self, db_conn, db_path, tmp_memory_dir):
        """store_session_log should create a record file and DB entry."""
        with patch.object(process_transcripts, "DB_DIR", tmp_memory_dir), \
             patch.object(process_transcripts, "DB_PATH", db_path):
            # Need to re-create the schema since db_conn uses UUID schema
            uuid = process_transcripts.store_session_log(
                db_conn, "sess-123", "myproj",
                "Session sess-123 for myproj, 10 turns.",
                "~/.claude/memory/transcripts/sess-123.jsonl",
            )
            assert len(uuid) == 32

            # Check file exists
            path = tmp_memory_dir / "records" / f"{uuid}.json"
            assert path.exists()
            record = json.loads(path.read_text())
            assert record["type"] == "session_log"
            assert record["project"] == "myproj"
            assert record["session_id"] == "sess-123"

    def test_dedup_via_files(self, tmp_memory_dir):
        """get_processed_sessions should find sessions from record files."""
        # Write a record file manually
        uuid = os.urandom(16).hex()
        record = {
            "schema_version": 1,
            "uuid": uuid,
            "type": "session_log",
            "session_id": "already-processed",
            "content": "test",
            "project": "test",
            "importance": 3,
            "created_at": "2026-03-10T12:00:00",
            "connections": [],
        }
        records_dir = tmp_memory_dir / "records"
        records_dir.mkdir(exist_ok=True)
        (records_dir / f"{uuid}.json").write_text(json.dumps(record))

        with patch.object(process_transcripts, "DB_DIR", tmp_memory_dir):
            sessions = process_transcripts.get_processed_sessions(None)
            assert "already-processed" in sessions


class TestCleanText:
    def test_strips_noise_tags(self):
        text = "Hello <system-reminder>ignore this</system-reminder> world"
        result = process_transcripts._clean_text(text)
        assert "system-reminder" not in result
        assert "Hello" in result
        assert "world" in result

    def test_strips_html(self):
        text = "<h1>Title</h1><p>Content</p>"
        result = process_transcripts._clean_text(text)
        assert "<h1>" not in result
