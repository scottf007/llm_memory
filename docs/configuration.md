# Configuration

## CLAUDE.md Rules

The file `~/.claude/CLAUDE.md` contains instructions that Claude reads at the start of every session. LLM Memory ships a default version that teaches Claude how to use the memory tools.

The canonical copy lives at `~/.claude/memory/config/CLAUDE.md`. On each session start, the hook syncs this to `~/.claude/CLAUDE.md` if the config version is newer.

To customize:

```bash
# Edit the shared config (syncs to all machines via Syncthing)
$EDITOR ~/.claude/memory/config/CLAUDE.md
```

Key sections in the rules file:

- **Memory Protocol** — tells Claude which MCP tools exist and how to call them
- **Memory types** — explains narrative, note, and session_log
- **When to use memory tools** — search before starting work, store corrections, store decisions
- **Project Narratives** — format and when to generate/update them
- **Context Management** — rules for reading files efficiently
- **Large Output Rules** — prevents Claude from generating excessively long outputs

## settings.yaml

The `settings.yaml` file defines tool permissions and hook configurations. It is applied to `~/.claude/settings.json` during installation.

### Tool Permissions

Auto-approved tools so Claude does not prompt for permission on common operations:

```yaml
permissions:
  - "Bash(python:*)"
  - "Bash(git:*)"
  - "Bash(sqlite3:*)"
  - "mcp__llm_memory__memory_store"
  - "mcp__llm_memory__memory_search"
  # ... etc
```

To add your own permissions, edit `settings.yaml` and re-run `install.sh`, or add them directly to `~/.claude/settings.json`.

### Hook Configuration

Each hook has a matcher (which events trigger it), a script path, and a timeout:

```yaml
hooks:
  SessionStart:
    matcher: "startup|resume|compact"
    script: session_start.sh
    timeout: 5
  PostToolUse:
    - matcher: ""
      script: session_monitor.sh
      timeout: 5
    - matcher: "mcp__llm_memory__memory_store"
      command: "date +%s > /tmp/llm_memory_last_save"
      async: true
  PreCompact:
    matcher: ""
    script: pre_compact.sh
    timeout: 5
  SessionEnd:
    matcher: ""
    script: session_end.sh
    timeout: 10
    async: true
```

Hook timeouts are in seconds. The SessionEnd hook runs asynchronously so it does not block Claude Code from exiting.

## Memory Types and When to Use Each

### narrative

One per project. A living document that captures the full story of a project. Updated over time as sessions accumulate. Generated from raw JSONL transcripts.

Required sections:
- What This Is
- Session History
- Decisions Made
- Gotchas & Lessons
- Current State
- Outstanding Items
- Direction
- Source Transcripts

### note

Atomic, self-contained facts. Use for:
- Corrections (tag: "correction", importance 8+)
- Design decisions (with rationale)
- User preferences
- Insights or lessons learned

Keep notes short. Use tags for discoverability.

### session_log

Created automatically by the SessionEnd hook. You rarely need to create these manually. Records that a session happened, with turn count and a brief summary.

## Importance Levels

Memories have an importance score from 1 to 10:

| Range | Usage |
|-------|-------|
| 1-3 | Session logs, routine notes |
| 4-6 | Standard decisions, general notes |
| 7-8 | Important corrections, key decisions |
| 9-10 | Critical facts that must never be forgotten |

The SessionStart hook loads notes with importance >= 6 into context automatically.

## Project Detection

The project name is derived from the working directory. If your path contains `/projects/myapp/`, the project name is `myapp`. This is used to scope narratives, notes, and session logs.

If Claude Code is started outside a recognized project directory, memories are stored without a project scope and cross-project notes are shown instead.
