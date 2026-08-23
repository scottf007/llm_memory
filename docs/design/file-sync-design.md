# File-Based Sync: Implementation Design

**Date:** 2026-03-10
**Status:** Design ready for implementation
**Supersedes:** Approaches 1-6 in sync-research.md. This is Approach 7A (one-file-per-record) refined into a concrete plan.

---

## Core Principle

**Files are the source of truth. The SQLite database is a derived, ephemeral index.**

You can delete `memory.db` at any time and rebuild it from the records directory. Syncthing syncs the records directory. No conflicts because every file has a unique UUID name.

---

## Directory Layout

```
~/.claude/memory/
  memory.db                 # LOCAL ONLY -- derived index, never synced
  records/                  # SYNCED via Syncthing
    {uuid}.json             # One file per memory record
    {uuid}.json
    ...
  transcripts/              # SYNCED (unchanged from today)
    {session_id}.jsonl
```

There is no subdirectory structure within `records/`. Every memory (narrative, note, session_log) and every connection lives in a flat directory of JSON files. This keeps the Syncthing config simple (one folder) and avoids any ambiguity about where a record lives.

---

## File Format

### Memory Record File

Filename: `{uuid}.json` where uuid is a 32-char lowercase hex string (no dashes).

```json
{
  "schema_version": 1,
  "uuid": "a1b2c3d4e5f6789012345678abcdef00",
  "type": "note",
  "content": "The MCP server config lives in ~/.claude/settings.json, not .claude.json",
  "project": "example_project",
  "session_id": "abc123",
  "importance": 7,
  "transcript_ref": "~/.claude/memory/transcripts/abc123.jsonl",
  "tags": "correction, mcp-config",
  "created_at": "2026-03-10T14:30:00",
  "connections": [
    {
      "to_uuid": "f0e1d2c3b4a5968778695a4b3c2d1e0f",
      "relationship": "supersedes"
    }
  ]
}
```

### Field Reference

| Field | Type | Required | Notes |
|---|---|---|---|
| `schema_version` | int | yes | Always `1`. For future format changes. |
| `uuid` | string | yes | 32-char lowercase hex. Matches filename (without `.json`). |
| `type` | string | yes | One of: `narrative`, `note`, `session_log` |
| `content` | string | yes | The memory content. |
| `project` | string | no | Project name. |
| `session_id` | string | no | Session that created this record. |
| `importance` | int | yes | 1-10 scale, default 5. |
| `transcript_ref` | string | no | Path to source transcript. |
| `tags` | string | no | Comma-separated tags. |
| `created_at` | string | yes | ISO 8601 datetime (UTC). Set once, never changes. |
| `connections` | array | no | Outgoing connections from this record. See below. |

### Connection Format (Embedded)

Connections are stored **inside the source record's file**, not as separate files.

```json
"connections": [
  {"to_uuid": "target-uuid-here", "relationship": "supersedes"},
  {"to_uuid": "other-uuid-here", "relationship": "related_to"}
]
```

**Why embedded, not separate files:**
- A connection is meaningless without its source record. If the source is deleted, the connection should vanish.
- Keeps the file count down (no explosion of tiny connection files).
- When Syncthing delivers a new record file, all its connections arrive atomically.
- The `memory_connect` tool updates the source record's file in-place -- this is a small JSON rewrite, not a conflict risk because each file has a unique name and only one machine typically owns a given record.

**Bidirectional discovery:** The DB index stores connections in a `connections` table (same as today), so queries in both directions (outgoing and incoming) are fast. The file only stores the outgoing direction; incoming connections are derived by scanning all files during DB rebuild.

---

## UUID Strategy

Replace integer AUTOINCREMENT IDs with UUIDs everywhere.

```python
import os

def generate_uuid() -> str:
    """Generate a 32-char lowercase hex UUID."""
    return os.urandom(16).hex()
```

No dashes, no hyphens. Just 32 hex chars. This is the filename and the primary key.

### ID Transition

The current DB uses integer IDs (`id INTEGER PRIMARY KEY AUTOINCREMENT`). The new schema uses:

