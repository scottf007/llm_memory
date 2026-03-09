# SQLite Record-Level Sync Research

**Date:** 2026-03-10
**Goal:** Find a way to sync `~/.claude/memory/memory.db` across desktop + laptop with record-level granularity, supporting simultaneous writes, no cloud dependency, minimal ops burden.

---

## Current Architecture

- Python MCP server (`server.py`) using `sqlite3` stdlib
- Two tables: `memories` (main data), `connections` (graph edges)
- One FTS5 virtual table: `memories_fts` (content-addressed, synced via triggers)
- WAL mode enabled, `AUTOINCREMENT` primary keys
- ~731 lines of straightforward Python, no ORM

## The Problem

Syncthing (file-level sync) copies the entire `memory.db` file. If both machines write between syncs, one file wins and the other's writes are lost. SQLite databases are binary files -- Syncthing cannot merge them.

---

## Approach 1: cr-sqlite (CRDT Extension)

**Repo:** https://github.com/vlcn-io/cr-sqlite
**What it is:** A loadable SQLite extension (written in Rust) that adds CRDT columns to existing tables, enabling conflict-free multi-writer merge.

### How it works

1. Load the extension: `conn.load_extension("crsqlite")`
2. Create tables normally, then call `SELECT crsql_as_crr('memories')` to upgrade them to CRDTs
3. Each database gets a unique `site_id` (UUID, auto-generated)
4. Every column becomes a Last-Write-Wins (LWW) register tracked by a Lamport clock (`col_version`)
5. A virtual table `crsql_changes` exposes all changes as rows:
   - Columns: `table, pk, cid, val, col_version, db_version, site_id, cl, seq`
6. To sync: SELECT changes from one DB, INSERT them into another DB's `crsql_changes`
7. Merge is deterministic -- higher `col_version` wins; ties broken by `site_id`

### Sync protocol (what we'd build)

```python
# On machine A -- export changes since last sync
changes = conn_a.execute("""
    SELECT "table", "pk", "cid", "val", "col_version", "db_version",
           COALESCE("site_id", crsql_site_id()), "cl", "seq"
    FROM crsql_changes WHERE db_version > ? AND site_id IS NOT ?
""", (last_synced_version, peer_site_id)).fetchall()

# Write changes to a file (JSONL, msgpack, etc.)
# Syncthing syncs this small changeset file

# On machine B -- apply changes
for change in changes:
    conn_b.execute("""
        INSERT INTO crsql_changes
        ("table","pk","cid","val","col_version","db_version","site_id","cl","seq")
        VALUES (?,?,?,?,?,?,?,?,?)
    """, change)
```

### FTS5 compatibility

**cr-sqlite does NOT directly support FTS5 virtual tables as CRRs.** You cannot call `crsql_as_crr('memories_fts')`. However, this is fine for our use case because:

- FTS5 is a derived index, not a source of truth
- We only need CRDT sync on the `memories` and `connections` tables
- After applying changes, we rebuild/update FTS5 via the existing triggers
- The triggers (`memories_ai`, `memories_ad`, `memories_au`) already keep FTS5 in sync with the `memories` table

**Key concern:** Our `AUTOINCREMENT` primary keys will collide across machines. Two machines could both create memory id=47. cr-sqlite handles this if you use `crsql_as_crr`, but the recommended approach is to use UUIDs or composite keys instead of autoincrement.

### Performance

- Inserts into CRR tables are ~2.5x slower than normal SQLite inserts
- For our use case (dozens of writes per session, not thousands per second), this is negligible

### Integration complexity: MEDIUM

Changes needed:
1. Switch `id INTEGER PRIMARY KEY AUTOINCREMENT` to UUID-based primary keys (TEXT)
2. Load cr-sqlite extension on every connection
3. Call `crsql_as_crr()` on `memories` and `connections` tables
4. Build a sync script that exports/imports changesets via files
5. Use Syncthing to sync the changeset files (small JSONL), not the DB itself
6. Call `crsql_finalize()` before closing connections

### Project status

- 2,163 commits, prebuilt binaries for Linux/macOS/Windows
- Written in Rust, loads as `.so`/`.dylib`/`.dll`
- Used in production by Fly.io's Corrosion (gossip-based distributed SQLite)
- Python: works via `conn.enable_load_extension(True)` + `conn.load_extension()`
- **Risk:** Single maintainer project. If abandoned, the extension still works but won't get SQLite version updates.

### Verdict: MOST PROMISING for our use case

---

## Approach 2: sqlite-sync (SQLite Cloud / sqliteai)

**Repo:** https://github.com/sqliteai/sqlite-sync
**What it is:** A CRDT-based SQLite extension from the SQLite Cloud team.

### How it works

Similar CRDT approach to cr-sqlite, but with a built-in network layer. You call `cloudsync_init('table')` to mark tables for sync, then `cloudsync_network_sync()` to push/pull changes.

