# LLM Memory

Persistent memory for Claude Code. Stores decisions, insights, and progress across sessions with full-text search and a knowledge graph — all local, no cloud services.

## The Problem

Claude Code forgets everything between sessions. Within a session, long conversations trigger "compaction" which lossy-summarizes older messages. If you're working on a large project across many sessions, you lose context constantly.

Common symptoms:
- Claude repeats mistakes it was corrected on last session
- Large file generation (kanban boards, specs) breaks mid-output with no way to resume
- Claude doesn't know what was decided yesterday
- You spend the first 10 minutes of every session re-explaining project state

## How It Works

```
┌─────────────┐     MCP (stdio)     ┌──────────────┐     ┌──────────┐
│ Claude Code  │◄──────────────────►│ LLM Memory   │────►│ SQLite   │
│              │                    │ server.py    │     │ + FTS5   │
└─────────────┘                    └──────────────┘     └──────────┘
       │
       │ lifecycle hooks + CLAUDE.md rules
       ▼
 SessionStart → auto-load memories
 PostToolUse  → monitor transcript size
 PreCompact   → save progress before compaction
 SessionEnd   → archive transcript + save summary
```

1. **It's an MCP server** (`server.py`) — a Python process that Claude Code spawns automatically on startup and talks to via stdin/stdout using the Model Context Protocol.

2. **SQLite is the backend** — all memories live in `~/.claude/memory/memory.db` with WAL mode, foreign keys, and FTS5 full-text search. Everything local, nothing leaves your machine.

3. **7 memory tools** — `memory_store`, `memory_search`, `memory_recent`, `memory_get`, `memory_connect`, `memory_explore`, `memory_delete`. Claude calls these directly during conversations.

4. **7 memory types** — `decision`, `insight`, `progress`, `correction`, `session_summary`, `chunk_summary`, `note`. Each serves a different purpose — corrections get stored at high importance so mistakes aren't repeated.

5. **Knowledge graph** — memories can be linked with 6 relationship types (`supports`, `contradicts`, `supersedes`, `implements`, `depends_on`, `related_to`). `memory_explore` traverses connections up to 3 hops deep.

6. **Three-layer memory architecture** — Layer 1: raw transcripts (archived JSONL, unmodified). Layer 2: chunk summaries (filtered, numbered mid-session snapshots). Layer 3: extracted signals (individual decisions, insights, corrections).

7. **Chunk summaries** — Claude creates these at natural breakpoints (topic changes, subtask completions, before compaction). They capture decisions/outcomes/learnings while filtering out noise like circular debugging and dead-end research.

8. **Transcript archiving** — the SessionEnd hook copies the raw JSONL transcript to `~/.claude/memory/transcripts/` before anything else. Chunk summaries can reference these via `transcript_ref`.

9. **Deduplication** — `memory_store` checks if similar content (first 100 chars) was stored in the last hour and skips duplicates. Prevents Claude from flooding the database with repeated saves.

10. **4 lifecycle hooks** — SessionStart (loads memories into context), PostToolUse (warns at 300KB/500KB transcript size), PreCompact (tells Claude to save progress before compaction), SessionEnd (archives transcript + auto-saves summary).

11. **SessionStart is context-aware** — on fresh start, it loads recent session summaries + chunk summaries + high-importance memories. After compaction, it loads the most recent summary + recent chunks to rebuild lost context.

12. **CLAUDE.md rules** — behavioral instructions in `~/.claude/CLAUDE.md` that tell Claude *when* and *how* to use memory. When to store, when to search, how to create chunk summaries, how to handle large files. Claude follows these every session.

13. **Web dashboard** (`dashboard.py`) — FastAPI app at `localhost:8765` with two views: Timeline (filterable/searchable card list of all memories) and Graph (force-directed vis.js visualization of the knowledge graph). Read-only, never modifies the DB.

14. **Auto-migration** — `init_db()` detects old schemas and adds missing columns (like `transcript_ref`). Existing databases upgrade seamlessly without losing data.

