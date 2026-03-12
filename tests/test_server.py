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


class TestNarrativeUniqueness:
    """Narratives should be one-per-project. Storing a new narrative for the
    same project must supersede the old one, not create duplicates."""

    def test_second_narrative_supersedes_first(self):
        """Storing a new narrative for the same project should auto-supersede the old one."""
        r1 = json.loads(server._handle_store({
            "content": "# V1 narrative\nOriginal project story.",
            "type": "narrative",
            "project": "myproj",
            "importance": 10,
        })[0].text)
        assert r1["status"] == "stored"

        r2 = json.loads(server._handle_store({
            "content": "# V2 narrative\nUpdated project story with new sessions.",
            "type": "narrative",
            "project": "myproj",
            "importance": 10,
        })[0].text)
        assert r2["status"] == "stored"

        # The new narrative should have a supersedes connection to the old one
        result = json.loads(server._handle_get({"uuid": r2["uuid"]})[0].text)
        outgoing = result["connections"]["outgoing"]
        supersedes = [c for c in outgoing if c["relationship"] == "supersedes"]
        assert len(supersedes) == 1, (
            f"New narrative should auto-supersede the old one, but has {len(supersedes)} supersedes connections"
        )
        assert supersedes[0]["to_uuid"] == r1["uuid"]

    def test_only_one_active_narrative_per_project(self):
        """Querying narratives for a project should return only the latest (non-superseded) one."""
        server._handle_store({
            "content": "# V1\nFirst version.",
            "type": "narrative",
            "project": "singleproj",
            "importance": 10,
        })
        server._handle_store({
            "content": "# V2\nSecond version.",
            "type": "narrative",
            "project": "singleproj",
            "importance": 10,
        })
        server._handle_store({
            "content": "# V3\nThird version.",
            "type": "narrative",
            "project": "singleproj",
            "importance": 10,
        })

        # When asking for recent narratives for this project, should get exactly 1
        result = json.loads(server._handle_recent({
            "project": "singleproj",
            "type": "narrative",
        })[0].text)
        assert len(result) == 1, (
            f"Expected exactly 1 active narrative for project, got {len(result)}. "
            f"Old narratives should be removed or excluded after being superseded."
        )
        assert "V3" in result[0]["content"]

    def test_narrative_supersedes_connection_in_json_file(self, tmp_memory_dir):
        """The supersedes connection should also be written to the JSON record file."""
        r1 = json.loads(server._handle_store({
            "content": "# Old narrative",
            "type": "narrative",
            "project": "fileproj",
        })[0].text)

        r2 = json.loads(server._handle_store({
            "content": "# New narrative",
            "type": "narrative",
            "project": "fileproj",
        })[0].text)

        # Check the JSON file for the new narrative has the supersedes connection
        path = tmp_memory_dir / "records" / f"{r2['uuid']}.json"
        record = json.loads(path.read_text())
        supersedes = [
            c for c in record.get("connections", [])
            if c["relationship"] == "supersedes"
        ]
        assert len(supersedes) == 1, (
            f"JSON file should contain supersedes connection, but has {len(supersedes)}"
        )
        assert supersedes[0]["to_uuid"] == r1["uuid"]

    def test_old_narrative_deleted_after_superseded(self):
        """Once superseded, the old narrative record should be deleted (not just connected)."""
        r1 = json.loads(server._handle_store({
            "content": "# Obsolete narrative",
            "type": "narrative",
            "project": "cleanproj",
            "importance": 10,
        })[0].text)

        server._handle_store({
            "content": "# Current narrative",
            "type": "narrative",
            "project": "cleanproj",
            "importance": 10,
        })

        # The old narrative should no longer exist
        old = server._handle_get({"uuid": r1["uuid"]})[0].text
        assert "Error" in old, (
            "Old narrative should be deleted after being superseded, but it still exists"
        )

    def test_narrative_across_different_projects_independent(self):
        """Narratives for different projects should not interfere with each other."""
        r1 = json.loads(server._handle_store({
            "content": "# Project A narrative",
            "type": "narrative",
            "project": "proj_a",
        })[0].text)

        r2 = json.loads(server._handle_store({
            "content": "# Project B narrative",
            "type": "narrative",
            "project": "proj_b",
        })[0].text)

        # Both should exist independently
        a = json.loads(server._handle_get({"uuid": r1["uuid"]})[0].text)
        b = json.loads(server._handle_get({"uuid": r2["uuid"]})[0].text)
        assert a["content"] == "# Project A narrative"
        assert b["content"] == "# Project B narrative"

        # Each project should have exactly 1 narrative
        result_a = json.loads(server._handle_recent({"project": "proj_a", "type": "narrative"})[0].text)
        result_b = json.loads(server._handle_recent({"project": "proj_b", "type": "narrative"})[0].text)
        assert len(result_a) == 1
        assert len(result_b) == 1


