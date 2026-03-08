"""
MCP server for persistent Claude Code memory.

Provides tools for storing, searching, connecting, and exploring memories
backed by SQLite with FTS5 full-text search. Designed to be spawned as a
subprocess by Claude Code.

Memory types:
  - narrative: Per-project living document (the project's full story)
  - note: Atomic fact, decision, correction, preference, or insight
  - session_log: Lightweight record that a session happened

Usage:
    python server.py
"""

import json
import os
import sqlite3
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

VALID_TYPES = {"narrative", "note", "session_log"}
VALID_RELATIONSHIPS = {"supersedes", "related_to"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, type, project, tags)
    VALUES (new.id, new.content, new.type, new.project, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, type, project, tags)
    VALUES('delete', old.id, old.content, old.type, old.project, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, type, project, tags)
    VALUES('delete', old.id, old.content, old.type, old.project, old.tags);
    INSERT INTO memories_fts(rowid, content, type, project, tags)
    VALUES (new.id, new.content, new.type, new.project, new.tags);
END;

CREATE TABLE IF NOT EXISTS connections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id INTEGER NOT NULL REFERENCES memories(id),
    to_id INTEGER NOT NULL REFERENCES memories(id),
    relationship TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(from_id, to_id, relationship)
);
"""

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

    Handles migration from v1 schema (adds tags column, rebuilds FTS
    with tags, migrates old types to new types).
    """
    conn = get_db()
    try:
        # Check existing columns before running schema
        columns = []
        try:
            columns = [row[1] for row in conn.execute("PRAGMA table_info(memories)").fetchall()]
        except Exception:
            pass

        if columns:
            # Existing database — migrate
            if "transcript_ref" not in columns:
                conn.execute("ALTER TABLE memories ADD COLUMN transcript_ref TEXT")
            if "tags" not in columns:
                conn.execute("ALTER TABLE memories ADD COLUMN tags TEXT")

            # Rebuild FTS to include tags column if it exists but lacks tags
            try:
                fts_cols = conn.execute("PRAGMA table_info(memories_fts)").fetchall()
                fts_col_names = [row[1] for row in fts_cols]
                if "tags" not in fts_col_names:
                    # Drop old FTS and triggers, recreate with tags
                    conn.executescript("""
                        DROP TRIGGER IF EXISTS memories_ai;
                        DROP TRIGGER IF EXISTS memories_ad;
                        DROP TRIGGER IF EXISTS memories_au;
                        DROP TABLE IF EXISTS memories_fts;
                    """)
                    conn.executescript(SCHEMA)
                    # Rebuild FTS index from existing data
                    conn.execute("""
                        INSERT INTO memories_fts(rowid, content, type, project, tags)
                        SELECT id, content, type, project, tags FROM memories
                    """)
            except Exception:
                pass

            # Migrate old types to new types
            conn.execute("UPDATE memories SET type = 'note' WHERE type IN ('decision', 'insight', 'progress', 'correction')")
            conn.execute("UPDATE memories SET type = 'narrative' WHERE type = 'project_narrative'")
            conn.execute("UPDATE memories SET type = 'narrative' WHERE type = 'note' AND content LIKE '[PROJECT NARRATIVE]%'")
            conn.execute("UPDATE memories SET type = 'session_log' WHERE type = 'session_summary'")
            conn.execute("UPDATE memories SET type = 'note' WHERE type = 'chunk_summary'")
            conn.commit()
        else:
            # Fresh database
            conn.executescript(SCHEMA)
            conn.commit()
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a sqlite3.Row to a plain dict."""
    return dict(row)


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
                        "type": "string",
                        "description": "Reference to raw transcript file, e.g. '~/.claude/memory/transcripts/SESSION_ID.jsonl'",
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
                                "to_id": {"type": "integer"},
                                "relationship": {
                                    "type": "string",
                                    "enum": list(VALID_RELATIONSHIPS),
                                },
                            },
                            "required": ["to_id", "relationship"],
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
            description="Get a specific memory by ID with its connections.",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {
                        "type": "integer",
                        "description": "Memory ID",
                    },
                },
                "required": ["id"],
            },
        ),
        types.Tool(
            name="memory_connect",
            description="Create a connection between two memories. Use 'supersedes' for narrative versions, 'related_to' for linked notes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "from_id": {
                        "type": "integer",
                        "description": "Source memory ID",
                    },
                    "to_id": {
                        "type": "integer",
                        "description": "Target memory ID",
                    },
                    "relationship": {
                        "type": "string",
                        "description": "One of: supersedes, related_to",
                        "enum": list(VALID_RELATIONSHIPS),
                    },
                },
                "required": ["from_id", "to_id", "relationship"],
            },
        ),
        types.Tool(
            name="memory_explore",
            description="Explore connections from a starting memory.",
            inputSchema={
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "integer",
                        "description": "Starting memory ID",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "How many hops to traverse (max 3, default 1)",
                        "minimum": 1,
                        "maximum": 3,
                        "default": 1,
                    },
                },
                "required": ["memory_id"],
            },
        ),
        types.Tool(
            name="memory_delete",
            description="Delete a memory and its connections.",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {
                        "type": "integer",
                        "description": "Memory ID to delete",
                    },
                },
                "required": ["id"],
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

    if not content:
        return _error("content is required")
    if mem_type not in VALID_TYPES:
        return _error(f"type must be one of: {', '.join(sorted(VALID_TYPES))}")
    if not (1 <= importance <= 10):
        return _error("importance must be between 1 and 10")

    conn = get_db()
    try:
        # Deduplication: skip if similar content stored in the last hour
        # (skip for narratives — they're meant to be updated)
        if mem_type != "narrative":
            existing = conn.execute(
                "SELECT id FROM memories WHERE substr(content, 1, 100) = substr(?, 1, 100) "
                "AND created_at > datetime('now', '-1 hour')",
                (content,),
            ).fetchone()
            if existing:
                return _text(json.dumps({
                    "id": existing["id"],
                    "status": "duplicate_skipped",
                    "message": "Similar memory already stored recently"
                }, indent=2))

        cursor = conn.execute(
            "INSERT INTO memories (type, content, project, session_id, importance, transcript_ref, tags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (mem_type, content, project, session_id, importance, transcript_ref, tags),
        )
        memory_id = cursor.lastrowid

        for link in connections:
            to_id = link.get("to_id")
            relationship = link.get("relationship", "")
            if relationship not in VALID_RELATIONSHIPS:
                continue
            target = conn.execute("SELECT id FROM memories WHERE id = ?", (to_id,)).fetchone()
            if target:
                conn.execute(
                    "INSERT OR IGNORE INTO connections (from_id, to_id, relationship) "
                    "VALUES (?, ?, ?)",
                    (memory_id, to_id, relationship),
                )

        conn.commit()
        return _text(json.dumps({"id": memory_id, "status": "stored"}, indent=2))
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
            "SELECT m.id, m.type, m.content, m.project, m.created_at, "
            "m.importance, m.transcript_ref, m.tags "
            "FROM memories_fts f "
            "JOIN memories m ON m.id = f.rowid "
            "WHERE memories_fts MATCH ? "
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
        sql = "SELECT id, type, content, project, session_id, created_at, importance, transcript_ref, tags FROM memories WHERE 1=1 "
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
    memory_id = args.get("id")
    if memory_id is None:
        return _error("id is required")

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, type, content, project, session_id, created_at, importance, transcript_ref, tags "
            "FROM memories WHERE id = ?",
            (memory_id,),
        ).fetchone()

        if not row:
            return _error(f"Memory {memory_id} not found")

        memory = row_to_dict(row)

        outgoing = conn.execute(
            "SELECT c.to_id, c.relationship, c.created_at, "
            "m.type, substr(m.content, 1, 200) as content, m.project, m.importance "
            "FROM connections c "
            "JOIN memories m ON m.id = c.to_id "
            "WHERE c.from_id = ?",
            (memory_id,),
        ).fetchall()

        incoming = conn.execute(
            "SELECT c.from_id, c.relationship, c.created_at, "
            "m.type, substr(m.content, 1, 200) as content, m.project, m.importance "
            "FROM connections c "
            "JOIN memories m ON m.id = c.from_id "
            "WHERE c.to_id = ?",
            (memory_id,),
        ).fetchall()

        memory["connections"] = {
            "outgoing": [
                {"to_id": r["to_id"], "relationship": r["relationship"],
                 "memory": {"type": r["type"], "content": r["content"], "project": r["project"]}}
                for r in outgoing
            ],
            "incoming": [
                {"from_id": r["from_id"], "relationship": r["relationship"],
                 "memory": {"type": r["type"], "content": r["content"], "project": r["project"]}}
                for r in incoming
            ],
        }

        return _text(json.dumps(memory, indent=2))
    finally:
        conn.close()


# -- memory_connect --------------------------------------------------------

def _handle_connect(args: dict[str, Any]) -> list[types.TextContent]:
    from_id = args.get("from_id")
    to_id = args.get("to_id")
    relationship = args.get("relationship", "")

    if from_id is None or to_id is None:
        return _error("from_id and to_id are required")
    if relationship not in VALID_RELATIONSHIPS:
        return _error(f"relationship must be one of: {', '.join(sorted(VALID_RELATIONSHIPS))}")

    conn = get_db()
    try:
        for mid in (from_id, to_id):
            if not conn.execute("SELECT id FROM memories WHERE id = ?", (mid,)).fetchone():
                return _error(f"Memory {mid} not found")

        conn.execute(
            "INSERT OR IGNORE INTO connections (from_id, to_id, relationship) VALUES (?, ?, ?)",
            (from_id, to_id, relationship),
        )
        conn.commit()
        return _text(json.dumps({
            "status": "connected", "from_id": from_id,
            "to_id": to_id, "relationship": relationship,
        }, indent=2))
    finally:
        conn.close()


# -- memory_explore --------------------------------------------------------

def _handle_explore(args: dict[str, Any]) -> list[types.TextContent]:
    start_id = args.get("memory_id")
    depth = min(args.get("depth", 1), 3)

    if start_id is None:
        return _error("memory_id is required")

    conn = get_db()
    try:
        start_row = conn.execute(
            "SELECT id, type, substr(content, 1, 300) as content, project, created_at, importance "
            "FROM memories WHERE id = ?",
            (start_id,),
        ).fetchone()

        if not start_row:
            return _error(f"Memory {start_id} not found")

        visited: set[int] = set()
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []

        def traverse(memory_id: int, current_depth: int) -> None:
            if memory_id in visited or current_depth > depth:
                return
            visited.add(memory_id)

            row = conn.execute(
                "SELECT id, type, substr(content, 1, 300) as content, project, created_at, importance "
                "FROM memories WHERE id = ?",
                (memory_id,),
            ).fetchone()
            if not row:
                return
            nodes.append(row_to_dict(row))

            for direction, id_col, other_col in [
                ("out", "from_id", "to_id"),
                ("in", "to_id", "from_id"),
            ]:
                rows = conn.execute(
                    f"SELECT {other_col} as other_id, relationship FROM connections WHERE {id_col} = ?",
                    (memory_id,),
                ).fetchall()
                for c in rows:
                    edges.append({
                        "from_id": memory_id if direction == "out" else c["other_id"],
                        "to_id": c["other_id"] if direction == "out" else memory_id,
                        "relationship": c["relationship"],
                    })
                    traverse(c["other_id"], current_depth + 1)

        traverse(start_id, 0)

        seen_edges: set[tuple[int, int, str]] = set()
        unique_edges = []
        for e in edges:
            key = (e["from_id"], e["to_id"], e["relationship"])
            if key not in seen_edges:
                seen_edges.add(key)
                unique_edges.append(e)

        return _text(json.dumps({
            "start_id": start_id, "depth": depth,
            "nodes": nodes, "edges": unique_edges,
        }, indent=2))
    finally:
        conn.close()


# -- memory_delete ---------------------------------------------------------

def _handle_delete(args: dict[str, Any]) -> list[types.TextContent]:
    memory_id = args.get("id")
    if memory_id is None:
        return _error("id is required")

    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM memories WHERE id = ?", (memory_id,)).fetchone()
        if not row:
            return _error(f"Memory {memory_id} not found")

        conn.execute("DELETE FROM connections WHERE from_id = ? OR to_id = ?", (memory_id, memory_id))
        conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        conn.commit()
        return _text(json.dumps({"id": memory_id, "status": "deleted"}, indent=2))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    init_db()
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
