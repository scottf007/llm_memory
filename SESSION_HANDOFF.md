# LLM Memory — Session Handoff

This file summarizes everything built across 5 sessions. Read this to pick up where we left off.

## What Is This Project

LLM Memory is a persistent memory system for Claude Code. It solves:
- Context window filling up during coding sessions, causing compaction and lost context
- No continuity between sessions — Claude starts fresh each time
- Large file generation breaking due to output token limits

It's an MCP server (Python + SQLite) that gives Claude Code 7 memory tools, plus lifecycle hooks and a web dashboard. Everything runs locally, no cloud services.

The user wants to open-source this project.

## Project Location

- Code: `/home/scott/projects/llm_memory/`
- Database: `~/.claude/memory/memory.db`
- Transcripts: `~/.claude/memory/transcripts/` (archived raw JSONL files)
- MCP config: `~/.claude.json` (registered via `claude mcp add-json`)
- Hooks config: `~/.claude/settings.json`
- Global rules: `~/.claude/CLAUDE.md` (memory protocol + chunk summary rules)
- Git: initialized on `main` branch, first commit `dc1ba94`

## Architecture

```
┌─────────────┐     MCP (stdio)     ┌──────────────┐     ┌──────────┐
│ Claude Code  │◄──────────────────►│ LLM Memory   │────►│ SQLite   │
│              │                    │ server.py    │     │ + FTS5   │
└─────────────┘                    └──────────────┘     └──────────┘
       │
       │ lifecycle hooks
       ▼
 SessionStart → auto-load memories + chunk summaries
 PostToolUse  → monitor transcript size
 PreCompact   → save chunk_summary + session_summary before compaction
 SessionEnd   → archive transcript + auto-save session summary
```

### Three-Layer Memory (added session 5)

```
Layer 1: Raw transcript (archived JSONL, never modified)
Layer 2: Chunk summaries (numbered, linked, filtered — stored as chunk_summary type)
Layer 3: Extracted signals (decisions, learnings, corrections — existing types)
```

Claude creates chunk summaries at natural breakpoints (topic changes, task completions, pre-compaction). Each chunk captures decisions/learnings/outcomes and filters out noise (circular debugging, wrong assumptions, dead-end research). Chunks are numbered per session, linked via connections, and reference the raw transcript.

## Files

### Core
- `server.py` (~710 lines) — MCP server with 7 tools. Uses low-level `mcp.server.Server` API (not FastMCP). SQLite with FTS5, WAL mode, foreign keys, deduplication. Includes `transcript_ref` column and `chunk_summary` type.
- `requirements.txt` — mcp>=1.0.0, fastapi>=0.100.0, jinja2>=3.0.0

