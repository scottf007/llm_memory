# LLM Memory

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-green.svg)](https://python.org)
[![Tests](https://img.shields.io/github/actions/workflow/status/scottf007/llm_memory/tests.yml?label=tests)](https://github.com/scottf007/llm_memory/actions)

**Persistent memory for Claude Code. Every session starts where the last one left off.**

## The Problem

Claude Code forgets everything between sessions. Long conversations get "compacted" — lossy-summarized into oblivion. If you work on a project across many sessions, you lose decisions, corrections, and context constantly. You spend the first 10 minutes re-explaining where you left off. Mistakes get repeated. Ideas get lost.

LLM Memory fixes this. It gives Claude Code a local, persistent memory that survives across sessions and syncs across machines — no cloud services, no external APIs, everything on your machine.

## Quick Install

```bash
curl -sL https://raw.githubusercontent.com/scottf007/llm_memory/main/install.sh | bash
```

Requires Python 3.10+, `jq`, `sqlite3`, and `curl`. No Node.js needed. Restart Claude Code after installing.

<details>
<summary>Or install from source</summary>

```bash
git clone https://github.com/scottf007/llm_memory.git
cd llm_memory
./install.sh
```
</details>

## What It Does

**MCP Server** — A local Python server that Claude Code spawns on startup. Provides 7 memory tools for storing, searching, and connecting memories. Backed by JSON files with a SQLite/FTS5 index for fast full-text search.

**Lifecycle Hooks** — Shell scripts that fire automatically at session start, before compaction, and at session end. They load project context, archive raw transcripts, and ensure nothing falls through the cracks.

**Web Dashboard** — A read-only browser UI for browsing your memories as a searchable timeline or interactive knowledge graph.

## How It Works

```
Session starts
  --> Hooks sweep uncollected transcripts, load project narrative + recent notes
  --> Claude knows exactly where you left off

You work normally
  --> Claude stores decisions, corrections, and insights as memories
  --> Hooks monitor the session and track progress

Session ends
  --> Raw JSONL transcript archived
  --> Session log created automatically
  --> Next session picks up right where you stopped
```

**Project narratives** are the core feature. Each project gets a living document generated from your raw JSONL transcripts, covering session history, decisions, gotchas, current state, and next steps. When you start a session in a project with no narrative, Claude reads the transcripts and writes one automatically.

## Memory Tools

| Tool | Description |
|------|-------------|
| `memory_store` | Save a narrative, note, or session log |
| `memory_search` | Full-text search across all memories |
| `memory_recent` | List recent memories, filtered by project or type |
| `memory_get` | Retrieve a specific memory by UUID |
| `memory_connect` | Create a relationship between two memories |
| `memory_explore` | Traverse the memory graph from a starting point |
| `memory_delete` | Remove a memory |

Three memory types: **narrative** (per-project living document), **note** (atomic fact, decision, or correction), **session_log** (automatic session record).

Two relationship types: **supersedes** (narrative versioning) and **related_to** (linked notes).

## Dashboard

```bash
llm-memory-dashboard          # http://localhost:8765
llm-memory-dashboard 9000     # custom port
```

Timeline view with search and type/project filters. Force-directed knowledge graph showing how memories connect. Read-only — never modifies your data.

## Multi-Device Sync

LLM Memory syncs between machines via [Syncthing](https://syncthing.net/). JSON record files and transcripts sync automatically; each machine builds its own SQLite index on startup. No merge conflicts — every record uses a globally unique UUID.

```bash
python3 ~/.claude/memory/lib/setup_syncthing.py
```

<details>
<summary><strong>Architecture</strong></summary>

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

**Records are JSON files.** Each memory is a JSON file in `~/.claude/memory/records/`. The SQLite database is a derived index rebuilt from these files on server startup. The database is disposable — delete it anytime and rebuild.

**UUIDs, not integers.** All records use 32-character hex UUIDs. No collisions across machines.

**Narratives are written from raw transcripts**, not from summaries. Transcripts capture the user's exact words, the debugging loops, the moments where direction changed. Summaries are lossy.

**What the installer does:** checks dependencies, downloads code to `~/.claude/memory/lib/`, creates a Python venv, registers the MCP server with Claude Code, installs all 4 lifecycle hooks, and processes any existing transcripts into session logs.

**What happens on session start:**
1. Auto-update check against GitHub
2. Transcript sweep — collects any JSONL files not yet archived
3. Project detection from working directory
4. Narrative + recent notes loaded into context
5. If no narrative exists but transcripts do, Claude generates one automatically
6. Staleness check — flags narratives that need updating

**Data layout:**

```
~/.claude/memory/
  records/        ← one JSON file per memory (source of truth, synced)
  transcripts/    ← raw session JSONL files (synced)
  config/         ← shared CLAUDE.md rules (synced)
  memory.db       ← local search index (rebuilt on startup, never synced)
  lib/            ← installed code + Python venv (not synced)
```

</details>

## Configuration

LLM Memory injects rules into `~/.claude/CLAUDE.md` that tell Claude when and how to use memory tools — when to search for past context, when to store decisions, how to write narratives. The source of these rules lives at `~/.claude/memory/config/CLAUDE.md` and syncs between machines via Syncthing.

## Development

```bash
git clone https://github.com/scottf007/llm_memory.git
cd llm_memory
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install pytest
python3 -m pytest tests/
```

## Troubleshooting

**MCP server not loading**
- Run `claude mcp list` and verify `llm_memory` appears
- Check Python version: `python3 --version` (needs 3.10+)
- Re-register: `claude mcp remove llm_memory --scope user` then re-run `install.sh`

**Hooks not firing**
- Check `~/.claude/settings.json` for hook entries
- Verify scripts are executable: `ls -la ~/.claude/memory/lib/hooks/`
- Re-run `install.sh` to reinstall hooks

**Permission errors**
- `chmod +x ~/.claude/memory/lib/hooks/*.sh`

**Database issues**
- Rebuild anytime: `python3 ~/.claude/memory/lib/server.py --rebuild`
- The database is disposable — JSON record files are the real data

## Uninstall

```bash
claude mcp remove llm_memory --scope user
# Remove hook entries from ~/.claude/settings.json
rm -rf ~/.claude/memory/lib/
# Optionally delete all stored memories:
# rm -rf ~/.claude/memory/records/ ~/.claude/memory/transcripts/
```

## License

[MIT](LICENSE)
