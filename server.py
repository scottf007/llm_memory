"""
MCP server for persistent Claude Code memory.

Provides tools for storing, searching, connecting, and exploring memories
backed by SQLite with FTS5 full-text search, with JSON file sync support
for cross-machine synchronization via Syncthing.

Files are the source of truth. The SQLite database is a derived, ephemeral index.
You can delete memory.db at any time and rebuild it from the records directory.

Memory types:
  - narrative: Per-project living document (the project's full story)
  - note: Atomic fact, decision, correction, preference, or insight
  - session_log: Lightweight record that a session happened

Usage:
    python server.py
    python server.py --rebuild   # Delete DB and rebuild from files
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DB_DIR = Path.home() / ".claude" / "memory"
DB_PATH = DB_DIR / "memory.db"
RECORDS_DIR = DB_DIR / "records"

VALID_TYPES = {"narrative", "note", "session_log"}
VALID_RELATIONSHIPS = {"supersedes", "related_to"}

SCHEMA = """
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
"""

# ---------------------------------------------------------------------------
# UUID helper
# ---------------------------------------------------------------------------


def generate_uuid() -> str:
    """Generate a 32-char lowercase hex UUID."""
    return os.urandom(16).hex()


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------


def write_record_file(record: dict) -> Path:
    """Write a memory record as a JSON file. File-first, then DB."""
    RECORDS_DIR.mkdir(parents=True, exist_ok=True)
    path = RECORDS_DIR / f"{record['uuid']}.json"
    path.write_text(json.dumps(record, indent=2))
    return path


def read_record_file(uuid: str) -> dict | None:
    """Read a memory record from its JSON file."""
    path = RECORDS_DIR / f"{uuid}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def delete_record_file(uuid: str) -> bool:
    """Delete a memory record's JSON file. Returns True if file existed."""
    path = RECORDS_DIR / f"{uuid}.json"
    if path.exists():
        path.unlink()
        return True
    return False


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def get_db() -> sqlite3.Connection:
    """Return a connection to the SQLite database, creating it if needed."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Create tables and triggers if they don't already exist.

    Detects old integer-ID schema and migrates to UUID-based schema.
    """
    DB_DIR.mkdir(parents=True, exist_ok=True)

    # Check if we need migration from old schema
    if DB_PATH.exists():
        conn = get_db()
        try:
            columns = []
            try:
                columns = [row[1] for row in conn.execute("PRAGMA table_info(memories)").fetchall()]
            except Exception:
                pass

            if columns and "id" in columns and "uuid" not in columns:
                # Old integer-ID schema detected — migrate
                conn.close()
                _migrate_v1_to_v2()
                return

            if columns and "uuid" in columns:
                # Already on new schema — ensure status column exists (v2 -> v2.1 migration)
                if "status" not in columns:
                    conn.execute("ALTER TABLE memories ADD COLUMN status TEXT DEFAULT 'active'")
                    conn.execute("UPDATE memories SET status = 'active' WHERE status IS NULL")
                    conn.commit()
                conn.close()
                return
        except Exception:
            conn.close()
            raise

    # Fresh database — create new schema
    conn = get_db()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()

    # Create .stignore for Syncthing
    _ensure_stignore()


def _ensure_stignore() -> None:
    """Create .stignore file to prevent Syncthing from syncing the DB."""
    stignore_path = DB_DIR / ".stignore"
    if not stignore_path.exists():
        stignore_path.write_text("memory.db\nmemory.db-wal\nmemory.db-shm\n")