### Dashboard
- `dashboard.py` — FastAPI web dashboard, read-only viewer. Endpoints: GET /, GET /api/memories, GET /api/stats, GET /api/graph, GET /api/memory/{id}. Accepts `--port` and `--host` CLI args. Default port 8765.
- `templates/dashboard.html` (~802 lines) — Single-page app with dark theme (#0d1117). Two tabs: Timeline and Graph.
- `dashboard.sh` — Launcher script. Usage: `./dashboard.sh [PORT] [HOST]`.

### Hooks
- `hooks/session_start.sh` — SessionStart hook. Loads session summaries + chunk summaries + high-importance memories. Post-compaction: loads last summary + recent chunks.
- `hooks/session_monitor.sh` — PostToolUse hook. Warns at 300KB and 500KB transcript thresholds.
- `hooks/pre_compact.sh` — PreCompact hook. Instructs Claude to save chunk_summary + session_summary before compaction.
- `hooks/session_end.sh` — SessionEnd hook. Archives raw transcript to `~/.claude/memory/transcripts/`, then saves auto-summary. Runs async.
- `hooks/install_hooks.sh` — Installs all 4 hooks into settings.json.

### Setup
- `install.sh` — Creates venv, installs deps, registers MCP server via `claude mcp add-json`.
- `claude-rules-example.md` — CLAUDE.md rules for new installs (memory protocol + chunk summaries).
- `.gitignore` — .venv/, __pycache__/, *.pyc, *.db
- `README.md` — Full documentation.

## MCP Tools (7 total)

All prefixed with `mcp__llm_memory__` when called from Claude Code:

| Tool | Purpose |
|------|---------|
| memory_store | Save a memory with type, project, importance, transcript_ref, optional connections |
| memory_search | Full-text search across all memories |
| memory_recent | Get N most recent memories, filtered by project/type |
| memory_get | Fetch specific memory by ID with connections |
| memory_connect | Link two memories with a relationship |
| memory_explore | Traverse knowledge graph up to 3 hops |
| memory_delete | Remove a memory and its connections |

### Memory Types
decision, insight, progress, correction, session_summary, **chunk_summary**, note

### Relationship Types
supports, contradicts, supersedes, implements, depends_on, related_to

## Database Schema

```sql
CREATE TABLE memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    project TEXT,
    session_id TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    importance INTEGER DEFAULT 5 CHECK(importance BETWEEN 1 AND 10),
    transcript_ref TEXT  -- e.g. "~/.claude/memory/transcripts/SESSION_ID.jsonl:100-250"
);

CREATE VIRTUAL TABLE memories_fts USING fts5(
    content, type, project,
    content='memories', content_rowid='id'
);

CREATE TABLE connections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id INTEGER NOT NULL REFERENCES memories(id),
    to_id INTEGER NOT NULL REFERENCES memories(id),
    relationship TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(from_id, to_id, relationship)
);

-- Triggers keep FTS in sync on insert/update/delete
-- init_db() auto-migrates: adds transcript_ref if missing
```

## Bugs Fixed During Development

1. **Hook format**: Old format → new format with matcher + hooks array
2. **Matcher type**: Used object `{}` → must be string `""`
3. **Timeout units**: 5000 (thinking ms) → 5 (actually seconds)
4. **MCP name collision**: `"memory"` shadowed built-in → renamed to `"llm_memory"`
5. **MCP tools not in ToolSearch**: Added CLAUDE.md note to call directly
6. **session_monitor.sh used slow `find`**: Fixed to read transcript_path from stdin JSON
7. **install_hooks.sh incomplete**: Updated to install all 4 hooks
8. **MCP config wrong file**: Was in `settings.json` → moved to `~/.claude.json` via `claude mcp add-json`
9. **dashboard.sh missing --host**: Added HOST as second positional arg

## Current State

### Working
- MCP server with 7 tools (verified via MCP protocol test + live session)
- All 4 lifecycle hooks installed and tested (dry-run verified)
- Database schema with FTS5, connections, and transcript_ref column
- chunk_summary type supported in server code
- Transcript archiving in session_end.sh
- Chunk summary loading in session_start.sh
- Dashboard at http://localhost:8765
- Git repo initialized on `main` branch
- CLAUDE.md has chunk summary behavioral rules
- claude-rules-example.md created for new installs

### Needs verification after server restart
- Storing a `chunk_summary` via MCP tool (server was running old code when last tested)
- Full chunk summary workflow: create numbered chunks, link them, reference transcript

## Completed Tasks (Session 6)

1. **Verified chunk_summary works** — Stored test chunk_summary (memory #6) after server restart. Confirmed type enum includes chunk_summary.
2. **Updated README.md** — Added chunk_summary to memory types, three-layer memory section, transcript archiving docs, fixed MCP config location (`~/.claude.json` not `settings.json`).
3. **Updated dashboard** — Added chunk_summary badge (green #39d353), type filter option, JS color, transcript_ref display in detail panel, transcript_ref in all API queries.
4. **Tested transcript archiving** — Confirmed `~/.claude/memory/transcripts/` has archived JSONL from previous session.
5. **Second commit** — All changes committed.

## User Preferences

- Wants to open-source this project
- Prefers Python over Node.js
- Wants everything local, no cloud services
- Working on multiple projects (finance-nexus, scrape_purchase, etc.)
- Has CLAUDE.md rules enforcing max 500 lines per Write call
- Wants full conversation preservation with smart chunked summaries (not just lossy session summaries)