15. **One-command install** — `./install.sh` creates a venv, installs deps, and registers the MCP server via `claude mcp add-json`. `./hooks/install_hooks.sh` adds all 4 hooks. Copy `claude-rules-example.md` to `~/.claude/CLAUDE.md` and restart Claude Code — done.

## Requirements

- Python 3.10+
- Claude Code (CLI or VS Code extension)
- No Node.js, no cloud services, no external APIs

## Install

```bash
git clone <this-repo> ~/projects/llm_memory
cd ~/projects/llm_memory
./install.sh
```

This will:
1. Create a Python venv and install the `mcp` package
2. Create the memory database directory at `~/.claude/memory/`
3. Register the MCP server with Claude Code (stored in `~/.claude.json`)

### Install lifecycle hooks (optional but recommended)

```bash
./hooks/install_hooks.sh
```

This adds four hooks to your Claude Code settings:
- **SessionStart** — Auto-loads recent high-importance memories when a session begins
- **PostToolUse** — Monitors transcript size, warns at 300KB and 500KB
- **PreCompact** — Tells Claude to save progress before context compaction
- **SessionEnd** — Auto-saves a session summary from the transcript

### Set up global CLAUDE.md

Copy the included rules to your global CLAUDE.md:

```bash
cp claude-rules-example.md ~/.claude/CLAUDE.md
```

Or if you already have a global CLAUDE.md, merge the rules manually. The key sections are:

- **Large Output Rules** — Prevents Claude from generating massive files in one shot
- **Memory Protocol** — Tells Claude when to store and search memories
- **Context Management** — Rules for managing context window usage
- **File Generation** — Chunked writing rules for large documents

### Restart Claude Code

After installing, restart Claude Code (close and reopen, or restart the CLI). The MCP server starts automatically.

## What Changes in Your Setup

### ~/.claude.json

The install script registers the MCP server via `claude mcp add-json`:

```json
{
  "mcpServers": {
    "llm_memory": {
      "type": "stdio",
      "command": "/path/to/llm_memory/.venv/bin/python3",
      "args": ["/path/to/llm_memory/server.py"]
    }
  }
}
```

Claude Code will spawn `server.py` as a subprocess when it starts and communicate via stdin/stdout using the MCP protocol.

### ~/.claude/settings.json

The hook install script adds lifecycle hooks for SessionStart, PostToolUse, PreCompact, and SessionEnd. See `hooks/install_hooks.sh` for the full configuration.

### ~/.claude/CLAUDE.md

Global rules loaded into every Claude Code session. These are instructions for Claude, not documentation. They tell Claude to:

- Use `memory_store` at task boundaries
- Use `memory_search` before starting work on a topic
- Never generate files larger than 500 lines in one Write call
- Write large documents in chunks (one section at a time)

### ~/.claude/memory/memory.db

SQLite database where all memories are stored. Created automatically on first run. You can inspect it directly:

```bash
sqlite3 ~/.claude/memory/memory.db "SELECT id, type, project, substr(content, 1, 80) FROM memories ORDER BY created_at DESC LIMIT 20;"
```

## Web Dashboard

Browse and visualize your memories in a browser:

```bash
./dashboard.sh          # http://localhost:8765
./dashboard.sh 9000     # custom port
```

Features:
- **Timeline view** — Filterable list of all memories with search, type/project filters
- **Graph view** — Force-directed knowledge graph showing memory connections
- Read-only — the dashboard never modifies the database

Requires `fastapi` and `jinja2` (installed automatically by `install.sh`).

## Data Storage

Everything is local:
- Database: `~/.claude/memory/memory.db`
- Archived transcripts: `~/.claude/memory/transcripts/`
- No data leaves your machine
- No API keys needed
- No cloud service dependencies

Back up the database file if you want to preserve your memories:

```bash
cp ~/.claude/memory/memory.db ~/.claude/memory/memory.db.bak
```

## Uninstall

Remove the MCP server (`claude mcp remove llm_memory --scope user`) and hook entries from `~/.claude/settings.json`, delete the venv, and optionally delete the database:

```bash
rm -rf /path/to/llm_memory/.venv
rm ~/.claude/memory/memory.db  # optional — deletes all memories
```

## License

MIT