class TestNarrativeRequiresProject:
    """Narratives must have a non-empty project. A narrative without a project
    is useless — it can't be looked up or uniquely enforced."""

    def test_narrative_without_project_rejected(self):
        """Storing a narrative with no project should fail."""
        result = server._handle_store({
            "content": "# Orphan narrative\nNo project specified.",
            "type": "narrative",
            "importance": 10,
        })[0].text
        assert "Error" in result or "project" in result.lower(), (
            f"Narrative without a project should be rejected, but got: {result}"
        )

    def test_narrative_with_empty_project_rejected(self):
        """Storing a narrative with empty string project should fail."""
        result = server._handle_store({
            "content": "# Empty project narrative",
            "type": "narrative",
            "project": "",
            "importance": 10,
        })[0].text
        assert "Error" in result or "project" in result.lower(), (
            f"Narrative with empty project should be rejected, but got: {result}"
        )

    def test_note_without_project_allowed(self):
        """Notes without a project should still be allowed (they're atomic facts)."""
        result = json.loads(server._handle_store({
            "content": "A general note without a project.",
            "type": "note",
        })[0].text)
        assert result["status"] == "stored"


class TestNarrativeSurvivesRebuild:
    """After full_rebuild(), narrative uniqueness should still hold —
    exactly one narrative per project."""

    def test_rebuild_with_multiple_narrative_files_keeps_latest(self, tmp_memory_dir):
        """If multiple narrative JSON files exist for the same project,
        rebuild should only keep the most recent one."""
        records_dir = tmp_memory_dir / "records"

        # Create two narrative files for the same project with different timestamps
        old_uuid = os.urandom(16).hex()
        new_uuid = os.urandom(16).hex()

        for uuid, content, created_at in [
            (old_uuid, "# V1 old narrative", "2026-03-08T10:00:00"),
            (new_uuid, "# V2 new narrative", "2026-03-10T10:00:00"),
        ]:
            record = {
                "schema_version": 1,
                "uuid": uuid,
                "type": "narrative",
                "content": content,
                "project": "rebuildproj",
                "importance": 10,
                "created_at": created_at,
                "connections": [],
            }
            (records_dir / f"{uuid}.json").write_text(json.dumps(record))

        server.full_rebuild()

        result = json.loads(server._handle_recent({
            "project": "rebuildproj",
            "type": "narrative",
        })[0].text)
        assert len(result) == 1, (
            f"After rebuild with 2 narrative files for same project, "
            f"expected 1 active narrative, got {len(result)}"
        )
        assert "V2" in result[0]["content"]


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