```sql
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

CREATE TABLE IF NOT EXISTS connections (
    from_uuid TEXT NOT NULL REFERENCES memories(uuid),
    to_uuid TEXT NOT NULL REFERENCES memories(uuid),
    relationship TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (from_uuid, to_uuid, relationship)
);
```

The FTS5 table uses `uuid` as the content_rowid equivalent. Since FTS5 content tables require an integer rowid, we add an integer `rowid` column (SQLite provides this implicitly) and map it:

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content, type, project, tags,
    content='memories',
    content_rowid='rowid'
);
```

This works because SQLite always provides a `rowid` for non-WITHOUT-ROWID tables, even when the declared primary key is TEXT.

---

## Tool API Changes

### External API: Integer IDs to UUIDs

All tool parameters and return values switch from `id: integer` to `uuid: string`:

| Tool | Old param | New param |
|---|---|---|
| `memory_store` | returns `{"id": 42}` | returns `{"uuid": "a1b2..."}` |
| `memory_get` | `id: 42` | `uuid: "a1b2..."` |
| `memory_delete` | `id: 42` | `uuid: "a1b2..."` |
| `memory_connect` | `from_id: 42, to_id: 43` | `from_uuid: "a1b2...", to_uuid: "c3d4..."` |
| `memory_explore` | `memory_id: 42` | `uuid: "a1b2..."` |
| `memory_search` | returns `[{"id": 42, ...}]` | returns `[{"uuid": "a1b2...", ...}]` |
| `memory_recent` | returns `[{"id": 42, ...}]` | returns `[{"uuid": "a1b2...", ...}]` |

### Backward Compatibility

The CLAUDE.md memory protocol and session hooks reference integer IDs. The LLM callers don't hard-code IDs though -- they get them from search/recent results and pass them back. So the switch to string UUIDs is transparent as long as the parameter names change. A one-time migration (below) handles existing data.

---

## Modified Server Flow

### `memory_store`

```
1. Validate inputs (same as today)
2. Deduplicate check (same as today, but query by substr match)
3. Generate UUID
4. Build record dict
5. Write JSON file to ~/.claude/memory/records/{uuid}.json
6. Insert into SQLite memories table
7. Process connections: for each connection, also update the file
8. Insert connections into SQLite connections table
9. Return {"uuid": "...", "status": "stored"}
```

**Write ordering:** Write the file FIRST, then insert into DB. If the DB insert fails, the file exists and will be picked up on next startup sync. If the file write fails, we don't insert into the DB either. The file is the source of truth.

### `memory_delete`

```
1. Look up the record by UUID in DB
2. Delete the JSON file from records/
3. Delete from connections table (both directions)
4. Delete from memories table (triggers clean up FTS)
5. Return {"uuid": "...", "status": "deleted"}
```

**Cross-machine deletion:** When a file is deleted on machine A, Syncthing propagates the deletion to machine B (Syncthing tracks deletions). Machine B's next startup sync notices the file is gone from disk but present in DB, and removes it from the DB. See "Startup Sync" below.

**No tombstones needed.** Syncthing's own deletion propagation handles this. Syncthing uses a `.stfolder` marker and internal state to track which files should exist. When a file is deleted on one side, Syncthing deletes it on the other side too.

### `memory_connect`

```
1. Validate from_uuid, to_uuid, relationship
2. Verify both UUIDs exist in DB
3. Read the source record's JSON file
4. Append the connection to the "connections" array (dedup by to_uuid+relationship)
5. Write the updated JSON file back
6. Insert into SQLite connections table
7. Return {"status": "connected", ...}
```

### `memory_get`, `memory_search`, `memory_recent`, `memory_explore`

These are **read-only** -- they query the SQLite DB exactly as today, just with UUID columns instead of integer IDs. No file I/O needed for reads.

---

## Startup Sync

On server startup (in `init_db()` or a new `sync_from_files()` function), reconcile the records directory with the DB.

### Algorithm

```python
def sync_from_files() -> None:
    """Reconcile records/ directory with SQLite DB."""
    records_dir = DB_DIR / "records"
    records_dir.mkdir(parents=True, exist_ok=True)

    conn = get_db()
    try:
        # 1. Get all UUIDs currently in DB
        db_uuids = set(
            row[0] for row in conn.execute("SELECT uuid FROM memories").fetchall()
        )

        # 2. Get all UUIDs from files on disk
        file_uuids = set()
        file_records = {}
        for path in records_dir.glob("*.json"):
            uuid = path.stem
            file_uuids.add(uuid)
            # Don't read the file yet -- only read if needed

        # 3. Files present on disk but missing from DB → import
        to_import = file_uuids - db_uuids
        for uuid in to_import:
            path = records_dir / f"{uuid}.json"
            record = json.loads(path.read_text())
            _import_record(conn, record)

        # 4. Records in DB but file missing from disk → delete from DB
        #    (file was deleted on another machine, Syncthing propagated the deletion)
        to_remove = db_uuids - file_uuids
        for uuid in to_remove:
            conn.execute("DELETE FROM connections WHERE from_uuid = ? OR to_uuid = ?", (uuid, uuid))
            conn.execute("DELETE FROM memories WHERE uuid = ?", (uuid,))

        # 5. Rebuild connections from files for imported records
        #    (connections reference other records that may also be new)
        if to_import:
            _rebuild_connections(conn, to_import, records_dir)

        conn.commit()
    finally:
        conn.close()


