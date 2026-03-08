# LLM Memory

Persistent memory for Claude Code. Stores project narratives, decisions, and session history across sessions — all local, no cloud services.

## The Problem

Claude Code forgets everything between sessions. Within a session, long conversations trigger "compaction" which lossy-summarizes older messages. If you're working on a large project across many sessions, you lose context constantly.

Common symptoms:
- Claude repeats mistakes it was corrected on last session
- Claude doesn't know what was decided yesterday
- You spend the first 10 minutes of every session re-explaining project state
- Ideas get suggested but never acted on, and nobody remembers them

## How It Works

```
┌─────────────┐     MCP (stdio)     ┌──────────────┐     ┌──────────┐
│ Claude Code  │◄──────────────────►│ LLM Memory   │────►│ SQLite   │
│              │                    │ server.py    │     │ + FTS5   │
└─────────────┘                    └──────────────┘     └──────────┘
       │
       │ lifecycle hooks + CLAUDE.md rules
       ▼
 SessionStart → auto-load narrative + notes, trigger narrative generation
 PostToolUse  → monitor transcript size, remind to save
 PreCompact   → save important notes before compaction
 SessionEnd   → archive transcript + create session_log
```

**MCP server** (`server.py`) — Claude Code spawns it as a subprocess on startup. 7 tools: `memory_store`, `memory_search`, `memory_recent`, `memory_get`, `memory_connect`, `memory_explore`, `memory_delete`.

**3 memory types:**
- **narrative** — per-project living document. One per project, updated over time. New versions supersede old ones. Written from raw JSONL transcripts.
- **note** — atomic fact, decision, correction, preference, or insight. Tagged for searchability.
- **session_log** — lightweight record that a session happened. Created automatically by hooks.

**2 relationship types:** `supersedes` (narrative versioning), `related_to` (linked notes).

**Project narratives** are the core feature. When you start a session in a project directory with no narrative, Claude reads the raw JSONL transcripts and generates one with: session history, decisions made, gotchas, current state, outstanding items, and direction. Nothing gets lost between sessions.

**Raw JSONL transcripts** are the source of truth. Every session's transcript is archived to `~/.claude/memory/transcripts/`. Narratives are always written from these, never from summaries — summaries are lossy.

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
2. Create the memory directory at `~/.claude/memory/`
3. Register the MCP server with Claude Code
4. Install all 4 lifecycle hooks
5. Initialize the database
6. Scan existing transcripts and report what projects it finds

Then copy the CLAUDE.md rules:

```bash
cp claude-rules-example.md ~/.claude/CLAUDE.md
```

Or merge into your existing `~/.claude/CLAUDE.md`. These rules tell Claude when and how to use the memory tools.

Restart Claude Code. Start a session in any project directory — the narrative system activates automatically.

## What Happens on Session Start

1. Hook detects which project you're in (from cwd)
2. If transcripts exist but no narrative: auto-processes transcripts into session_logs, then tells Claude to generate a narrative from the raw JSONL files
3. If a narrative exists: loads it into context along with recent notes and session_logs
4. If new sessions happened since the last narrative update: suggests updating it

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
- Database: `~/.claude/memory/memory.db`
- Archived transcripts: `~/.claude/memory/transcripts/`
- No data leaves your machine
- No API keys needed

```bash
# Back up
cp ~/.claude/memory/memory.db ~/.claude/memory/memory.db.bak

# Inspect
sqlite3 ~/.claude/memory/memory.db "SELECT id, type, project, substr(content, 1, 80) FROM memories ORDER BY created_at DESC LIMIT 20;"
```

## Uninstall

```bash
claude mcp remove llm_memory --scope user
# Remove hook entries from ~/.claude/settings.json
rm -rf ~/projects/llm_memory/.venv
rm ~/.claude/memory/memory.db  # optional — deletes all memories
```

## License

MIT