class TestNarrativeCoverage:
    def test_no_narrative_returns_unprocessed(self, tmp_path):
        """When no narrative exists, all on-disk files are unprocessed."""
        # Create a fake project dir with transcripts (under .claude/projects/)
        proj_dir = tmp_path / ".claude" / "projects" / "-home-scott-projects-testproj"
        proj_dir.mkdir(parents=True)
        (proj_dir / "session1.jsonl").write_text("{}")
        (proj_dir / "session2.jsonl").write_text("{}")
        sub_dir = proj_dir / "subagents"
        sub_dir.mkdir()
        (sub_dir / "agent1.jsonl").write_text("{}")

        with patch("server.Path.home", return_value=tmp_path):
            result = json.loads(
                server._handle_narrative_coverage({"project": "testproj"})[0].text
            )

        assert result["status"] == "no_narrative"
        assert result["narrative_uuid"] is None
        assert len(result["unprocessed"]) == 3

    def test_all_processed(self, tmp_path):
        """When transcript_ref covers all files, nothing is unprocessed."""
        proj_dir = tmp_path / ".claude" / "projects" / "-home-scott-projects-myproj"
        proj_dir.mkdir(parents=True)
        f1 = proj_dir / "abc.jsonl"
        f1.write_text("{}")
        f2 = proj_dir / "def.jsonl"
        f2.write_text("{}")

        # Store a narrative with transcript_ref as JSON array
        transcript_ref = json.dumps([str(f1), str(f2)])
        r = json.loads(server._handle_store({
            "content": "# My narrative",
            "type": "narrative",
            "project": "myproj",
            "transcript_ref": [str(f1), str(f2)],
        })[0].text)

        with patch("server.Path.home", return_value=tmp_path):
            result = json.loads(
                server._handle_narrative_coverage({"project": "myproj"})[0].text
            )

        assert result["unprocessed_count"] == 0
        assert "All" in result["summary"]

    def test_partial_coverage(self, tmp_path):
        """Unprocessed files are correctly identified."""
        proj_dir = tmp_path / ".claude" / "projects" / "-home-scott-projects-partial"
        proj_dir.mkdir(parents=True)
        f1 = proj_dir / "done.jsonl"
        f1.write_text("{}")
        f2 = proj_dir / "new.jsonl"
        f2.write_text("{}")
        sub_dir = proj_dir / "subagents"
        sub_dir.mkdir()
        f3 = sub_dir / "agent.jsonl"
        f3.write_text("{}")

        r = json.loads(server._handle_store({
            "content": "# Partial narrative",
            "type": "narrative",
            "project": "partial",
            "transcript_ref": [str(f1)],
        })[0].text)

        with patch("server.Path.home", return_value=tmp_path):
            result = json.loads(
                server._handle_narrative_coverage({"project": "partial"})[0].text
            )

        assert result["unprocessed_count"] == 2
        assert result["on_disk_count"] == 3
        assert result["processed_count"] == 1

    def test_transcript_ref_list_stored_as_json(self, tmp_memory_dir):
        """When transcript_ref is passed as a list, it's stored as JSON string."""
        r = json.loads(server._handle_store({
            "content": "# List ref test",
            "type": "narrative",
            "project": "listref",
            "transcript_ref": ["/path/a.jsonl", "/path/b.jsonl"],
        })[0].text)

        # Check the stored record file
        path = tmp_memory_dir / "records" / f"{r['uuid']}.json"
        record = json.loads(path.read_text())
        ref = record["transcript_ref"]
        assert isinstance(ref, str)
        parsed = json.loads(ref)
        assert isinstance(parsed, list)
        assert len(parsed) == 2

    def test_legacy_string_transcript_ref(self, tmp_path):
        """Legacy freeform string transcript_ref is still parsed."""
        proj_dir = tmp_path / ".claude" / "projects" / "-home-scott-projects-legacy"
        proj_dir.mkdir(parents=True)
        f1 = proj_dir / "abc.jsonl"
        f1.write_text("{}")

        r = json.loads(server._handle_store({
            "content": "# Legacy narrative",
            "type": "narrative",
            "project": "legacy",
            "transcript_ref": str(f1),
        })[0].text)

        with patch("server.Path.home", return_value=tmp_path):
            result = json.loads(
                server._handle_narrative_coverage({"project": "legacy"})[0].text
            )

        assert result["unprocessed_count"] == 0
