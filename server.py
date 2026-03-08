"""
MCP server for persistent Claude Code memory.

Provides tools for storing, searching, connecting, and exploring memories
backed by SQLite with FTS5 full-text search. Designed to be spawned as a
subprocess by Claude Code.

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

VALID_TYPES = {"decision", "insight", "progress", "correction", "session_summary", "chunk_summary", "note"}
VALID_RELATIONSHIPS = {"supports", "contradicts", "supersedes", "implements", "depends_on", "related_to"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    project TEXT,
    session_id TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    importance INTEGER DEFAULT 5 CHECK(importance BETWEEN 1 AND 10),
    transcript_ref TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content, type, project,
    content='memories',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, type, project)
    VALUES (new.id, new.content, new.type, new.project);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, type, project)
    VALUES('delete', old.id, old.content, old.type, old.project);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, type, project)
    VALUES('delete', old.id, old.content, old.type, old.project);
    INSERT INTO memories_fts(rowid, content, type, project)
    VALUES (new.id, new.content, new.type, new.project);
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
    """Create tables and triggers if they don't already exist."""
    conn = get_db()
    try:
        conn.executescript(SCHEMA)
        # Migrate: add transcript_ref if missing (for existing databases)
        columns = [row[1] for row in conn.execute("PRAGMA table_info(memories)").fetchall()]
        if "transcript_ref" not in columns:
            conn.execute("ALTER TABLE memories ADD COLUMN transcript_ref TEXT")
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
            description="Store a new memory.",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The memory content",
                    },
                    "type": {
                        "type": "string",
                        "description": "One of: decision, insight, progress, correction, session_summary, note",
                        "enum": list(VALID_TYPES),
                    },
                    "project": {
                        "type": "string",
                        "description": "Project name, e.g. 'finance-nexus'",
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
                        "description": "Reference to raw transcript file and line range, e.g. '~/.claude/memory/transcripts/SESSION_ID.jsonl:150-220'",
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
            description="Full-text search across all memories.",
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
            description="Create a connection between two memories.",
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
                        "description": "One of: supports, contradicts, supersedes, implements, depends_on, related_to",
                        "enum": list(VALID_RELATIONSHIPS),
                    },
                },
                "required": ["from_id", "to_id", "relationship"],
            },
        ),
        types.Tool(
            name="memory_explore",
            description="Explore the knowledge graph from a starting memory.",
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
    """Wrap a string as a list containing a single TextContent."""
    return [types.TextContent(type="text", text=content)]


def _error(message: str) -> list[types.TextContent]:
    """Return an error-formatted text result."""
    return _text(f"Error: {message}")


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    try:
        if name == "memory_store":
            return _handle_store(arguments)
        elif name == "memory_search":
            return _handle_search(arguments)
        elif name == "memory_recent":
            return _handle_recent(arguments)
        elif name == "memory_get":
            return _handle_get(arguments)
        elif name == "memory_connect":
            return _handle_connect(arguments)
        elif name == "memory_explore":
            return _handle_explore(arguments)
        elif name == "memory_delete":
            return _handle_delete(arguments)
        else:
            return _error(f"Unknown tool: {name}")
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
    connections = args.get("connections", [])

    if not content:
        return _error("content is required")
    if mem_type not in VALID_TYPES:
        return _error(f"type must be one of: {', '.join(sorted(VALID_TYPES))}")
    if not (1 <= importance <= 10):
        return _error("importance must be between 1 and 10")

    conn = get_db()
    try:
        # Deduplication: skip if similar content was stored in the last hour
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
            "INSERT INTO memories (type, content, project, session_id, importance, transcript_ref) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (mem_type, content, project, session_id, importance, transcript_ref),
        )
        memory_id = cursor.lastrowid

        for link in connections:
            to_id = link.get("to_id")
            relationship = link.get("relationship", "")
            if relationship not in VALID_RELATIONSHIPS:
                continue
            # Verify the target memory exists
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

    # Build FTS query: escape special characters for safety, use implicit AND
    fts_query = " ".join(
        f'"{token}"' for token in query.split() if token
    )

    conn = get_db()
    try:
        sql = (
            "SELECT m.id, m.type, m.content, m.project, m.created_at, m.importance, m.transcript_ref "
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
            # Truncate content to 200 characters for search results
            if len(d["content"]) > 200:
                d["content"] = d["content"][:200] + "..."
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
        sql = "SELECT id, type, content, project, session_id, created_at, importance, transcript_ref FROM memories WHERE 1=1 "
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
        results = [row_to_dict(row) for row in rows]
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
            "SELECT id, type, content, project, session_id, created_at, importance, transcript_ref "
            "FROM memories WHERE id = ?",
            (memory_id,),
        ).fetchone()

        if not row:
            return _error(f"Memory {memory_id} not found")

        memory = row_to_dict(row)

        # Get connections where this memory is the source
        outgoing = conn.execute(
            "SELECT c.to_id, c.relationship, c.created_at, "
            "m.type, m.content, m.project, m.importance "
            "FROM connections c "
            "JOIN memories m ON m.id = c.to_id "
            "WHERE c.from_id = ?",
            (memory_id,),
        ).fetchall()

        # Get connections where this memory is the target
        incoming = conn.execute(
            "SELECT c.from_id, c.relationship, c.created_at, "
            "m.type, m.content, m.project, m.importance "
            "FROM connections c "
            "JOIN memories m ON m.id = c.from_id "
            "WHERE c.to_id = ?",
            (memory_id,),
        ).fetchall()

        memory["connections"] = {
            "outgoing": [
                {
                    "to_id": r["to_id"],
                    "relationship": r["relationship"],
                    "connected_at": r["created_at"],
                    "memory": {
                        "type": r["type"],
                        "content": r["content"],
                        "project": r["project"],
                        "importance": r["importance"],
                    },
                }
                for r in outgoing
            ],
            "incoming": [
                {
                    "from_id": r["from_id"],
                    "relationship": r["relationship"],
                    "connected_at": r["created_at"],
                    "memory": {
                        "type": r["type"],
                        "content": r["content"],
                        "project": r["project"],
                        "importance": r["importance"],
                    },
                }
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
        # Verify both memories exist
        for mid in (from_id, to_id):
            if not conn.execute("SELECT id FROM memories WHERE id = ?", (mid,)).fetchone():
                return _error(f"Memory {mid} not found")

        conn.execute(
            "INSERT OR IGNORE INTO connections (from_id, to_id, relationship) VALUES (?, ?, ?)",
            (from_id, to_id, relationship),
        )
        conn.commit()
        return _text(json.dumps({
            "status": "connected",
            "from_id": from_id,
            "to_id": to_id,
            "relationship": relationship,
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
        # Fetch starting memory
        start_row = conn.execute(
            "SELECT id, type, content, project, session_id, created_at, importance "
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
                "SELECT id, type, content, project, created_at, importance "
                "FROM memories WHERE id = ?",
                (memory_id,),
            ).fetchone()
            if not row:
                return
            nodes.append(row_to_dict(row))

            # Outgoing connections
            out = conn.execute(
                "SELECT to_id, relationship FROM connections WHERE from_id = ?",
                (memory_id,),
            ).fetchall()
            for c in out:
                edges.append({
                    "from_id": memory_id,
                    "to_id": c["to_id"],
                    "relationship": c["relationship"],
                })
                traverse(c["to_id"], current_depth + 1)

            # Incoming connections
            inc = conn.execute(
                "SELECT from_id, relationship FROM connections WHERE to_id = ?",
                (memory_id,),
            ).fetchall()
            for c in inc:
                edges.append({
                    "from_id": c["from_id"],
                    "to_id": memory_id,
                    "relationship": c["relationship"],
                })
                traverse(c["from_id"], current_depth + 1)

        traverse(start_id, 0)

        # Deduplicate edges
        seen_edges: set[tuple[int, int, str]] = set()
        unique_edges = []
        for e in edges:
            key = (e["from_id"], e["to_id"], e["relationship"])
            if key not in seen_edges:
                seen_edges.add(key)
                unique_edges.append(e)

        result = {
            "start_id": start_id,
            "depth": depth,
            "nodes": nodes,
            "edges": unique_edges,
        }
        return _text(json.dumps(result, indent=2))
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

        # Delete connections first
        conn.execute("DELETE FROM connections WHERE from_id = ? OR to_id = ?", (memory_id, memory_id))
        # Delete the memory (triggers will clean up FTS)
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
