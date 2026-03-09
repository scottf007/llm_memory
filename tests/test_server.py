"""Tests for the MCP server tool handlers."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

# We need to patch DB_DIR and DB_PATH before importing handlers
import server


@pytest.fixture(autouse=True)
def patch_server_paths(tmp_memory_dir, db_path):
    """Redirect server to use temp directories for all tests."""
    with patch.object(server, "DB_DIR", tmp_memory_dir), \
         patch.object(server, "DB_PATH", db_path), \
         patch.object(server, "RECORDS_DIR", tmp_memory_dir / "records"):
        server.init_db()
        yield


class TestStore:
    def test_store_note(self):
        result = json.loads(server._handle_store({
            "content": "Test note",
            "type": "note",
            "project": "test",
            "importance": 7,
            "tags": "test",
        })[0].text)
        assert result["status"] == "stored"
        assert len(result["uuid"]) == 32

    def test_store_creates_json_file(self, tmp_memory_dir):
        result = json.loads(server._handle_store({
            "content": "File test",
            "type": "note",
            "project": "test",
        })[0].text)
        uuid = result["uuid"]
        path = tmp_memory_dir / "records" / f"{uuid}.json"
        assert path.exists()
        record = json.loads(path.read_text())
        assert record["content"] == "File test"
        assert record["schema_version"] == 1
        assert record["uuid"] == uuid

    def test_store_with_connections(self):
        r1 = json.loads(server._handle_store({
            "content": "First", "type": "note", "project": "test",
        })[0].text)
        r2 = json.loads(server._handle_store({
            "content": "Second", "type": "note", "project": "test",
            "connections": [{"to_uuid": r1["uuid"], "relationship": "related_to"}],
        })[0].text)
        assert r2["status"] == "stored"

        # Verify connection in JSON file
        path = Path(server.RECORDS_DIR) / f"{r2['uuid']}.json"
        record = json.loads(path.read_text())
        assert len(record["connections"]) == 1
        assert record["connections"][0]["to_uuid"] == r1["uuid"]

    def test_store_narrative(self):
        result = json.loads(server._handle_store({
            "content": "# Project narrative\nLong content here.",
            "type": "narrative",
            "project": "test",
            "importance": 10,
        })[0].text)
        assert result["status"] == "stored"

    def test_store_dedup(self):
        server._handle_store({"content": "Duplicate test", "type": "note", "project": "test"})
        result = json.loads(server._handle_store({
            "content": "Duplicate test", "type": "note", "project": "test",
        })[0].text)
        assert result["status"] == "duplicate_skipped"

    def test_store_invalid_type(self):
        result = server._handle_store({
            "content": "Bad type", "type": "invalid",
        })[0].text
        assert "Error" in result

    def test_store_empty_content(self):
        result = server._handle_store({"content": "", "type": "note"})[0].text
        assert "Error" in result


class TestSearch:
    def test_search_basic(self):
        server._handle_store({"content": "Python is great for scripting", "type": "note", "project": "test"})
        result = json.loads(server._handle_search({"query": "Python scripting"})[0].text)
        assert len(result) >= 1
        assert "uuid" in result[0]

    def test_search_with_project_filter(self):
        server._handle_store({"content": "Alpha content", "type": "note", "project": "alpha"})
        server._handle_store({"content": "Beta content", "type": "note", "project": "beta"})
        result = json.loads(server._handle_search({"query": "content", "project": "alpha"})[0].text)
        assert all(r["project"] == "alpha" for r in result)

    def test_search_with_type_filter(self):
        server._handle_store({"content": "A test narrative about search", "type": "narrative", "project": "test"})
        server._handle_store({"content": "A test note about search", "type": "note", "project": "test"})
        result = json.loads(server._handle_search({"query": "search", "type": "note"})[0].text)
        assert all(r["type"] == "note" for r in result)

    def test_search_narrative_truncation(self):
        long_content = "Searchable narrative " + "x" * 1000
        server._handle_store({"content": long_content, "type": "narrative", "project": "test"})
        result = json.loads(server._handle_search({"query": "Searchable narrative"})[0].text)
        assert len(result) >= 1
        assert len(result[0]["content"]) < len(long_content)
        assert "memory_get" in result[0]["content"]


class TestGet:
    def test_get_by_uuid(self):
        r = json.loads(server._handle_store({
            "content": "Get test", "type": "note", "project": "test",
        })[0].text)
        result = json.loads(server._handle_get({"uuid": r["uuid"]})[0].text)
        assert result["uuid"] == r["uuid"]
        assert result["content"] == "Get test"
        assert "connections" in result

    def test_get_not_found(self):
        result = server._handle_get({"uuid": "nonexistent" * 2})[0].text
        assert "Error" in result

    def test_get_with_connections(self):
        r1 = json.loads(server._handle_store({"content": "A", "type": "note", "project": "test"})[0].text)
        r2 = json.loads(server._handle_store({
            "content": "B", "type": "note", "project": "test",
            "connections": [{"to_uuid": r1["uuid"], "relationship": "related_to"}],
        })[0].text)
        result = json.loads(server._handle_get({"uuid": r2["uuid"]})[0].text)
        assert len(result["connections"]["outgoing"]) == 1
        assert result["connections"]["outgoing"][0]["to_uuid"] == r1["uuid"]


class TestRecent:
    def test_recent_default(self):
        server._handle_store({"content": "Recent 1", "type": "note", "project": "test"})
        server._handle_store({"content": "Recent 2", "type": "note", "project": "test"})
        result = json.loads(server._handle_recent({})[0].text)
        assert len(result) >= 2

    def test_recent_with_filters(self):
        server._handle_store({"content": "Note for alpha", "type": "note", "project": "alpha"})
        server._handle_store({"content": "Log for alpha", "type": "session_log", "project": "alpha"})
        result = json.loads(server._handle_recent({"project": "alpha", "type": "note"})[0].text)
        assert all(r["type"] == "note" and r["project"] == "alpha" for r in result)

    def test_recent_limit(self):
        for i in range(5):
            server._handle_store({"content": f"Limit test {i}", "type": "note", "project": "test"})
        result = json.loads(server._handle_recent({"limit": 3})[0].text)
        assert len(result) <= 3


class TestConnect:
    def test_connect_basic(self):
        r1 = json.loads(server._handle_store({"content": "A", "type": "note", "project": "t"})[0].text)
        r2 = json.loads(server._handle_store({"content": "B", "type": "note", "project": "t"})[0].text)
        result = json.loads(server._handle_connect({
            "from_uuid": r1["uuid"], "to_uuid": r2["uuid"], "relationship": "related_to",
        })[0].text)
        assert result["status"] == "connected"

    def test_connect_updates_json_file(self):
        r1 = json.loads(server._handle_store({"content": "Source", "type": "note", "project": "t"})[0].text)
        r2 = json.loads(server._handle_store({"content": "Target", "type": "note", "project": "t"})[0].text)
        server._handle_connect({"from_uuid": r1["uuid"], "to_uuid": r2["uuid"], "relationship": "supersedes"})

        path = Path(server.RECORDS_DIR) / f"{r1['uuid']}.json"
        record = json.loads(path.read_text())
        assert any(c["to_uuid"] == r2["uuid"] for c in record.get("connections", []))

    def test_connect_invalid_relationship(self):
        r1 = json.loads(server._handle_store({"content": "A", "type": "note", "project": "t"})[0].text)
        r2 = json.loads(server._handle_store({"content": "B", "type": "note", "project": "t"})[0].text)
        result = server._handle_connect({
            "from_uuid": r1["uuid"], "to_uuid": r2["uuid"], "relationship": "bad_rel",
        })[0].text
        assert "Error" in result


class TestExplore:
    def test_explore_basic(self):
        r1 = json.loads(server._handle_store({"content": "Start", "type": "note", "project": "t"})[0].text)
        r2 = json.loads(server._handle_store({
            "content": "Connected", "type": "note", "project": "t",
            "connections": [{"to_uuid": r1["uuid"], "relationship": "related_to"}],
        })[0].text)
        result = json.loads(server._handle_explore({"uuid": r2["uuid"], "depth": 1})[0].text)
        assert len(result["nodes"]) == 2
        assert len(result["edges"]) >= 1


class TestDelete:
    def test_delete_basic(self, tmp_memory_dir):
        r = json.loads(server._handle_store({"content": "Delete me", "type": "note", "project": "t"})[0].text)
        uuid = r["uuid"]
        path = tmp_memory_dir / "records" / f"{uuid}.json"
        assert path.exists()

        result = json.loads(server._handle_delete({"uuid": uuid})[0].text)
        assert result["status"] == "deleted"
        assert not path.exists()

    def test_delete_not_found(self):
        result = server._handle_delete({"uuid": "nonexistent" * 2})[0].text
        assert "Error" in result

    def test_delete_removes_connections(self):
        r1 = json.loads(server._handle_store({"content": "A", "type": "note", "project": "t"})[0].text)
        r2 = json.loads(server._handle_store({
            "content": "B", "type": "note", "project": "t",
            "connections": [{"to_uuid": r1["uuid"], "relationship": "related_to"}],
        })[0].text)
        server._handle_delete({"uuid": r2["uuid"]})

        # r1 should have no incoming connections
        result = json.loads(server._handle_get({"uuid": r1["uuid"]})[0].text)
        assert len(result["connections"]["incoming"]) == 0


class TestSync:
    def test_sync_imports_new_file(self, tmp_memory_dir):
        """A JSON file added externally should be imported on sync."""
        uuid = os.urandom(16).hex()
        record = {
            "schema_version": 1,
            "uuid": uuid,
            "type": "note",
            "content": "Externally added",
            "project": "sync_test",
            "importance": 5,
            "created_at": "2026-03-10T12:00:00",
            "connections": [],
        }
        (tmp_memory_dir / "records" / f"{uuid}.json").write_text(json.dumps(record))

        server.sync_from_files()

        result = json.loads(server._handle_get({"uuid": uuid})[0].text)
        assert result["content"] == "Externally added"

    def test_sync_removes_deleted_file(self, tmp_memory_dir):
        """A record whose file was deleted should be removed from DB."""
        r = json.loads(server._handle_store({"content": "Will be deleted", "type": "note", "project": "t"})[0].text)
        uuid = r["uuid"]

        # Delete the file but not the DB entry
        (tmp_memory_dir / "records" / f"{uuid}.json").unlink()

        server.sync_from_files()

        result = server._handle_get({"uuid": uuid})[0].text
        assert "Error" in result

    def test_full_rebuild(self, tmp_memory_dir):
        """DB can be deleted and rebuilt from files."""
        server._handle_store({"content": "Survive rebuild", "type": "note", "project": "t"})
        server._handle_store({"content": "Also survive", "type": "note", "project": "t"})

        server.full_rebuild()

        result = json.loads(server._handle_recent({"limit": 10})[0].text)
        contents = [r["content"] for r in result]
        assert "Survive rebuild" in contents
        assert "Also survive" in contents
