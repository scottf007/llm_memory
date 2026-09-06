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

Requires Python 3.10+, `jq`, `sqlite3`, `curl`, and Python's venv support
(`python3-venv` on Ubuntu/Debian). No Node.js needed. Restart Claude Code after installing.

<details>
<summary>Or install from source</summary>

```bash
git clone https://github.com/scottf007/llm_memory.git
cd llm_memory
./install.sh
```
</details>

## What It Does

**MCP Server** — A local Python server that Claude Code spawns on startup. Provides 4 read-only memory tools for searching and resuming project state. Backed by per-project JSON ledgers with a SQLite/FTS5 index for fast full-text search.

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

All four tools are read-only. Memory is written by the narrative pipeline (delta-extractor → merger → renderer), not by calling a tool mid-session.

| Tool | Description |
|------|-------------|
| `resume` | Return the last session's journal and a conversation-tail excerpt for a project — the fast path for "where did I leave off" |
| `project_lookup` | Fuzzy-search one project's ledger (decisions, learnings, done, goals, suggestions) |
| `memory_search` | Fuzzy-search ledger items across *all* projects, for when you don't know which project a fact is in |
| `narrative_coverage` | Compare on-disk session transcripts against what's already been merged into a project's narrative |

<!-- am-seed-capsule:start -->
**llm_memory** — persistent, local, cross-session project memory for CLI agents. Reach for it BEFORE starting work on an unfamiliar topic, and whenever you are about to reconstruct context by re-reading code or asking the user something the project already decided. All four MCP tools are read-only and cost milliseconds.
`resume` — picking up prior work: last session's journal plus a conversation tail. `project_lookup` — you know the project, want its decisions/learnings/done/goals/suggestions. `memory_search` — you do NOT know which project holds the fact; searches every project's ledger. `narrative_coverage` — how far behind a project's narrative is; run before `/narrative`.
Items are archived, not deleted, so an answer may describe a superseded decision — check status before acting on it.
<!-- am-seed-capsule:end -->

Each project's ledger holds five kinds of item: **decisions**, **learnings**, **done**, **goals**, **suggestions** — one JSON file per item under `~/.claude/memory/items/{project}/{kind}/`, indexed into SQLite/FTS5 for search. The per-project source of truth is `~/.claude/memory/projects/{project}.json`; a human-readable narrative is rendered from it to `{project}.narrative.md`.

## Dashboard

```bash
llm-memory-dashboard          # http://localhost:8765
llm-memory-dashboard 9000     # custom port
```

Timeline view with search and type/project filters. Force-directed knowledge graph showing how memories connect. Read-only — never modifies your data.

## Multi-Device Sync

LLM Memory syncs between machines via [Syncthing](https://syncthing.net/). The `projects/`, `items/`, `conversations/`, `transcripts/`, and `deltas/` directories sync automatically; each machine builds its own local, disposable SQLite index on startup.

```bash
python3 ~/.claude/memory/lib/setup_syncthing.py
```

<details>
<summary><strong>Architecture</strong></summary>

```
┌─────────────┐     MCP (stdio)     ┌──────────────┐     ┌────────────┐
│ Claude Code  │◄──────────────────►│ LLM Memory   │────►│ projects/  │
│              │                    │ server.py    │     │ (JSON)     │
└─────────────┘                    └──────┬───────┘     └────────────┘
       │                                  │
       │ lifecycle hooks + CLAUDE.md      ▼
       │                            ┌───────────┐
       ▼                            │ SQLite     │ ← derived index over items/
 SessionStart → auto-load narrative │ + FTS5     │   (rebuildable)
 PostToolUse  → monitor transcript  └───────────┘
 PreCompact   → save before compact
 SessionEnd   → archive transcript
```

**The per-project JSON ledger is the source of truth.** Each project has one file, `~/.claude/memory/projects/{project}.json`, holding its decisions, learnings, done items, goals, suggestions, and session history. Per-item files under `~/.claude/memory/items/{project}/{kind}/` are derived from it and feed the SQLite/FTS5 index — the database itself is disposable, delete it anytime and rebuild with `python3 ~/.claude/memory/lib/indexer.py`.

**Items use short kind-prefixed ids** (e.g. `dec-00245540.json`), not database row numbers — one JSON file per item, so item history is diffable and mergeable per-file.

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
  projects/       ← one JSON file per project (source of truth, synced): decisions, learnings, done, goals, suggestions, session history
  items/          ← one JSON file per ledger item, derived from projects/ (synced) — feeds the FTS5 index
  conversations/  ← rendered per-session Markdown, tool noise stripped (synced)
  transcripts/    ← raw session JSONL files (synced)
  deltas/         ← per-session extraction deltas consumed by the merger (synced)
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
- Rebuild anytime: `python3 ~/.claude/memory/lib/indexer.py`
- The database is disposable — the JSON files under `projects/` and `items/` are the real data

## Uninstall

```bash
claude mcp remove llm_memory --scope user
# Remove hook entries from ~/.claude/settings.json
rm -rf ~/.claude/memory/lib/
# Optionally delete all stored memories:
# rm -rf ~/.claude/memory/projects/ ~/.claude/memory/items/ ~/.claude/memory/conversations/ ~/.claude/memory/transcripts/ ~/.claude/memory/deltas/
```

## License

[MIT](LICENSE)