def _migrate_v1_to_v2() -> None:
    """Migrate existing integer-ID memories to UUID-based file records."""
    RECORDS_DIR.mkdir(parents=True, exist_ok=True)

    conn = get_db()
    try:
        # Handle older v1 schemas that might be missing columns
        columns = [row[1] for row in conn.execute("PRAGMA table_info(memories)").fetchall()]
        if "transcript_ref" not in columns:
            conn.execute("ALTER TABLE memories ADD COLUMN transcript_ref TEXT")
        if "tags" not in columns:
            conn.execute("ALTER TABLE memories ADD COLUMN tags TEXT")

        # Migrate old types before reading
        conn.execute("UPDATE memories SET type = 'note' WHERE type IN ('decision', 'insight', 'progress', 'correction')")
        conn.execute("UPDATE memories SET type = 'narrative' WHERE type = 'project_narrative'")
        conn.execute("UPDATE memories SET type = 'narrative' WHERE type = 'note' AND content LIKE '[PROJECT NARRATIVE]%'")
        conn.execute("UPDATE memories SET type = 'session_log' WHERE type = 'session_summary'")
        conn.execute("UPDATE memories SET type = 'note' WHERE type = 'chunk_summary'")
        conn.commit()

        # Read all existing records
        rows = conn.execute(
            "SELECT id, type, content, project, session_id, created_at, "
            "importance, transcript_ref, tags FROM memories"
        ).fetchall()

        # Map old integer IDs to new UUIDs
        id_to_uuid = {}
        for row in rows:
            uuid = generate_uuid()
            id_to_uuid[row["id"]] = uuid

        # Read all connections
        connections = conn.execute(
            "SELECT from_id, to_id, relationship FROM connections"
        ).fetchall()

        # Build connection lookup: from_id -> [(to_id, relationship), ...]
        conn_lookup: dict[int, list[tuple[int, str]]] = {}
        for c in connections:
            conn_lookup.setdefault(c["from_id"], []).append(
                (c["to_id"], c["relationship"])
            )

        # Write JSON files
        for row in rows:
            old_id = row["id"]
            uuid = id_to_uuid[old_id]
            record = {
                "schema_version": 1,
                "uuid": uuid,
                "type": row["type"],
                "content": row["content"],
                "project": row["project"],
                "session_id": row["session_id"],
                "importance": row["importance"] or 5,
                "transcript_ref": row["transcript_ref"],
                "tags": row["tags"],
                "created_at": row["created_at"],
                "connections": [],
            }
            for to_id, rel in conn_lookup.get(old_id, []):
                if to_id in id_to_uuid:
                    record["connections"].append({
                        "to_uuid": id_to_uuid[to_id],
                        "relationship": rel,
                    })

            write_record_file(record)

        conn.close()

        # Rebuild DB from files with new schema
        full_rebuild()

    except Exception:
        conn.close()
        raise


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a sqlite3.Row to a plain dict."""
    return dict(row)


# ---------------------------------------------------------------------------
# Startup sync
# ---------------------------------------------------------------------------


def _import_record(conn: sqlite3.Connection, record: dict) -> None:
    """Insert a single record from a JSON file into the DB."""
    conn.execute(
        "INSERT OR IGNORE INTO memories (uuid, type, content, project, session_id, "
        "created_at, importance, transcript_ref, tags, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            record["uuid"], record["type"], record["content"],
            record.get("project"), record.get("session_id"),
            record.get("created_at"), record.get("importance", 5),
            record.get("transcript_ref"), record.get("tags"),
            record.get("status") or "active",
        ),
    )


def _rebuild_connections(conn: sqlite3.Connection, uuids: set, records_dir: Path) -> None:
    """Rebuild connection rows for newly imported records."""
    for uuid in uuids:
        path = records_dir / f"{uuid}.json"
        if not path.exists():
            continue
        try:
            record = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        for link in record.get("connections", []):
            to_uuid = link.get("to_uuid")
            relationship = link.get("relationship")
            if not to_uuid or not relationship:
                continue
            # Only insert if target exists in DB
            if conn.execute("SELECT 1 FROM memories WHERE uuid = ?", (to_uuid,)).fetchone():
                conn.execute(
                    "INSERT OR IGNORE INTO connections (from_uuid, to_uuid, relationship) "
                    "VALUES (?, ?, ?)",
                    (uuid, to_uuid, relationship),
                )


def _resolve_conflicts(records_dir: Path) -> None:
    """Merge any Syncthing conflict files."""
    for conflict_path in records_dir.glob("*.sync-conflict-*"):
        # Extract original UUID from conflict filename
        # Syncthing format: {uuid}.sync-conflict-{date}-{id}.json
        stem = conflict_path.stem
        original_uuid = stem.split(".sync-conflict")[0]
        original_path = records_dir / f"{original_uuid}.json"

        if not original_path.exists():
            # Original was deleted; conflict file is stale
            conflict_path.unlink()
            continue

        try:
            original = json.loads(original_path.read_text())
            conflict = json.loads(conflict_path.read_text())
        except (json.JSONDecodeError, OSError):
            # Can't parse — remove conflict file, keep original
            conflict_path.unlink()
            continue

        # Merge connections (union)
        existing = {(c["to_uuid"], c["relationship"]) for c in original.get("connections", [])}
        for c in conflict.get("connections", []):
            key = (c["to_uuid"], c["relationship"])
            if key not in existing:
                original.setdefault("connections", []).append(c)

        # Keep the more recent content if different
        if conflict.get("created_at", "") > original.get("created_at", ""):
            original["content"] = conflict["content"]

        original_path.write_text(json.dumps(original, indent=2))
        conflict_path.unlink()


def sync_from_files() -> None:
    """Reconcile records/ directory with SQLite DB."""
    RECORDS_DIR.mkdir(parents=True, exist_ok=True)

    # Resolve Syncthing conflicts first
    _resolve_conflicts(RECORDS_DIR)

    conn = get_db()
    try:
        # 1. Get all UUIDs currently in DB
        db_uuids = set(
            row[0] for row in conn.execute("SELECT uuid FROM memories").fetchall()
        )

        # 2. Get all UUIDs from files on disk
        file_uuids = set()
        for path in RECORDS_DIR.glob("*.json"):
            file_uuids.add(path.stem)

        # 3. Files present on disk but missing from DB → import
        to_import = file_uuids - db_uuids
        for uuid in to_import:
            path = RECORDS_DIR / f"{uuid}.json"
            try:
                record = json.loads(path.read_text())
                _import_record(conn, record)
            except (json.JSONDecodeError, OSError, KeyError):
                # Skip corrupt/unreadable files
                continue

        # 4. Records in DB but file missing from disk → delete from DB
        to_remove = db_uuids - file_uuids
        for uuid in to_remove:
            conn.execute("DELETE FROM connections WHERE from_uuid = ? OR to_uuid = ?", (uuid, uuid))
            conn.execute("DELETE FROM memories WHERE uuid = ?", (uuid,))

        # 5. Rebuild connections for imported records
        if to_import:
            _rebuild_connections(conn, to_import, RECORDS_DIR)

        conn.commit()
    finally:
        conn.close()


def full_rebuild() -> None:
    """Delete and rebuild memory.db entirely from records/ files."""
    if DB_PATH.exists():
        DB_PATH.unlink()
    # Also delete WAL and SHM files
    for suffix in ("-wal", "-shm"):
        p = DB_PATH.parent / (DB_PATH.name + suffix)
        if p.exists():
            p.unlink()

    init_db()  # Creates fresh schema

    conn = get_db()
    try:
        # Pass 1: Import all records
        for path in sorted(RECORDS_DIR.glob("*.json")):
            try:
                record = json.loads(path.read_text())
                _import_record(conn, record)
            except (json.JSONDecodeError, OSError, KeyError):
                continue

        # Pass 2: Deduplicate narratives — keep only the latest active per project,
        # archive older ones (do not delete; preserves recoverable history).
        # Already-archived records are skipped so they stay out of the "most recent"
        # selection and keep their status.
        narrative_rows = conn.execute(
            "SELECT uuid, project, created_at, status FROM memories "
            "WHERE type = 'narrative' AND project IS NOT NULL "
            "ORDER BY project, created_at DESC"
        ).fetchall()
        seen_projects: set[str] = set()
        newest_active_per_project: dict[str, str] = {}
        for row in narrative_rows:
            proj = row["project"]
            # Skip records already archived — leave them alone
            if row["status"] == "archived":
                continue
            if proj in seen_projects:
                # Older active duplicate — archive it
                old_uuid = row["uuid"]
                new_uuid = newest_active_per_project.get(proj)
                old_record = read_record_file(old_uuid)
                if old_record is not None:
                    old_record["status"] = "archived"
                    if new_uuid:
                        old_record["archived_in"] = new_uuid
                    write_record_file(old_record)
                conn.execute(
                    "UPDATE memories SET status = 'archived' WHERE uuid = ?",
                    (old_uuid,),
                )
            else:
                seen_projects.add(proj)
                newest_active_per_project[proj] = row["uuid"]

        # Pass 3: Import all connections (all records now exist)
        for path in RECORDS_DIR.glob("*.json"):
            try:
                record = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            for link in record.get("connections", []):
                to_uuid = link.get("to_uuid")
                relationship = link.get("relationship")
                if not to_uuid or not relationship:
                    continue
                if conn.execute("SELECT 1 FROM memories WHERE uuid = ?", (to_uuid,)).fetchone():
                    conn.execute(
                        "INSERT OR IGNORE INTO connections (from_uuid, to_uuid, relationship) "
                        "VALUES (?, ?, ?)",
                        (record["uuid"], to_uuid, relationship),
                    )

        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

app = Server("llm-memory")


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="memory_store",
            description="Store a memory. Types: 'narrative' (per-project living document), 'note' (atomic fact/decision/correction), 'session_log' (session record).",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The memory content",
                    },
                    "type": {
                        "type": "string",
                        "description": "One of: narrative, note, session_log",
                        "enum": list(VALID_TYPES),
                    },
                    "project": {
                        "type": "string",
                        "description": "Project name, e.g. 'finance_nexus'",
                    },
                    "session_id": {
                        "type": "string",
                        "description": "Current session ID",
                    },
                    "importance": {
                        "type": "integer",
                        "description": "1-10 importance scale (default 5)",
                        "minimum": 1,
                        "maximum": 10,
                        "default": 5,
                    },
                    "transcript_ref": {
                        "type": ["string", "array"],
                        "description": "Transcript files processed to create this memory. "
                        "Prefer a JSON array of full paths, e.g. "
                        "['~/.claude/projects/-home-scott-projects-brine/abc123.jsonl']. "
                        "Legacy string format also accepted.",
                        "items": {"type": "string"},
                    },
                    "tags": {
                        "type": "string",
                        "description": "Comma-separated tags for searchability, e.g. 'correction, mcp-config'",
                    },
                    "connections": {
                        "type": "array",
                        "description": "Connections to existing memories",
                        "items": {
                            "type": "object",
                            "properties": {
                                "to_uuid": {"type": "string"},
                                "relationship": {
                                    "type": "string",
                                    "enum": list(VALID_RELATIONSHIPS),
                                },
                            },
                            "required": ["to_uuid", "relationship"],
                        },
                    },
                },
                "required": ["content", "type"],
            },
        ),
        types.Tool(
            name="memory_search",
            description="Full-text search across all memories. Returns snippets for narratives, full content for notes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query",
                    },
                    "project": {
                        "type": "string",
                        "description": "Filter by project",
                    },
                    "type": {
                        "type": "string",
                        "description": "Filter by type",
                        "enum": list(VALID_TYPES),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 10)",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="memory_recent",
            description="Get most recent memories.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": "Filter by project",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 10)",
                        "default": 10,
                    },
                    "type": {
                        "type": "string",
                        "description": "Filter by type",
                        "enum": list(VALID_TYPES),
                    },
                },
                "required": [],
            },
        ),
        types.Tool(
            name="memory_get",
            description="Get a specific memory by UUID with its connections.",
            inputSchema={
                "type": "object",
                "properties": {
                    "uuid": {
                        "type": "string",
                        "description": "Memory UUID (32-char hex string)",
                    },
                },
                "required": ["uuid"],
            },
        ),
        types.Tool(
            name="memory_connect",
            description="Create a connection between two memories. Use 'supersedes' for narrative versions, 'related_to' for linked notes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "from_uuid": {
                        "type": "string",
                        "description": "Source memory UUID",
                    },
                    "to_uuid": {
                        "type": "string",
                        "description": "Target memory UUID",
                    },
                    "relationship": {
                        "type": "string",
                        "description": "One of: supersedes, related_to",
                        "enum": list(VALID_RELATIONSHIPS),
                    },
                },
                "required": ["from_uuid", "to_uuid", "relationship"],
            },
        ),
        types.Tool(
            name="memory_explore",
            description="Explore connections from a starting memory.",
            inputSchema={
                "type": "object",
                "properties": {
                    "uuid": {
                        "type": "string",
                        "description": "Starting memory UUID",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "How many hops to traverse (max 3, default 1)",
                        "minimum": 1,
                        "maximum": 3,
                        "default": 1,
                    },
                },
                "required": ["uuid"],
            },
        ),
        types.Tool(
            name="narrative_coverage",
            description="Check which transcript files have been processed into a project's narrative. "
            "Compares on-disk .jsonl files against the narrative's transcript_ref field.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": "Project name, e.g. 'cricket_manager'",
                    },
                },
                "required": ["project"],
            },
        ),
        types.Tool(
            name="memory_delete",
            description="Delete a memory and its connections.",
            inputSchema={
                "type": "object",
                "properties": {
                    "uuid": {
                        "type": "string",
                        "description": "Memory UUID to delete",
                    },
                },
                "required": ["uuid"],
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _text(content: str) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=content)]


def _error(message: str) -> list[types.TextContent]:
    return _text(f"Error: {message}")


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    try:
        handlers = {
            "memory_store": _handle_store,
            "memory_search": _handle_search,
            "memory_recent": _handle_recent,
            "memory_get": _handle_get,
            "memory_connect": _handle_connect,
            "memory_explore": _handle_explore,
            "memory_delete": _handle_delete,
            "narrative_coverage": _handle_narrative_coverage,
        }
        handler = handlers.get(name)
        if not handler:
            return _error(f"Unknown tool: {name}")
        return handler(arguments)
    except Exception as exc:
        return _error(str(exc))


# -- memory_store ----------------------------------------------------------

def _handle_store(args: dict[str, Any]) -> list[types.TextContent]:
    content = args.get("content", "").strip()
    mem_type = args.get("type", "").strip()
    project = args.get("project")
    session_id = args.get("session_id")
    importance = args.get("importance", 5)
    transcript_ref = args.get("transcript_ref")
    tags = args.get("tags")
    connections = args.get("connections", [])

    # Normalize transcript_ref: store as JSON string whether input is list or string
    if isinstance(transcript_ref, list):
        transcript_ref = json.dumps(transcript_ref)

    if not content:
        return _error("content is required")
    if mem_type not in VALID_TYPES:
        return _error(f"type must be one of: {', '.join(sorted(VALID_TYPES))}")
    if not (1 <= importance <= 10):
        return _error("importance must be between 1 and 10")

    # Narratives require a non-empty project
    if mem_type == "narrative" and not (project and project.strip()):
        return _error("Narratives require a non-empty project")

    conn = get_db()
    try:
        # Deduplication: skip if similar content stored in the last hour
        # (skip for narratives — they're meant to be updated)
        if mem_type != "narrative":
            existing = conn.execute(
                "SELECT uuid FROM memories WHERE substr(content, 1, 100) = substr(?, 1, 100) "
                "AND created_at > datetime('now', '-1 hour')",
                (content,),
            ).fetchone()
            if existing:
                return _text(json.dumps({
                    "uuid": existing["uuid"],
                    "status": "duplicate_skipped",
                    "message": "Similar memory already stored recently"
                }, indent=2))

        # Narrative uniqueness: find any existing active narrative for this project.
        # The old one will be archived (status='archived') after the new one is stored,
        # rather than deleted — this preserves recoverable history in case the new
        # narrative was generated as a broken/skeleton record.
        old_narrative_uuid = None
        if mem_type == "narrative":
            old_row = conn.execute(
                "SELECT uuid FROM memories WHERE type = 'narrative' AND project = ? "
                "AND (status IS NULL OR status = 'active')",
                (project,),
            ).fetchone()
            if old_row:
                old_narrative_uuid = old_row["uuid"]

        uuid = generate_uuid()
        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

        # Build the record dict
        record = {
            "schema_version": 1,
            "uuid": uuid,
            "type": mem_type,
            "content": content,
            "project": project,
            "session_id": session_id,
            "importance": importance,
            "transcript_ref": transcript_ref,
            "tags": tags,
            "created_at": created_at,
            "connections": [],
        }

        # If superseding an old narrative, record it in the new record's connections
        if old_narrative_uuid:
            record["connections"].append({
                "to_uuid": old_narrative_uuid,
                "relationship": "supersedes",
            })

        # Process connections for the record file
        for link in connections:
            to_uuid = link.get("to_uuid")
            relationship = link.get("relationship", "")
            if relationship not in VALID_RELATIONSHIPS:
                continue
            if to_uuid:
                target = conn.execute("SELECT uuid FROM memories WHERE uuid = ?", (to_uuid,)).fetchone()
                if target:
                    record["connections"].append({
                        "to_uuid": to_uuid,
                        "relationship": relationship,
                    })

        # Archive the old narrative (file + DB) before storing the new one.
        # Archiving (vs deleting) preserves recoverable history — if the new
        # narrative is a broken/skeleton record, the old content is still on disk
        # and in the DB, just hidden from default queries.
        if old_narrative_uuid:
            old_record = read_record_file(old_narrative_uuid)
            if old_record is not None:
                old_record["status"] = "archived"
                old_record["archived_in"] = uuid
                write_record_file(old_record)
            conn.execute(
                "UPDATE memories SET status = 'archived' WHERE uuid = ?",
                (old_narrative_uuid,),
            )

        # Write file FIRST (source of truth), then DB
        record["status"] = "active"
        write_record_file(record)

        conn.execute(
            "INSERT INTO memories (uuid, type, content, project, session_id, "
            "created_at, importance, transcript_ref, tags, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (uuid, mem_type, content, project, session_id, created_at, importance, transcript_ref, tags, "active"),
        )

        # Insert connections into DB (only for targets that still exist)
        for link in record["connections"]:
            if conn.execute("SELECT 1 FROM memories WHERE uuid = ?", (link["to_uuid"],)).fetchone():
                conn.execute(
                    "INSERT OR IGNORE INTO connections (from_uuid, to_uuid, relationship) "
                    "VALUES (?, ?, ?)",
                    (uuid, link["to_uuid"], link["relationship"]),
                )

        conn.commit()
        return _text(json.dumps({"uuid": uuid, "status": "stored"}, indent=2))
    finally:
        conn.close()


# -- memory_search ---------------------------------------------------------

def _handle_search(args: dict[str, Any]) -> list[types.TextContent]:
    query = args.get("query", "").strip()
    project = args.get("project")
    mem_type = args.get("type")
    limit = min(args.get("limit", 10), 100)

    if not query:
        return _error("query is required")

    fts_query = " ".join(f'"{token}"' for token in query.split() if token)

    conn = get_db()
    try:
        sql = (
            "SELECT m.uuid, m.type, m.content, m.project, m.created_at, "
            "m.importance, m.transcript_ref, m.tags "
            "FROM memories_fts f "
            "JOIN memories m ON m.rowid = f.rowid "
            "WHERE memories_fts MATCH ? "
            "AND (m.status IS NULL OR m.status != 'archived') "
        )
        params: list[Any] = [fts_query]

        if project:
            sql += "AND m.project = ? "
            params.append(project)
        if mem_type:
            sql += "AND m.type = ? "
            params.append(mem_type)

        sql += "ORDER BY rank LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        results = []
        for row in rows:
            d = row_to_dict(row)
            # For notes/session_logs: return full content (they're short)
            # For narratives: truncate to 500 chars in search results
            # (use memory_get for full content)
            if d["type"] == "narrative" and len(d["content"]) > 500:
                d["content"] = d["content"][:500] + "...\n[Use memory_get for full narrative]"
            results.append(d)

        return _text(json.dumps(results, indent=2))
    finally:
        conn.close()


# -- memory_recent ---------------------------------------------------------

def _handle_recent(args: dict[str, Any]) -> list[types.TextContent]:
    project = args.get("project")
    mem_type = args.get("type")
    limit = min(args.get("limit", 10), 100)

    conn = get_db()
    try:
        sql = (
            "SELECT uuid, type, content, project, session_id, created_at, "
            "importance, transcript_ref, tags FROM memories "
            "WHERE (status IS NULL OR status != 'archived') "
        )
        params: list[Any] = []

        if project:
            sql += "AND project = ? "
            params.append(project)
        if mem_type:
            sql += "AND type = ? "
            params.append(mem_type)

        sql += "ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        results = []
        for row in rows:
            d = row_to_dict(row)
            # Truncate narratives in list view
            if d["type"] == "narrative" and len(d["content"]) > 500:
                d["content"] = d["content"][:500] + "...\n[Use memory_get for full narrative]"
            results.append(d)
        return _text(json.dumps(results, indent=2))
    finally:
        conn.close()


# -- memory_get ------------------------------------------------------------

def _handle_get(args: dict[str, Any]) -> list[types.TextContent]:
    uuid = args.get("uuid")
    if not uuid:
        return _error("uuid is required")

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT uuid, type, content, project, session_id, created_at, "
            "importance, transcript_ref, tags, status "
            "FROM memories WHERE uuid = ?",
            (uuid,),
        ).fetchone()

        if not row:
            return _error(f"Memory {uuid} not found")

        memory = row_to_dict(row)
        # Normalize status for existing rows that pre-date the column
        if memory.get("status") is None:
            memory["status"] = "active"

        outgoing = conn.execute(
            "SELECT c.to_uuid, c.relationship, c.created_at, "
            "m.type, substr(m.content, 1, 200) as content, m.project, m.importance "
            "FROM connections c "
            "JOIN memories m ON m.uuid = c.to_uuid "
            "WHERE c.from_uuid = ?",
            (uuid,),
        ).fetchall()

        incoming = conn.execute(
            "SELECT c.from_uuid, c.relationship, c.created_at, "
            "m.type, substr(m.content, 1, 200) as content, m.project, m.importance "
            "FROM connections c "
            "JOIN memories m ON m.uuid = c.from_uuid "
            "WHERE c.to_uuid = ?",
            (uuid,),
        ).fetchall()

        outgoing_list = [
            {"to_uuid": r["to_uuid"], "relationship": r["relationship"],
             "memory": {"type": r["type"], "content": r["content"], "project": r["project"]}}
            for r in outgoing
        ]

        # Also include connections from the JSON file that aren't in the DB
        # (e.g. supersedes connections to deleted narratives)
        db_outgoing_uuids = {r["to_uuid"] for r in outgoing}
        record_file = read_record_file(uuid)
        if record_file:
            for link in record_file.get("connections", []):
                if link.get("to_uuid") and link["to_uuid"] not in db_outgoing_uuids:
                    outgoing_list.append({
                        "to_uuid": link["to_uuid"],
                        "relationship": link["relationship"],
                        "memory": None,
                    })

        memory["connections"] = {
            "outgoing": outgoing_list,
            "incoming": [
                {"from_uuid": r["from_uuid"], "relationship": r["relationship"],
                 "memory": {"type": r["type"], "content": r["content"], "project": r["project"]}}
                for r in incoming
            ],
        }

        return _text(json.dumps(memory, indent=2))
    finally:
        conn.close()


# -- memory_connect --------------------------------------------------------

def _handle_connect(args: dict[str, Any]) -> list[types.TextContent]:
    from_uuid = args.get("from_uuid")
    to_uuid = args.get("to_uuid")
    relationship = args.get("relationship", "")

    if not from_uuid or not to_uuid:
        return _error("from_uuid and to_uuid are required")
    if relationship not in VALID_RELATIONSHIPS:
        return _error(f"relationship must be one of: {', '.join(sorted(VALID_RELATIONSHIPS))}")

    conn = get_db()
    try:
        for mid in (from_uuid, to_uuid):
            if not conn.execute("SELECT uuid FROM memories WHERE uuid = ?", (mid,)).fetchone():
                return _error(f"Memory {mid} not found")

        # Update the source record's JSON file
        record = read_record_file(from_uuid)
        if record:
            existing_conns = record.get("connections", [])
            already_exists = any(
                c["to_uuid"] == to_uuid and c["relationship"] == relationship
                for c in existing_conns
            )
            if not already_exists:
                existing_conns.append({
                    "to_uuid": to_uuid,
                    "relationship": relationship,
                })
                record["connections"] = existing_conns
                write_record_file(record)

        # Insert into DB
        conn.execute(
            "INSERT OR IGNORE INTO connections (from_uuid, to_uuid, relationship) VALUES (?, ?, ?)",
            (from_uuid, to_uuid, relationship),
        )
        conn.commit()
        return _text(json.dumps({
            "status": "connected", "from_uuid": from_uuid,
            "to_uuid": to_uuid, "relationship": relationship,
        }, indent=2))
    finally:
        conn.close()


# -- memory_explore --------------------------------------------------------

def _handle_explore(args: dict[str, Any]) -> list[types.TextContent]:
    start_uuid = args.get("uuid")
    depth = min(args.get("depth", 1), 3)

    if not start_uuid:
        return _error("uuid is required")

    conn = get_db()
    try:
        start_row = conn.execute(
            "SELECT uuid, type, substr(content, 1, 300) as content, project, created_at, importance "
            "FROM memories WHERE uuid = ?",
            (start_uuid,),
        ).fetchone()

        if not start_row:
            return _error(f"Memory {start_uuid} not found")

        visited: set[str] = set()
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []

        def traverse(memory_uuid: str, current_depth: int) -> None:
            if memory_uuid in visited or current_depth > depth:
                return
            visited.add(memory_uuid)

            row = conn.execute(
                "SELECT uuid, type, substr(content, 1, 300) as content, project, created_at, importance "
                "FROM memories WHERE uuid = ?",
                (memory_uuid,),
            ).fetchone()
            if not row:
                return
            nodes.append(row_to_dict(row))

            for direction, id_col, other_col in [
                ("out", "from_uuid", "to_uuid"),
                ("in", "to_uuid", "from_uuid"),
            ]:
                rows = conn.execute(
                    f"SELECT {other_col} as other_uuid, relationship FROM connections WHERE {id_col} = ?",
                    (memory_uuid,),
                ).fetchall()
                for c in rows:
                    edges.append({
                        "from_uuid": memory_uuid if direction == "out" else c["other_uuid"],
                        "to_uuid": c["other_uuid"] if direction == "out" else memory_uuid,
                        "relationship": c["relationship"],
                    })
                    traverse(c["other_uuid"], current_depth + 1)

        traverse(start_uuid, 0)

        seen_edges: set[tuple[str, str, str]] = set()
        unique_edges = []
        for e in edges:
            key = (e["from_uuid"], e["to_uuid"], e["relationship"])
            if key not in seen_edges:
                seen_edges.add(key)
                unique_edges.append(e)

        return _text(json.dumps({
            "start_uuid": start_uuid, "depth": depth,
            "nodes": nodes, "edges": unique_edges,
        }, indent=2))
    finally:
        conn.close()


# -- memory_delete ---------------------------------------------------------

def _handle_delete(args: dict[str, Any]) -> list[types.TextContent]:
    uuid = args.get("uuid")
    if not uuid:
        return _error("uuid is required")

    conn = get_db()
    try:
        row = conn.execute("SELECT uuid FROM memories WHERE uuid = ?", (uuid,)).fetchone()
        if not row:
            return _error(f"Memory {uuid} not found")

        # Delete file FIRST (source of truth), then DB
        delete_record_file(uuid)

        conn.execute("DELETE FROM connections WHERE from_uuid = ? OR to_uuid = ?", (uuid, uuid))
        conn.execute("DELETE FROM memories WHERE uuid = ?", (uuid,))
        conn.commit()
        return _text(json.dumps({"uuid": uuid, "status": "deleted"}, indent=2))
    finally:
        conn.close()


# -- narrative_coverage ----------------------------------------------------

def _find_project_transcripts(project: str) -> set[str]:
    """Find all .jsonl transcript files on disk for a project.

    Searches ~/.claude/projects/ directories, matching the project name
    against directory names (handling hyphens/underscores).
    """
    projects_dir = Path.home() / ".claude" / "projects"
    if not projects_dir.exists():
        return set()

    # Normalize for matching: lowercase, strip hyphens/underscores
    def normalize(name: str) -> str:
        return name.lower().replace("-", "").replace("_", "")

    target = normalize(project)
    found: set[str] = set()

    for proj_dir in projects_dir.iterdir():
        if not proj_dir.is_dir():
            continue
        # Extract project name from dir like "-home-scott-projects-cricket-manager"
        dir_name = proj_dir.name
        # Remove the path prefix (everything up to and including "projects-")
        parts = dir_name.split("projects-", 1)
        if len(parts) == 2:
            dir_project = parts[1]
        else:
            dir_project = dir_name

        if normalize(dir_project) != target:
            # Also handle "general" which maps to bare "-home-scott-projects"
            if not (project == "general" and dir_name.endswith("-projects") and not dir_project):
                continue

        # Collect main session transcripts
        for jsonl in proj_dir.glob("*.jsonl"):
            found.add(str(jsonl))

        # Collect subagent transcripts
        subagents_dir = proj_dir / "subagents"
        if subagents_dir.exists():
            for jsonl in subagents_dir.glob("*.jsonl"):
                found.add(str(jsonl))

    return found


def _parse_transcript_ref(transcript_ref: str | None) -> set[str]:
    """Parse transcript_ref field into a set of file paths.

    Handles both JSON array format and legacy freeform string format.
    """
    if not transcript_ref:
        return set()

    # Try JSON array first
    try:
        parsed = json.loads(transcript_ref)
        if isinstance(parsed, list):
            return {str(p) for p in parsed}
    except (json.JSONDecodeError, TypeError):
        pass

    # Legacy freeform: comma-separated or newline-separated paths
    refs: set[str] = set()
    for part in transcript_ref.replace("\n", ",").split(","):
        part = part.strip()
        if part:
            refs.add(part)
    return refs


def _handle_narrative_coverage(args: dict[str, Any]) -> list[types.TextContent]:
    project = args.get("project", "").strip()
    if not project:
        return _error("project is required")

    conn = get_db()
    try:
        # Find the current (active, non-archived) narrative for this project
        row = conn.execute(
            "SELECT uuid, transcript_ref, created_at FROM memories "
            "WHERE type = 'narrative' AND project = ? "
            "AND (status IS NULL OR status != 'archived') "
            "ORDER BY created_at DESC LIMIT 1",
            (project,),
        ).fetchone()

        if not row:
            on_disk = _find_project_transcripts(project)
            return _text(json.dumps({
                "project": project,
                "narrative_uuid": None,
                "status": "no_narrative",
                "on_disk": sorted(on_disk),
                "processed": [],
                "unprocessed": sorted(on_disk),
                "summary": f"No narrative exists. {len(on_disk)} transcript(s) on disk.",
            }, indent=2))

        processed = _parse_transcript_ref(row["transcript_ref"])
        on_disk = _find_project_transcripts(project)

        # Normalize paths for comparison (expand ~)
        def expand(p: str) -> str:
            return str(Path(p).expanduser())

        processed_expanded = {expand(p) for p in processed}
        on_disk_expanded = {expand(p) for p in on_disk}

        unprocessed = on_disk_expanded - processed_expanded
        # Also find processed refs that aren't on disk (stale refs)
        stale = processed_expanded - on_disk_expanded

        return _text(json.dumps({
            "project": project,
            "narrative_uuid": row["uuid"],
            "narrative_updated": row["created_at"],
            "on_disk_count": len(on_disk),
            "processed_count": len(processed),
            "unprocessed_count": len(unprocessed),
            "unprocessed": sorted(unprocessed),
            "stale_refs": sorted(stale) if stale else [],
            "summary": (
                f"{len(unprocessed)} unprocessed transcript(s) out of {len(on_disk)} on disk."
                if unprocessed else
                f"All {len(on_disk)} transcript(s) are covered by the narrative."
            ),
        }, indent=2))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    init_db()
    _ensure_stignore()
    sync_from_files()
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    import asyncio

    if "--rebuild" in sys.argv:
        print("Rebuilding memory.db from records/ files...")
        RECORDS_DIR.mkdir(parents=True, exist_ok=True)
        full_rebuild()
        print("Rebuild complete.")
    else:
        asyncio.run(main())