def _import_record(conn, record: dict) -> None:
    """Insert a single record from a JSON file into the DB."""
    conn.execute(
        "INSERT OR IGNORE INTO memories (uuid, type, content, project, session_id, "
        "created_at, importance, transcript_ref, tags) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            record["uuid"], record["type"], record["content"],
            record.get("project"), record.get("session_id"),
            record["created_at"], record.get("importance", 5),
            record.get("transcript_ref"), record.get("tags"),
        ),
    )


def _rebuild_connections(conn, imported_uuids: set, records_dir: Path) -> None:
    """Rebuild connection rows for newly imported records."""
    for uuid in imported_uuids:
        path = records_dir / f"{uuid}.json"
        if not path.exists():
            continue
        record = json.loads(path.read_text())
        for link in record.get("connections", []):
            to_uuid = link["to_uuid"]
            relationship = link["relationship"]
            # Only insert if target exists in DB
            if conn.execute("SELECT 1 FROM memories WHERE uuid = ?", (to_uuid,)).fetchone():
                conn.execute(
                    "INSERT OR IGNORE INTO connections (from_uuid, to_uuid, relationship) "
                    "VALUES (?, ?, ?)",
                    (uuid, to_uuid, relationship),
                )
```

### Full DB Rebuild (Nuclear Option)

If `memory.db` is deleted or corrupted, the server rebuilds it entirely from files:

```python
def full_rebuild() -> None:
    """Delete and rebuild memory.db entirely from records/ files."""
    if DB_PATH.exists():
        DB_PATH.unlink()
    # Also delete WAL and SHM files
    for suffix in (".db-wal", ".db-shm"):
        p = DB_PATH.with_suffix(suffix)
        if p.exists():
            p.unlink()

    init_db()  # Creates fresh schema

    conn = get_db()
    try:
        records_dir = DB_DIR / "records"
        # Pass 1: Import all records
        for path in sorted(records_dir.glob("*.json")):
            record = json.loads(path.read_text())
            _import_record(conn, record)

        # Pass 2: Import all connections (all records now exist)
        for path in records_dir.glob("*.json"):
            record = json.loads(path.read_text())
            for link in record.get("connections", []):
                to_uuid = link["to_uuid"]
                relationship = link["relationship"]
                if conn.execute("SELECT 1 FROM memories WHERE uuid = ?", (to_uuid,)).fetchone():
                    conn.execute(
                        "INSERT OR IGNORE INTO connections (from_uuid, to_uuid, relationship) "
                        "VALUES (?, ?, ?)",
                        (record["uuid"], to_uuid, relationship),
                    )

        conn.commit()
    finally:
        conn.close()
