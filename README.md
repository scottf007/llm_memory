# LLM Memory

Persistent memory for Claude Code. Stores project narratives, decisions, and session history across sessions — all local, no cloud services. Syncs between machines via Syncthing.

## The Problem

Claude Code forgets everything between sessions. Within a session, long conversations trigger "compaction" which lossy-summarizes older messages. If you're working on a large project across many sessions, you lose context constantly.

Common symptoms:
- Claude repeats mistakes it was corrected on last session
- Claude doesn't know what was decided yesterday
- You spend the first 10 minutes of every session re-explaining project state
- Ideas get suggested but never acted on, and nobody remembers them

## How It Works

```
┌─────────────┐     MCP (stdio)     ┌──────────────┐     ┌───────────┐
│ Claude Code  │◄──────────────────►│ LLM Memory   │────►│ records/  │
│              │                    │ server.py    │     │ (JSON)    │
└─────────────┘                    └──────┬───────┘     └───────────┘
       │                                  │
       │ lifecycle hooks + CLAUDE.md      ▼
       │                            ┌───────────┐
       ▼                            │ SQLite     │ ← derived index
 SessionStart → auto-load narrative │ + FTS5     │   (rebuildable)
 PostToolUse  → monitor transcript  └───────────┘
 PreCompact   → save before compact
 SessionEnd   → archive transcript
```

There are three layers:

### 1. MCP Server (`server.py`)

Claude Code spawns it as a subprocess on startup. Provides 7 memory tools: `memory_store`, `memory_search`, `memory_recent`, `memory_get`, `memory_connect`, `memory_explore`, `memory_delete`. Claude uses these to read and write memories during a session.

### 2. Lifecycle Hooks

Shell scripts that run automatically at key moments in a Claude Code session:

- **SessionStart** — checks for updates from GitHub, sweeps for uncollected transcripts, loads the project narrative + recent notes into context, flags projects that need narratives
- **PostToolUse** — monitors the session transcript and tracks save timestamps
- **PreCompact** — saves context before Claude Code's compaction lossy-summarizes the conversation
- **SessionEnd** — archives the raw JSONL transcript and creates a session_log record

### 3. Shared Config (`CLAUDE.md`)

A set of rules injected into every Claude Code session via `~/.claude/CLAUDE.md`. These tell Claude how to use the memory tools — when to search, when to store, how to write narratives. The rules sync between machines via `~/.claude/memory/config/CLAUDE.md`.

### Memory Architecture

**Records are JSON files.** Each memory is a JSON file in `~/.claude/memory/records/`. The SQLite database is a derived index rebuilt from these files on server startup. The database is disposable — the files are the source of truth.

**UUIDs, not integers.** All records use 32-character hex UUIDs. No collisions across machines.

**3 memory types:**
- **narrative** — per-project living document. One per project, updated over time. Written from raw JSONL transcripts.
- **note** — atomic fact, decision, correction, preference, or insight. Tagged for searchability.
- **session_log** — lightweight record that a session happened. Created automatically by hooks.

**2 relationship types:** `supersedes` (narrative versioning), `related_to` (linked notes).

**Project narratives** are the core feature. When you start a session in a project directory with no narrative, Claude reads the raw JSONL transcripts and generates one with: session history, decisions made, gotchas, current state, outstanding items, and direction. Nothing gets lost between sessions.

**Raw JSONL transcripts** are archived to `~/.claude/memory/transcripts/`. Narratives are always written from these, never from summaries — summaries are lossy.

## Requirements

- Python 3.10+
- Claude Code (CLI or VS Code extension)
- **jq** — used by lifecycle hooks to parse JSON (`sudo apt install jq` / `brew install jq`)
- **sqlite3** CLI — used by hooks to query the database (`sudo apt install sqlite3` / `brew install sqlite3`)
- **curl** — used for downloading and auto-updates
- No Node.js, no cloud services, no external APIs

## Install

One-liner (no git clone needed):

```bash
curl -sL https://raw.githubusercontent.com/scottf007/llm_memory/main/install.sh | bash
```

Or clone and run:

