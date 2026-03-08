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

LLM Memory is an MCP (Model Context Protocol) server that gives Claude Code persistent memory tools. Claude stores and retrieves memories itself — no external AI service needed, Claude IS the intelligence layer.

```
┌─────────────┐     MCP (stdio)     ┌──────────────┐     ┌──────────┐
│ Claude Code  │◄──────────────────►│ LLM Memory   │────►│ SQLite   │
│              │                    │ server.py    │     │ + FTS5   │
└─────────────┘                    └──────────────┘     └──────────┘
       │
       │ reads on startup
       ▼
 ~/.claude/CLAUDE.md
 (memory protocol rules)
```

### Three components:

1. **MCP Server** (`server.py`) — Python server with SQLite + FTS5. Claude Code spawns it automatically. Provides 7 tools: `memory_store`, `memory_search`, `memory_recent`, `memory_get`, `memory_connect`, `memory_explore`, `memory_delete`.

2. **Global CLAUDE.md** — Rules that tell Claude when and how to use memory. Loaded into every session automatically.

3. **Lifecycle Hooks** — Bash scripts for session lifecycle events:
   - **SessionStart** — Auto-loads recent memories into context
   - **PostToolUse** — Monitors transcript size, warns when context is getting large
   - **PreCompact** — Instructs Claude to save progress before compaction
   - **SessionEnd** — Auto-saves session summary from transcript

4. **Web Dashboard** (`dashboard.py`) — Browser-based read-only viewer with timeline and knowledge graph visualization at `http://localhost:8765`.

## Memory Tools

| Tool | Purpose |
|------|---------|
| `memory_store` | Save a memory with type, project, importance, transcript_ref, and optional connections |
| `memory_search` | Full-text search across all memories |
| `memory_recent` | Get the N most recent memories, optionally filtered by project/type |
| `memory_get` | Fetch a specific memory by ID with all its connections |
| `memory_connect` | Link two memories with a relationship (supports, contradicts, supersedes, etc.) |
| `memory_explore` | Traverse the knowledge graph from a starting memory up to 3 hops deep |
| `memory_delete` | Remove a memory and its connections |

### Memory Types

- `decision` — A choice that was made and why
- `insight` — Something learned about the codebase, architecture, or domain
- `progress` — What was accomplished in a task
- `correction` — A mistake that was corrected (stored with high importance so it's not repeated)
- `session_summary` — End-of-session summary of what happened
- `chunk_summary` — Mid-session summary of a work chunk (numbered, linked, captures decisions/outcomes)
- `note` — General information worth remembering

### Relationship Types

- `supports` — Memory A provides evidence for Memory B
- `contradicts` — Memory A conflicts with Memory B
- `supersedes` — Memory A replaces Memory B (newer decision)
- `implements` — Memory A is an implementation of Memory B
- `depends_on` — Memory A requires Memory B
- `related_to` — General relationship

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

## How It Behaves In Practice

**Session start:** Claude reads `~/.claude/CLAUDE.md`, sees the memory protocol rules, and calls `memory_recent` or `memory_search` to load relevant context from previous sessions.

**During work:** At task boundaries (finishing a subtask, making a decision, getting corrected), Claude calls `memory_store` to persist important information.

**Large file generation:** Instead of outputting a 17,000-line file and crashing, Claude writes one section at a time using Write/Edit tools.

**Session getting long:** The hook warns Claude when the transcript is large. Claude saves current progress with `memory_store` before context compaction hits.

**Next session:** Claude searches memories for the project it's working on and picks up where it left off, even though the conversation is gone.

## Three-Layer Memory

LLM Memory uses a three-layer approach to preserve context:

```
Layer 1: Raw transcript (archived JSONL, never modified)
Layer 2: Chunk summaries (numbered, linked, filtered mid-session snapshots)
Layer 3: Extracted signals (decisions, learnings, corrections, insights)
```

**Layer 1 — Raw Transcripts.** The SessionEnd hook automatically copies the raw JSONL transcript to `~/.claude/memory/transcripts/`. These are the unmodified source of truth.

**Layer 2 — Chunk Summaries.** Claude creates `chunk_summary` memories at natural breakpoints (topic changes, subtask completions, before compaction). Each chunk captures decisions, outcomes, and learnings while filtering out noise like circular debugging or dead-end research. Chunks are numbered per session, linked via `memory_connect`, and reference the raw transcript via `transcript_ref`.

**Layer 3 — Extracted Signals.** Individual `decision`, `insight`, `correction`, and other memory types that capture specific high-value information. These are the most durable and searchable.

The CLAUDE.md rules (see `claude-rules-example.md`) tell Claude when to create each layer.

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
