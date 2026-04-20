# LLM Memory — CLAUDE.md Rules

These rules go in your `~/.claude/CLAUDE.md` file so Claude Code knows when and how
to use the LLM Memory tools. The install script copies this automatically to
`~/.claude/memory/config/CLAUDE.md` (synced between machines) and applies it to
`~/.claude/CLAUDE.md` on each machine.

To update: edit `~/.claude/memory/config/CLAUDE.md` — it syncs via Syncthing and
gets applied on session start.

---

## Large Output Rules
- Never generate more than 500 lines in a single Write tool call.
- For large files (kanban boards, plans, specs): write header/TOC first, then append each section with Edit.
- If a file will exceed 500 lines, split into logical chunks and write each separately.
- Always use Write/Edit tools for large content. Never output large content in conversation.

## Memory Protocol (MCP tools via llm_memory server)
You have access to four MCP tools from the "llm_memory" server. Names are prefixed
`mcp__llm_memory__` (e.g. `mcp__llm_memory__memory_search`). Call them directly —
do NOT use ToolSearch to find them.

| Tool | Purpose |
|------|---------|
| `memory_search` | Cross-project FTS5 search over per-project ledger items (decisions, learnings, goals, suggestions, done). Use when you don't know which project a fact is in. |
| `project_lookup` | Single-project fuzzy search over that project's ledger. Use when you know the project and want to drill into its history. |
| `narrative_coverage` | Returns merged vs. unprocessed session transcripts for a project. Run before `/narrative` to see what's outstanding. |
| `resume` | Returns the last real session's journal plus a tail of its conversation.md. Use when picking up prior work. |

There are NO `memory_store`, `memory_recent`, `memory_get`, `memory_connect`, `memory_explore`, or `memory_delete` tools. Those were retired when the taxonomy moved to per-project JSON ledgers — do not call them.

## Where memory lives on disk

- `~/.claude/memory/projects/{project}.json` — canonical per-project state. Decisions, learnings, goals, suggestions, done items, session journals. Source of truth.
- `~/.claude/memory/projects/{project}.narrative.md` — rendered human-readable narrative, auto-injected into context at SessionStart by the llm_memory hook.
- `~/.claude/memory/conversations/{session_id}.md` — **stripped conversations** (user + assistant text only, tool noise removed, project stamped in frontmatter). This is the right file for historical lookup: "what did we discuss about X". Grep here before reaching for transcripts.
- `~/.claude/memory/transcripts/{session_id}.jsonl` — **raw JSONL archive** including every tool call and result. Large and noisy. Only use when you specifically need the raw tool I/O (debugging a failed tool call, reconstructing exact file edits). Prefer conversations/*.md for everything else.
- `~/.claude/memory/items/{project}/{kind}/{id}.json` — per-item ledger files. Inputs to the items_fts index that `memory_search` queries. Don't edit directly; they're rebuilt from `{project}.json` by merger.py.

## When to use memory tools
- Before starting work on a topic: `project_lookup` (if you know the project) or `memory_search` (if you don't).
- Picking up prior work: `resume` with the project name.
- About to run `/narrative`: `narrative_coverage` to see what sessions are unprocessed.

## Project Narratives
- Rendering is automatic. The `/narrative` skill runs the delta-extractor → merger → renderer pipeline and writes `{project}.narrative.md`. Do not hand-write narratives.
- If the SessionStart hook says **"AUTOMATIC TASK: No narrative exists"** or **"N new session(s) since last narrative update"**, invoke the `/narrative` skill. Don't ask permission — just run it.
- For historical lookups ("what did we decide about X", "when did we discuss Y"), grep `~/.claude/memory/conversations/*.md` — they're smaller, stripped, and tagged by project.

## Context Management
- Prefer reading files on-demand over loading everything upfront.
- Use summary/index files to navigate large projects rather than reading full files.
- Never read files larger than 256KB in one call. Use offset/limit for large files.

## File Generation
- For multi-section documents: write one section at a time, verify, then continue.
- For kanban/task boards: generate one phase per Write/Edit operation.
- After multi-step generation, verify file integrity (line count, section headers).