```bash
git clone https://github.com/scottf007/llm_memory.git
cd llm_memory
./install.sh
```

The installer will:
1. Check system dependencies (jq, sqlite3, python3, curl)
2. Download the latest version from GitHub to `~/.claude/memory/lib/`
3. Create a Python venv and install dependencies
4. Create the memory directory at `~/.claude/memory/` (with `records/`, `transcripts/`, `config/`)
5. Copy shared config (`CLAUDE.md`) and apply it
6. Register the MCP server with Claude Code
7. Install all 4 lifecycle hooks
8. Scan existing transcripts and process them into session logs

Restart Claude Code after installing. Start a session in any project directory — the narrative system activates automatically.

## What Happens on Session Start

1. **Auto-update** — checks GitHub for a newer version. If found, downloads and installs in the background.
2. **Transcript sweep** — scans `~/.claude/projects/` for any JSONL transcripts not yet archived to `~/.claude/memory/transcripts/` and copies them. This catches transcripts from before the hooks were installed or from sessions where hooks failed.
3. **Project detection** — derives the project name from the current working directory.
4. **Narrative loading** — if a narrative exists for this project, loads it into Claude's context along with recent notes and session logs.
5. **Narrative generation** — if transcripts exist but no narrative, tells Claude to generate one immediately from the raw JSONL files.
6. **Staleness check** — if new sessions have happened since the last narrative update, suggests updating it.
7. **Cross-project check** — lists any other projects that have session logs but no narrative yet.

## Multi-Device Sync

LLM Memory is designed for multi-device use via [Syncthing](https://syncthing.net/):

1. Install llm_memory on each machine (`curl ... | bash` or `./install.sh`)
2. Set up Syncthing: `python3 ~/.claude/memory/lib/setup_syncthing.py`
3. The `.stignore` file (created by install) excludes `memory.db` and the venv from sync
4. Each machine rebuilds its own SQLite index from the shared record files on server startup
5. No conflicts — every record has a globally unique UUID filename

What syncs:
- `records/` — one JSON file per memory (the source of truth)
- `transcripts/` — raw session JSONL files
- `config/CLAUDE.md` — shared Claude rules across machines

What stays local:
- `memory.db` — each machine builds its own index
- `lib/.venv/` — each machine has its own Python environment

If the database gets out of sync, rebuild it:
```bash
python3 ~/.claude/memory/lib/server.py --rebuild
```

## Web Dashboard

Browse and visualize your memories in a browser:

```bash
llm-memory-dashboard          # http://localhost:8765
llm-memory-dashboard 9000     # custom port
```

- **Timeline view** — filterable list of all memories with search, type/project filters
- **Graph view** — force-directed knowledge graph showing memory connections
- Read-only — never modifies the database

## Data Storage

Everything is local:

```
~/.claude/memory/
  memory.db              ← local index (never synced, can be rebuilt)
  .stignore              ← excludes DB and venv from Syncthing
  lib/                   ← installed code (downloaded from GitHub)
    server.py            ← MCP server
    hooks/               ← lifecycle hook scripts
    dashboard.py         ← web dashboard
    .venv/               ← Python virtual environment
    VERSION              ← current installed commit SHA
  records/               ← synced — one JSON file per memory
  transcripts/           ← synced — raw session JSONL files
  config/
    CLAUDE.md            ← synced — shared Claude rules
```

```bash
# Back up (just the records — they ARE the data)
cp -r ~/.claude/memory/records/ ~/backup/llm-memory-records/

# Rebuild database from record files
python3 ~/.claude/memory/lib/server.py --rebuild

# Inspect
sqlite3 ~/.claude/memory/memory.db \
  "SELECT substr(uuid,1,8), type, project, substr(content,1,60) FROM memories ORDER BY created_at DESC LIMIT 20;"
```

## Uninstall

```bash
claude mcp remove llm_memory --scope user
# Remove hook entries from ~/.claude/settings.json
rm -rf ~/.claude/memory/lib/        # remove installed code and venv
rm ~/.claude/memory/memory.db       # optional — delete local index
rm -rf ~/.claude/memory/records/    # optional — delete all stored memories
```

## License

MIT
