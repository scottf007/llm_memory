"""
FastAPI read-only web dashboard for the LLM Memory SQLite database.

Provides a browser-based interface to browse, search, and visualize
memories and their connections.

Usage:
    python dashboard.py
    # Then open http://localhost:8765
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DB_PATH = Path.home() / ".claude" / "memory" / "memory.db"
TEMPLATES_DIR = Path(__file__).parent / "templates"

app = FastAPI(title="LLM Memory Dashboard", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def get_db() -> Optional[sqlite3.Connection]:
    """Return a read-only connection, or None if the DB doesn't exist."""
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def rows_to_list(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Serve the main dashboard page."""
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/api/memories")
async def api_memories(
    project: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Return memories with optional filtering, search, and pagination."""
    conn = get_db()
    if conn is None:
        return {"memories": [], "total": 0}

    try:
        params: list[Any] = []

        if search:
            fts_query = " ".join(f'"{token}"' for token in search.split() if token)
            count_sql = (
                "SELECT COUNT(*) as cnt FROM memories_fts f "
                "JOIN memories m ON m.rowid = f.rowid "
                "WHERE memories_fts MATCH ?"
            )
            data_sql = (
                "SELECT m.uuid, m.type, m.content, m.project, m.session_id, "
                "m.created_at, m.importance, m.transcript_ref, m.tags "
                "FROM memories_fts f "
                "JOIN memories m ON m.rowid = f.rowid "
                "WHERE memories_fts MATCH ?"
            )
            params.append(fts_query)

            if project:
                count_sql += " AND m.project = ?"
                data_sql += " AND m.project = ?"
                params.append(project)
            if type:
                count_sql += " AND m.type = ?"
                data_sql += " AND m.type = ?"
                params.append(type)

            data_sql += " ORDER BY rank LIMIT ? OFFSET ?"
        else:
            count_sql = "SELECT COUNT(*) as cnt FROM memories WHERE 1=1"
            data_sql = (
                "SELECT uuid, type, content, project, session_id, "
                "created_at, importance, transcript_ref, tags FROM memories WHERE 1=1"
            )

            if project:
                count_sql += " AND project = ?"
                data_sql += " AND project = ?"
                params.append(project)
            if type:
                count_sql += " AND type = ?"
                data_sql += " AND type = ?"
                params.append(type)

            data_sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"

        total = conn.execute(count_sql, params).fetchone()["cnt"]

        params.extend([limit, offset])
        rows = conn.execute(data_sql, params).fetchall()

        return {"memories": rows_to_list(rows), "total": total}
    finally:
        conn.close()


@app.get("/api/stats")
async def api_stats():
    """Return aggregate statistics about the memory database."""
    conn = get_db()
    if conn is None:
        return {
            "total_memories": 0,
            "total_connections": 0,
            "projects": [],
            "types": {},
            "recent_activity": [],
        }

    try:
        total_memories = conn.execute("SELECT COUNT(*) as cnt FROM memories").fetchone()["cnt"]
        total_connections = conn.execute("SELECT COUNT(*) as cnt FROM connections").fetchone()["cnt"]

        projects = [
            r["project"]
            for r in conn.execute(
                "SELECT DISTINCT project FROM memories WHERE project IS NOT NULL ORDER BY project"
            ).fetchall()
        ]

        type_rows = conn.execute(
            "SELECT type, COUNT(*) as cnt FROM memories GROUP BY type ORDER BY cnt DESC"
        ).fetchall()
        types = {r["type"]: r["cnt"] for r in type_rows}

        recent_activity = []
        for days_ago in range(6, -1, -1):
            day = (datetime.utcnow() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
            count = conn.execute(
                "SELECT COUNT(*) as cnt FROM memories WHERE date(created_at) = ?",
                (day,),
            ).fetchone()["cnt"]
            recent_activity.append({"date": day, "count": count})

        return {
            "total_memories": total_memories,
            "total_connections": total_connections,
            "projects": projects,
            "types": types,
            "recent_activity": recent_activity,
        }
    finally:
        conn.close()


@app.get("/api/graph")
async def api_graph(
    project: Optional[str] = Query(None),
    memory_uuid: Optional[str] = Query(None),
):
    """Return nodes and edges for graph visualization."""
    conn = get_db()
    if conn is None:
        return {"nodes": [], "edges": []}

    try:
        if memory_uuid is not None:
            visited: set[str] = set()
            nodes: list[dict[str, Any]] = []
            edges: list[dict[str, Any]] = []
            seen_edges: set[tuple[str, str, str]] = set()

            def traverse(muuid: str, current_depth: int) -> None:
                if muuid in visited or current_depth > 2:
                    return
                visited.add(muuid)

                row = conn.execute(
                    "SELECT uuid, type, content, project, importance "
                    "FROM memories WHERE uuid = ?",
                    (muuid,),
                ).fetchone()
                if not row:
                    return

                d = row_to_dict(row)
                if len(d["content"]) > 100:
                    d["content"] = d["content"][:100] + "..."
                nodes.append(d)

                for c in conn.execute(
                    "SELECT to_uuid, relationship FROM connections WHERE from_uuid = ?",
                    (muuid,),
                ).fetchall():
                    key = (muuid, c["to_uuid"], c["relationship"])
                    if key not in seen_edges:
                        seen_edges.add(key)
                        edges.append({"from": muuid, "to": c["to_uuid"], "relationship": c["relationship"]})
                    traverse(c["to_uuid"], current_depth + 1)

                for c in conn.execute(
                    "SELECT from_uuid, relationship FROM connections WHERE to_uuid = ?",
                    (muuid,),
                ).fetchall():
                    key = (c["from_uuid"], muuid, c["relationship"])
                    if key not in seen_edges:
                        seen_edges.add(key)
                        edges.append({"from": c["from_uuid"], "to": muuid, "relationship": c["relationship"]})
                    traverse(c["from_uuid"], current_depth + 1)

            traverse(memory_uuid, 0)
            return {"nodes": nodes, "edges": edges}

        else:
            if project:
                mem_rows = conn.execute(
                    "SELECT uuid, type, content, project, importance "
                    "FROM memories WHERE project = ? AND uuid IN ("
                    "  SELECT from_uuid FROM connections UNION SELECT to_uuid FROM connections"
                    ") LIMIT 200",
                    (project,),
                ).fetchall()
            else:
                mem_rows = conn.execute(
                    "SELECT uuid, type, content, project, importance "
                    "FROM memories WHERE uuid IN ("
                    "  SELECT from_uuid FROM connections UNION SELECT to_uuid FROM connections"
                    ") LIMIT 200",
                ).fetchall()

            node_uuids = set()
            nodes = []
            for r in mem_rows:
                d = row_to_dict(r)
                node_uuids.add(d["uuid"])
                if len(d["content"]) > 100:
                    d["content"] = d["content"][:100] + "..."
                nodes.append(d)

            if not node_uuids:
                return {"nodes": [], "edges": []}

            placeholders = ",".join("?" * len(node_uuids))
            uuid_list = list(node_uuids)
            edge_rows = conn.execute(
                f"SELECT from_uuid, to_uuid, relationship FROM connections "
                f"WHERE from_uuid IN ({placeholders}) AND to_uuid IN ({placeholders})",
                uuid_list + uuid_list,
            ).fetchall()

            edges = [
                {"from": r["from_uuid"], "to": r["to_uuid"], "relationship": r["relationship"]}
                for r in edge_rows
            ]

            return {"nodes": nodes, "edges": edges}
    finally:
        conn.close()


@app.get("/api/memory/{memory_uuid}")
async def api_memory_detail(memory_uuid: str):
    """Return full details of a single memory with all its connections."""
    conn = get_db()
    if conn is None:
        return {"error": "Database not found"}

    try:
        row = conn.execute(
            "SELECT uuid, type, content, project, session_id, created_at, importance, transcript_ref, tags "
            "FROM memories WHERE uuid = ?",
            (memory_uuid,),
        ).fetchone()

        if not row:
            return {"error": f"Memory {memory_uuid} not found"}

        memory = row_to_dict(row)

        outgoing = conn.execute(
            "SELECT c.to_uuid, c.relationship, c.created_at as connected_at, "
            "m.type, m.content, m.project, m.importance "
            "FROM connections c "
            "JOIN memories m ON m.uuid = c.to_uuid "
            "WHERE c.from_uuid = ?",
            (memory_uuid,),
        ).fetchall()

        incoming = conn.execute(
            "SELECT c.from_uuid, c.relationship, c.created_at as connected_at, "
            "m.type, m.content, m.project, m.importance "
            "FROM connections c "
            "JOIN memories m ON m.uuid = c.from_uuid "
            "WHERE c.to_uuid = ?",
            (memory_uuid,),
        ).fetchall()

        memory["connections"] = {
            "outgoing": [
                {
                    "to_uuid": r["to_uuid"],
                    "relationship": r["relationship"],
                    "connected_at": r["connected_at"],
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
                    "from_uuid": r["from_uuid"],
                    "relationship": r["relationship"],
                    "connected_at": r["connected_at"],
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

        return memory
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="LLM Memory Dashboard")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    uvicorn.run(app, host=args.host, port=args.port)