### The dealbreaker: REQUIRES SQLITE CLOUD

The sync protocol is tightly integrated with SQLite Cloud's infrastructure. There is no local-only or peer-to-peer mode. You need a `sqlitecloud://` connection string and an API key.

### Verdict: ELIMINATED -- requires cloud service

---

## Approach 3: Litestream

**Repo:** https://github.com/benbjohnson/litestream
**What it is:** Streaming replication of SQLite WAL to S3 or another file.

### How it works

Litestream runs as a sidecar process, continuously streaming WAL frames to a replica destination (S3, SFTP, or local file path). It's disaster recovery, not multi-writer sync.

### Why it doesn't solve our problem

- **Single-writer only.** Litestream replicates FROM one primary TO one or more read replicas.
- If both machines write, you get two divergent WAL streams with no merge capability.
- Designed for backup/restore, not bidirectional sync.

### Verdict: ELIMINATED -- single-writer only

---

## Approach 4: LiteSync

**Website:** https://litesync.io
**What it is:** Commercial SQLite replication library (closed-source, modified SQLite).

### How it works

Replaces the standard SQLite library with a modified version that intercepts writes and replicates them to peers via TCP. All nodes can write, even offline. Changes sync when connected.

### Problems

- **Commercial license required.** Free version limited to a single table per database (we have 3 tables + FTS5).
- **Closed source.** You use their custom SQLite build, not standard SQLite.
- **Replaces sqlite3.** Can't use Python's built-in `sqlite3` module -- need their custom library.
- Pricing not publicly listed ("contact us").

### Verdict: ELIMINATED -- commercial, closed-source, replaces sqlite3

---

## Approach 5: rqlite / dqlite

**rqlite:** https://github.com/rqlite/rqlite -- Raft-consensus distributed SQLite
**dqlite:** Canonical's C-Raft based distributed SQLite

### Why they don't fit

- Both require a **cluster of always-connected nodes** with Raft leader election.
- Designed for servers, not laptops that go offline for hours.
- rqlite is a standalone Go binary with an HTTP API -- not a drop-in SQLite replacement.
- Raft consensus requires majority quorum; 2 nodes can't form a valid cluster.

### Verdict: ELIMINATED -- requires always-on cluster, wrong architecture

---

## Approach 6: Append-Only Log + Merge (DIY)

**What it is:** Instead of syncing the SQLite DB, each machine writes changes to append-only JSONL files. A merge process reconstructs the DB from all logs.

### Design sketch

```
~/.claude/memory/
  memory.db           (local, derived, not synced)
  changelog/
    desktop-abc123.jsonl   (append-only, synced via Syncthing)
    laptop-def456.jsonl    (append-only, synced via Syncthing)
```

Each write appends a JSON line:
```json
{"op":"insert","table":"memories","id":"uuid-here","data":{...},"ts":"2026-03-10T14:30:00Z","site":"desktop-abc123"}
{"op":"update","table":"memories","id":"uuid-here","data":{"importance":8},"ts":"2026-03-10T14:31:00Z","site":"desktop-abc123"}
{"op":"delete","table":"memories","id":"uuid-here","ts":"2026-03-10T15:00:00Z","site":"desktop-abc123"}
```

### Merge strategy

- On startup (or periodically), read ALL changelog files
- Sort events by timestamp (with site_id tiebreaker)
- Replay into a fresh SQLite DB (or apply only new events since last merge)
- Last-write-wins for conflicts on the same record

### FTS5 compatibility

Perfect -- FTS5 is rebuilt from the merged `memories` table via triggers, same as today.

### Advantages

- **Zero dependencies.** Pure Python, no extensions to load.
- **Syncthing-friendly.** Append-only files merge cleanly -- Syncthing just copies new bytes.
- **Debuggable.** Every change is human-readable JSON.
- **Auditable.** Full history of every change ever made.

### Disadvantages

