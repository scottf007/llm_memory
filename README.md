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

**MCP server** (`server.py`) — Claude Code spawns it as a subprocess on startup. 7 tools: `memory_store`, `memory_search`, `memory_recent`, `memory_get`, `memory_connect`, `memory_explore`, `memory_delete`.

**Records are JSON files.** Each memory is stored as a JSON file in `~/.claude/memory/records/`. The SQLite database is a derived index that can be rebuilt from these files at any time: `python server.py --rebuild`. This means the database is disposable — the files are the source of truth.

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
- No Node.js, no cloud services, no external APIs

## Install

```bash
git clone https://github.com/scottf007/llm_memory.git ~/projects/llm_memory
cd ~/projects/llm_memory
./install.sh
```

This single command will:
1. Create a Python venv and install dependencies
2. Create the memory directory at `~/.claude/memory/` (with `records/`, `transcripts/`, `config/`)
3. Copy shared config (`CLAUDE.md`) to `~/.claude/memory/config/` and apply it
4. Register the MCP server with Claude Code
5. Install all 4 lifecycle hooks
6. Apply shared settings from `settings.yaml`
7. Initialize the database
8. Scan existing transcripts and process them into session logs

Restart Claude Code. Start a session in any project directory — the narrative system activates automatically.

## What Happens on Session Start

1. Hook detects which project you're in (from cwd)
2. If transcripts exist but no narrative: auto-processes transcripts into session_logs, then tells Claude to generate a narrative from the raw JSONL files
3. If a narrative exists: loads it into context along with recent notes and session_logs
4. If new sessions happened since the last narrative update: suggests updating it

## Multi-Device Sync

LLM Memory is designed for multi-device use via [Syncthing](https://syncthing.net/):

1. Install llm_memory on each machine (run `install.sh` on each)
2. Set up Syncthing to share `~/.claude/memory/` between your devices
3. The `.stignore` file (created by install) excludes `memory.db` from sync
4. Each machine rebuilds its own SQLite index from the shared record files
5. No conflicts — every record has a globally unique UUID filename

What syncs:
- `records/` — one JSON file per memory (the source of truth)
- `transcripts/` — raw session JSONL files
- `config/CLAUDE.md` — shared Claude rules across machines

What stays local:
- `memory.db` — each machine builds its own index

If the database gets out of sync, rebuild it: `python server.py --rebuild`

## Web Dashboard

Browse and visualize your memories in a browser:

```bash
./dashboard.sh          # http://localhost:8765
./dashboard.sh 9000     # custom port
```

- **Timeline view** — filterable list of all memories with search, type/project filters
- **Graph view** — force-directed knowledge graph showing memory connections
- Read-only — never modifies the database

## Data Storage

Everything is local:

```
~/.claude/memory/
  memory.db              ← local index (never synced, can be rebuilt)
  .stignore              ← excludes DB from Syncthing
  records/               ← synced — one JSON file per memory
  transcripts/           ← synced — raw session JSONL files
  config/
    CLAUDE.md            ← synced — shared Claude rules
```

```bash
# Back up (just the records — they ARE the data)
cp -r ~/.claude/memory/records/ ~/backup/llm-memory-records/

# Rebuild database from record files
python server.py --rebuild

# Inspect
sqlite3 ~/.claude/memory/memory.db \
  "SELECT substr(uuid,1,8), type, project, substr(content,1,60) FROM memories ORDER BY created_at DESC LIMIT 20;"
```

## Uninstall

```bash
claude mcp remove llm_memory --scope user
# Remove hook entries from ~/.claude/settings.json
rm -rf ~/projects/llm_memory/.venv
rm ~/.claude/memory/memory.db       # optional — delete local index
rm -rf ~/.claude/memory/records/    # optional — delete all stored memories
```

## License

MIT
