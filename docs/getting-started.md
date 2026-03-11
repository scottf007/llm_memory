# Getting Started

## Prerequisites

- **Python 3.10+** — check with `python3 --version`
- **Claude Code** — CLI (`claude`) or VS Code extension
- **jq** — JSON processor used by lifecycle hooks
- **sqlite3** CLI — used by hooks to query the memory database
- **curl** — used for downloading and auto-updates

Install missing dependencies:

```bash
# Debian/Ubuntu
sudo apt install jq sqlite3 python3 curl

# macOS
brew install jq sqlite3 python3 curl

# Fedora
sudo dnf install jq sqlite3 python3 curl
```

## Install

One-liner (no git clone needed):

```bash
curl -sL https://raw.githubusercontent.com/scottf007/llm_memory/main/install.sh | bash
```

Or clone and run manually:

```bash
git clone https://github.com/scottf007/llm_memory.git
cd llm_memory
./install.sh
```

The installer performs 8 steps:

1. Checks system dependencies (installs missing ones if possible)
2. Downloads the latest version from GitHub to `~/.claude/memory/lib/`
3. Creates a Python venv and installs pip dependencies
4. Creates `~/.claude/memory/` with `records/`, `transcripts/`, `config/`
5. Copies shared `CLAUDE.md` rules and applies them to `~/.claude/CLAUDE.md`
6. Registers the MCP server with Claude Code
7. Installs all 4 lifecycle hooks (SessionStart, PostToolUse, PreCompact, SessionEnd)
8. Scans existing transcripts and processes them into session logs

## Verify Installation

After installing, restart Claude Code. Then check:

```bash
# MCP server should be registered
claude mcp list

# Memory directory should exist
ls ~/.claude/memory/
# Expected: config/  lib/  memory.db  records/  transcripts/

# Hooks should be in your settings
cat ~/.claude/settings.json | jq '.hooks'
```

## What Happens on First Session

1. **Auto-update check** — the SessionStart hook checks GitHub for a newer version.
2. **Transcript sweep** — any existing JSONL transcripts from `~/.claude/projects/` are copied to `~/.claude/memory/transcripts/`.
3. **Project detection** — the project name is derived from your working directory (e.g., `/home/you/projects/myapp` becomes `myapp`).
4. **Narrative check** — if this is a known project with session logs but no narrative, Claude is instructed to generate one automatically from the raw transcripts.
5. **Memory tools available** — Claude can now use `memory_store`, `memory_search`, `memory_recent`, `memory_get`, `memory_connect`, `memory_explore`, and `memory_delete`.

On subsequent sessions, Claude loads the project narrative, recent notes, and session history automatically. You do not need to re-explain your project state.

## First Things to Try

- **Ask Claude a question, then correct it.** The correction gets stored as a note with high importance. Next session, Claude remembers the correction.
- **Make a design decision.** Tell Claude "let's go with approach X because Y." It stores the decision as a note.
- **End the session.** The SessionEnd hook archives the transcript and creates a session_log record automatically.
- **Start a new session.** The narrative and recent context load automatically. Ask Claude "what did we do last time?" to verify.

## Uninstall

```bash
claude mcp remove llm_memory --scope user
# Remove hook entries from ~/.claude/settings.json
rm -rf ~/.claude/memory/lib/        # remove code and venv
rm ~/.claude/memory/memory.db       # optional: delete local index
rm -rf ~/.claude/memory/records/    # optional: delete all memories
```