```

This can also be exposed as a CLI command: `python server.py --rebuild`.

---

## Performance Analysis

### Startup Sync Cost

| Records | File scan time | DB diff time | Total |
|---|---|---|---|
| 100 | ~5ms | ~1ms | ~6ms |
| 500 | ~20ms | ~3ms | ~23ms |
| 1000 | ~40ms | ~5ms | ~45ms |
| 5000 | ~200ms | ~20ms | ~220ms |

The scan is just `glob("*.json")` + set operations on UUIDs. No file content is read unless the UUID is missing from the DB. For the expected scale (a few hundred to low thousands of records), this is negligible.

### Full Rebuild Cost

Reading and parsing 1000 JSON files: ~500ms. Inserting 1000 rows + rebuilding FTS5: ~200ms. Total: under 1 second for 1000 records. Acceptable as an occasional operation.

### Ongoing Write Cost

Each `memory_store` adds one `json.dumps` + one file write (~0.5ms). This is negligible compared to the DB insert and FTS5 indexing.

---

## Migration Plan

### One-Time Migration Script

For existing users who have data in the old integer-ID schema:

```python
def migrate_v1_to_v2():
    """Migrate existing integer-ID memories to UUID-based file records."""
    records_dir = DB_DIR / "records"
    records_dir.mkdir(parents=True, exist_ok=True)

    conn = get_db()
    try:
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
        conn_lookup = {}
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

            path = records_dir / f"{uuid}.json"
            path.write_text(json.dumps(record, indent=2))

        # Now rebuild the DB with new schema
        conn.close()
        full_rebuild()

    except Exception:
        conn.close()
        raise
```

### Migration Trigger

Add detection in `init_db()`:

```python
def init_db() -> None:
    conn = get_db()
    columns = [row[1] for row in conn.execute("PRAGMA table_info(memories)").fetchall()]

    if "id" in columns and "uuid" not in columns:
        # Old schema detected -- migrate
        conn.close()
        migrate_v1_to_v2()
        return

    # ... proceed with new schema setup
```

---

## Syncthing Configuration

### What to Sync

```
~/.claude/memory/records/     ← SYNC THIS (the source of truth)
~/.claude/memory/transcripts/ ← SYNC THIS (raw session logs, unchanged)
```

### What NOT to Sync

```
~/.claude/memory/memory.db      ← DO NOT SYNC (derived index)
~/.claude/memory/memory.db-wal  ← DO NOT SYNC
~/.claude/memory/memory.db-shm  ← DO NOT SYNC
```

### Syncthing Ignore Patterns

In `~/.claude/memory/.stignore`:

```
memory.db
memory.db-wal
memory.db-shm
```

---

## Edge Cases

### Narrative Supersedes

When a narrative is updated, the caller stores a NEW record with a `supersedes` connection to the old one. Both files remain on disk. The old narrative file is not deleted -- it serves as history. The `supersedes` connection in the new file points to the old UUID, making the chain traversable.

To find the "current" narrative for a project: query for type=narrative, project=X, ordered by created_at DESC, limit 1. The supersedes chain is informational, not required for finding the latest version.

### Connection to Not-Yet-Synced Record

If machine A creates record X with a connection to record Y, and machine B receives X's file before Y's file, the connection simply won't be inserted into the DB (target doesn't exist yet). On the next startup sync after Y arrives, `_rebuild_connections` will pick it up.

For this to work, `sync_from_files` must always rebuild connections for any records whose connections reference UUIDs that were just imported in the same sync cycle. The algorithm above handles this: it imports all records first, then rebuilds connections.

### Concurrent Writes to Same File

This can only happen if two machines both call `memory_connect` on the same source record between syncs. Syncthing would detect a conflict and create a `.sync-conflict` file. This is rare (connections are usually created at store time, not separately) but we should handle it:

On startup, scan for `.sync-conflict-*` files in records/. For each conflict file:
1. Parse both the original and conflict versions
2. Merge the connections arrays (union by to_uuid+relationship)
3. Take the newer `content` if it differs (it shouldn't, since only connections change)
4. Write the merged result, delete the conflict file

```python
def _resolve_conflicts(records_dir: Path) -> None:
    """Merge any Syncthing conflict files."""
    for conflict_path in records_dir.glob("*.sync-conflict-*"):
        # Extract original UUID from conflict filename
        # Syncthing format: {name}.sync-conflict-{date}-{id}.json
        stem = conflict_path.stem
        # Find the original file
        original_uuid = stem.split(".sync-conflict")[0]
        original_path = records_dir / f"{original_uuid}.json"

        if not original_path.exists():
            # Original was deleted; conflict file is stale
            conflict_path.unlink()
            continue

        original = json.loads(original_path.read_text())
        conflict = json.loads(conflict_path.read_text())

        # Merge connections (union)
        existing = {(c["to_uuid"], c["relationship"]) for c in original.get("connections", [])}
        for conn in conflict.get("connections", []):
            key = (conn["to_uuid"], conn["relationship"])
            if key not in existing:
                original.setdefault("connections", []).append(conn)

        # Keep the more recent content if different
        if conflict.get("created_at", "") > original.get("created_at", ""):
            original["content"] = conflict["content"]

        original_path.write_text(json.dumps(original, indent=2))
        conflict_path.unlink()