- **Must build it ourselves.** No library does this for us.
- **Log files grow forever.** Need periodic compaction/snapshotting.
- **Startup cost.** Replaying the full log to rebuild the DB takes time (but for our scale -- hundreds of memories, not millions -- it's negligible).
- **Clock skew.** If machine clocks differ, LWW resolution may be wrong. (Mitigated: both machines use NTP.)
- **No transactions.** Multi-row atomic operations require careful handling.

### Integration complexity: MEDIUM-LOW

Changes needed:
1. Switch to UUID primary keys (same as cr-sqlite)
2. Wrap every write in server.py to also append to the local JSONL file
3. Build a merge/replay script that runs on startup
4. Point Syncthing at `~/.claude/memory/changelog/` instead of the DB file
5. Each machine generates a unique site_id on first run

### Verdict: STRONG ALTERNATIVE -- simpler, no native dependencies, but more code to write

---

## Approach 7: Syncthing + Record-Level (Hybrid)

**Can Syncthing do record-level sync?** No. Syncthing operates on files. But we can make it work:

### Option A: One-file-per-record

Store each memory as a separate JSON file:
```
~/.claude/memory/records/
  memories/
    uuid-abc.json
    uuid-def.json
  connections/
    uuid-ghi.json
```

Syncthing syncs individual files. On startup, rebuild SQLite from the JSON files.

**Problem:** Thousands of tiny files. Syncthing handles this but it's ugly. FTS5 index must be rebuilt on every startup.

### Option B: Append-only log files (same as Approach 6)

This is really just Approach 6 with Syncthing as transport.

### Verdict: Approach 6 is the cleaner version of this idea

---

## Comparison Matrix

| Criterion | cr-sqlite | Append-only log | sqlite-sync | Litestream | LiteSync | rqlite |
|---|---|---|---|---|---|---|
| Multi-writer | Yes | Yes | Yes | No | Yes | Yes (online only) |
| Works offline | Yes | Yes | Yes | N/A | Yes | No |
| No cloud needed | Yes | Yes | **No** | Yes | Yes | Yes |
| Python stdlib only | No (needs .so) | **Yes** | No | No | No | No |
| FTS5 compatible | Yes (indirect) | **Yes** | Unknown | N/A | Unknown | N/A |
| Conflict resolution | Automatic (CRDT) | Manual (LWW) | Automatic | N/A | Automatic | Raft consensus |
| Operational complexity | Low | Low | Medium | Low | Medium | High |
| Code changes needed | Medium | Medium | Medium | Low | High | High |
| Dependencies | Rust .so binary | None | Cloud service | Go binary | Custom SQLite | Go binary |
| Production proven | Yes (Fly.io) | DIY | Yes (Cloud) | Yes | Yes | Yes |

---

## Recommendation

### Best option: cr-sqlite

**Why:**
- Battle-tested CRDT merge semantics -- no need to implement our own conflict resolution
- The `crsql_changes` virtual table gives us a clean sync protocol
- Fly.io runs it in production (Corrosion) at massive scale
- Works with standard Python `sqlite3` via `load_extension`
- FTS5 works fine alongside it (just don't CRDT the FTS table itself)
- Prebuilt Linux/macOS binaries available

**Sync architecture with cr-sqlite:**
1. Both machines load cr-sqlite and use UUID primary keys
2. Each machine has its own `memory.db` (not synced by Syncthing)
3. A cron job or session hook exports new changes to `~/.claude/memory/sync/outbox-{site_id}.jsonl`
4. Syncthing syncs the `sync/` directory
5. On session start, import any new changes from peer outbox files
6. Total new code: ~100-150 lines for the sync layer

### Fallback option: Append-only log (Approach 6)

**Why it's the fallback:**
- Zero native dependencies -- pure Python
- If cr-sqlite's .so binary causes issues (platform, Python version, etc.), this works everywhere
- Slightly more code (~200-300 lines) but conceptually simple
- Good enough for our scale (hundreds of records, not millions)

### Migration path

Both approaches require the same schema change: **switch from AUTOINCREMENT integer IDs to UUIDs.** This is the first step regardless of which sync approach is chosen.

```sql
-- New schema (both approaches)
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    ...
);
```

---

## Next Steps

1. **Try cr-sqlite:** Download the Linux `.so` from GitHub releases, test loading it in Python 3.12, verify it works with our schema + FTS5 triggers
2. **Prototype the sync layer:** Write a small script that exports/imports changesets between two local DBs
3. **UUID migration:** Write a migration script to convert existing integer IDs to UUIDs
4. **If cr-sqlite doesn't work:** Fall back to append-only log approach

---

## Sources

- [cr-sqlite GitHub](https://github.com/vlcn-io/cr-sqlite)
- [cr-sqlite intro docs](https://vlcn.io/docs/cr-sqlite/intro)
- [crsql_changes API](https://vlcn.io/docs/cr-sqlite/api-methods/crsql_changes)
- [Simon Willison's cr-sqlite walkthrough](https://til.simonwillison.net/sqlite/cr-sqlite-macos)
- [Corrosion (Fly.io) -- production cr-sqlite usage](https://fly.io/blog/corrosion/)
- [sqlite-sync (SQLite Cloud)](https://github.com/sqliteai/sqlite-sync)
- [Litestream](https://github.com/benbjohnson/litestream)
- [LiteSync](https://litesync.io/en/)
- [rqlite](https://rqlite.io/docs/faq/)
- [LiteFS vs Litestream vs rqlite comparison](https://onidel.com/blog/sqlite-replication-vps-2025)
- [Evolu (CRDT for SQLite)](https://www.evolu.dev/docs/how-evolu-works)
- [ElectricSQL](https://electric-sql.com/)
- [CRDT implementations list](https://crdt.tech/implementations)
