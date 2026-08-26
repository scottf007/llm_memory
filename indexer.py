"""FTS5 index over per-item files.

memory.db's job post-Phase-6/7 is a single FTS5 table over ledger items
(decisions/learnings/done/goals/suggestions) rebuilt from
~/.claude/memory/items/{project}/{kind}/{id}.json. `memory_search` queries
this index for cross-project fuzzy search.

Rebuild is cheap (a few thousand JSON reads) and fully recoverable from
the item files, so the DB can be nuked and recreated at any time.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DEFAULT_ITEMS_ROOT = Path.home() / ".claude" / "memory" / "items"
DEFAULT_DB_PATH = Path.home() / ".claude" / "memory" / "memory.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id           TEXT PRIMARY KEY,
    project      TEXT NOT NULL,
    kind         TEXT NOT NULL,
    text         TEXT,
    rationale    TEXT,
    quote        TEXT,
    status       TEXT,
    importance   TEXT,
    last_touched_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_project ON items(project);
CREATE INDEX IF NOT EXISTS idx_items_kind ON items(kind);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);

CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    text, rationale, quote, project, kind,
    content='items', content_rowid='rowid'
);
"""


def _item_fields(data: dict) -> tuple[str, str, str, str, str, str, str, str, str]:
    return (
        data.get("id", ""),
        data.get("project", ""),
        data.get("kind", ""),
        data.get("text", "") or "",
        data.get("rationale", "") or "",
        data.get("quote", "") or "",
        data.get("status", "") or "",
        str(data.get("importance", "") or ""),
        data.get("last_touched_at", "") or "",
    )


def rebuild_items_index(
    items_root: Path | None = None,
    db_path: Path | None = None,
) -> int:
    """Drop and repopulate the items table + FTS index from per-item files.

    Returns the number of items indexed.
    """
    items_root = items_root or DEFAULT_ITEMS_ROOT
    db_path = db_path or DEFAULT_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(SCHEMA)
        conn.execute("DELETE FROM items_fts")
        conn.execute("DELETE FROM items")

        count = 0
        if items_root.exists():
            for project_dir in items_root.iterdir():
                if not project_dir.is_dir():
                    continue
                for kind_dir in project_dir.iterdir():
                    if not kind_dir.is_dir():
                        continue
                    for item_file in kind_dir.glob("*.json"):
                        try:
                            data = json.loads(item_file.read_text())
                        except (OSError, json.JSONDecodeError):
                            continue
                        data.setdefault("project", project_dir.name)
                        data.setdefault("kind", kind_dir.name)
                        fields = _item_fields(data)
                        conn.execute(
                            "INSERT OR REPLACE INTO items "
                            "(id, project, kind, text, rationale, quote, status, importance, last_touched_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            fields,
                        )
                        count += 1

        # Rebuild the FTS contentless-external table from the base table.
        conn.execute(
            "INSERT INTO items_fts (rowid, text, rationale, quote, project, kind) "
            "SELECT rowid, text, rationale, quote, project, kind FROM items"
        )
        conn.commit()
        return count
    finally:
        conn.close()


def search_items(
    query: str,
    project: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    limit: int = 20,
    db_path: Path | None = None,
) -> list[dict]:
    """Fuzzy search across item files via the FTS5 index.

    Default (status=None) returns both active and archived hits. Archived
    rows are ordered after every active row, then by bm25, so they stay
    reachable but never outrank an active hit. Pass status='active' or
    'archived' to filter; that contract is unchanged.
    """
    db_path = db_path or DEFAULT_DB_PATH
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # FTS5 MATCH query across text/rationale/quote/project/kind.
        sql = (
            "SELECT i.id, i.project, i.kind, i.text, i.rationale, i.quote, "
            "       i.status, i.importance, i.last_touched_at, "
            "       bm25(items_fts) AS score "
            "FROM items_fts JOIN items i ON items_fts.rowid = i.rowid "
            "WHERE items_fts MATCH ? "
        )
        params: list = [query]
        if project:
            sql += "AND i.project = ? "
            params.append(project)
        if kind:
            sql += "AND i.kind = ? "
            params.append(kind)
        if status:
            sql += "AND (i.status IS NULL OR i.status = ?) "
            params.append(status)
        # Rank-below: status-first sort key, then existing bm25 order.
        # Applied before LIMIT so a lower-scoring active is not dropped
        # in favour of a higher-scoring archived row.
        sql += (
            "ORDER BY CASE WHEN i.status = 'archived' THEN 1 ELSE 0 END ASC, "
            "score ASC LIMIT ?"
        )
        params.append(limit)

        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return []
        return [dict(r) for r in rows]
    finally:
        conn.close()


def main() -> None:
    count = rebuild_items_index()
    print(f"Rebuilt items_fts with {count} items.")


if __name__ == "__main__":
    main()