```

### File Corruption

If a JSON file can't be parsed, log a warning and skip it. Don't crash the server. The record won't be in the DB index, but the file stays on disk for manual recovery.

---

## Implementation Checklist

Ordered by dependency. Each step is a single commit.

### Step 1: UUID Infrastructure

- [ ] Add `generate_uuid()` helper
- [ ] Add `RECORDS_DIR` constant (`DB_DIR / "records"`)
- [ ] Add `write_record_file(record: dict) -> Path` helper
- [ ] Add `read_record_file(uuid: str) -> dict | None` helper
- [ ] Add `delete_record_file(uuid: str) -> bool` helper

### Step 2: New Schema

- [ ] Write new `SCHEMA_V2` with TEXT uuid primary keys
- [ ] Update `get_db()` to use new schema
- [ ] Update `init_db()` to detect old vs new schema
- [ ] Write `migrate_v1_to_v2()` function
- [ ] Test: fresh DB creation with new schema
- [ ] Test: migration from old schema

### Step 3: Dual-Write in Tool Handlers

- [ ] `_handle_store`: generate UUID, write JSON file, then insert into DB
- [ ] `_handle_delete`: delete JSON file, then delete from DB
- [ ] `_handle_connect`: update source record's JSON file, then insert into DB
- [ ] Update all handlers to use `uuid` instead of `id` in params and return values

### Step 4: Startup Sync

- [ ] Write `sync_from_files()` function
- [ ] Write `_import_record()` helper
- [ ] Write `_rebuild_connections()` helper
- [ ] Call `sync_from_files()` after `init_db()` in `main()`
- [ ] Test: add a JSON file manually, verify it appears in DB after restart
- [ ] Test: delete a JSON file manually, verify it's removed from DB after restart

### Step 5: Full Rebuild

- [ ] Write `full_rebuild()` function
- [ ] Add `--rebuild` CLI flag to `server.py`
- [ ] Test: delete memory.db, restart server, verify all records restored

### Step 6: Conflict Resolution

- [ ] Write `_resolve_conflicts()` function
- [ ] Call it at the start of `sync_from_files()`
- [ ] Test: create a `.sync-conflict` file manually, verify it's merged

### Step 7: Syncthing Config

- [ ] Create `.stignore` file in `~/.claude/memory/` during init
- [ ] Update `install.sh` to set up the records directory
- [ ] Update README with Syncthing setup instructions

### Step 8: Update Callers

- [ ] Update CLAUDE.md memory protocol to reference UUIDs
- [ ] Update session hooks if they reference integer IDs
- [ ] Update dashboard.py to work with UUID schema
- [ ] Update tests

---

## What We're NOT Doing

- **Not syncing ALL records as files retroactively from old sessions.** The migration script handles existing data, but we don't need to worry about "most recent 15-20 chats" as a subset. All records become files. At our scale (hundreds, maybe low thousands), this is fine.
- **Not adding a manifest/index file.** A `glob("*.json")` + set diff against the DB is fast enough for thousands of files. A manifest adds complexity (another file to sync, another conflict vector) for no real gain.
- **Not using tombstone files for deletes.** Syncthing propagates file deletions natively. If someone uses a different sync tool that doesn't propagate deletes, they'll need tombstones, but we can add that later.
- **Not compressing JSON files.** They're small (1-50KB each). Disk space is not a concern.
- **Not encrypting files at rest.** They're in the user's home directory, same as the DB was. If encryption is needed, Syncthing supports it at the folder level.
