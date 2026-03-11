# Architecture

## Overview

LLM Memory has three components: an MCP server, lifecycle hooks, and a web dashboard. All data is stored locally under `~/.claude/memory/`.

```
┌─────────────────┐    MCP (stdio)    ┌────────────────┐    ┌────────────┐
│   Claude Code    │◄────────────────►│  MCP Server     │───►│  records/  │
│                  │                   │  (server.py)    │    │  (JSON)    │
└────────┬────────┘                   └───────┬────────┘    └────────────┘
         │                                     │
         │ lifecycle hooks                     ▼
         │ + CLAUDE.md rules            ┌────────────┐
         ▼                              │  SQLite     │ ← derived index
  SessionStart ─── load context         │  + FTS5     │   (rebuildable)
  PostToolUse  ─── monitor activity     └────────────┘
  PreCompact   ─── save before compact        ▲
  SessionEnd   ─── archive transcript         │
                                        ┌─────┴──────┐
                                        │  Dashboard   │ (read-only)
                                        │  (FastAPI)   │
                                        └─────────────┘
```

## Component 1: MCP Server (`server.py`)

Claude Code spawns the server as a subprocess on startup using stdio transport. The server provides 7 tools:

| Tool | Purpose |
|------|---------|
| `memory_store` | Create a new memory (narrative, note, or session_log) |
| `memory_search` | Full-text search across all memories using FTS5 |
| `memory_recent` | List recent memories, optionally filtered by type/project |
| `memory_get` | Retrieve a specific memory by UUID |
| `memory_connect` | Link two memories with a relationship (supersedes, related_to) |
| `memory_explore` | Browse the knowledge graph from a starting memory |
| `memory_delete` | Remove a memory by UUID |

**Dual storage model:**
- Each memory is written as a JSON file in `records/` (source of truth)
- A SQLite database with FTS5 provides fast search (derived index)
- The database can be deleted and rebuilt at any time: `python3 server.py --rebuild`

**Schema:** The `memories` table stores uuid, type, content, project, session_id, created_at, importance (1-10), transcript_ref, and tags. The `connections` table links memories with typed relationships.

## Component 2: Lifecycle Hooks (4 scripts)

Shell scripts in `hooks/` that fire at key moments in a Claude Code session.

### SessionStart (`session_start.sh`)
Triggers on: startup, resume, compaction.
- Syncs `CLAUDE.md` from shared config if newer
- Checks GitHub for updates (background install if found)
- Sweeps `~/.claude/projects/` for uncollected transcripts
- Processes unprocessed transcripts into session_log records
- Loads project narrative, recent notes, and session logs into context
- Flags projects that need narratives generated

### PostToolUse (`session_monitor.sh`)
Triggers on: every tool invocation.
- Monitors session activity
- Records timestamps of memory saves for staleness tracking

### PreCompact (`pre_compact.sh`)
Triggers on: before context compaction.
- Preserves important context before Claude Code's lossy summarization

### SessionEnd (`session_end.sh`)
Triggers on: session termination (async).
- Copies the raw JSONL transcript to `~/.claude/memory/transcripts/`
- Extracts project name, turn count, and summary from the transcript
- Creates a `session_log` record as a JSON file in `records/`

## Component 3: Dashboard (`dashboard.py`)

A FastAPI application that provides a read-only web interface. Never modifies the database.

- **Timeline view** — filterable list of all memories with search, type/project filters
- **Graph view** — force-directed knowledge graph showing memory connections
- Uses Jinja2 templates from `templates/dashboard.html`

## Memory Types

| Type | Purpose | Created by |
|------|---------|------------|
| `narrative` | Per-project living document covering the full story | Claude (via memory_store) |
| `note` | Atomic fact, decision, correction, or preference | Claude (via memory_store) |
| `session_log` | Lightweight record that a session happened | SessionEnd hook (automatic) |

## Relationship Types

| Relationship | Purpose |
|-------------|---------|
| `supersedes` | Newer narrative replaces older one for the same project |
| `related_to` | General link between any two memories |

## Data Flow

1. **Session starts** → SessionStart hook loads existing context into Claude's prompt
2. **During session** → Claude reads/writes memories via MCP tools; PostToolUse monitors activity
3. **Before compaction** → PreCompact hook preserves key context
4. **After compaction** → SessionStart re-fires, reloads narrative and notes
5. **Session ends** → SessionEnd hook archives transcript and creates session_log
6. **Next session** → Cycle repeats; Claude has full context from previous sessions

## File Layout

```
~/.claude/memory/
├── memory.db              # Local SQLite index (never synced, rebuildable)
├── .stignore              # Excludes DB and venv from Syncthing
├── records/               # SYNCED — one JSON file per memory (source of truth)
│   ├── a1b2c3d4...json
│   └── ...
├── transcripts/           # SYNCED — raw session JSONL files
│   ├── session-abc123.jsonl
│   └── ...
├── config/
│   └── CLAUDE.md          # SYNCED — shared Claude rules across machines
└── lib/                   # Installed code (downloaded from GitHub)
    ├── server.py
    ├── dashboard.py
    ├── process_transcripts.py
    ├── hooks/
    │   ├── session_start.sh
    │   ├── session_end.sh
    │   ├── session_monitor.sh
    │   ├── pre_compact.sh
    │   └── install_hooks.sh
    ├── templates/
    ├── .venv/             # Python virtual environment (local only)
    └── VERSION            # Current installed commit SHA
```
