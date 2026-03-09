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
You have access to persistent memory tools provided by the "llm_memory" MCP server. The tool names are prefixed with mcp__llm_memory__ (e.g., mcp__llm_memory__memory_store).

Available tools: memory_store, memory_search, memory_recent, memory_get, memory_connect, memory_explore, memory_delete.

IMPORTANT: These MCP tools are available DIRECTLY — do NOT use ToolSearch to find them. Call them by their full name.

### Memory types (3):
- **narrative**: Per-project living document — the full story of a project. One per project, updated over time. New versions supersede old ones.
- **note**: Atomic fact, decision, correction, preference, or insight. Keep it short and self-contained. Use tags for searchability.
- **session_log**: Lightweight record that a session happened. Created automatically by hooks.

### When to use memory tools:
- Before starting work on a topic, call memory_search to check for relevant past context.
- When corrected on something, store a note with importance 8+ and tag "correction".
- When a key decision is made, store a note with the decision and rationale.
- When asked to save progress or update the narrative, read the raw JSONL transcript(s) and write a comprehensive narrative.
- Include the project name in every memory_store call.

### Project Narratives:
- Each project has one living narrative — a reference document, not a history book.
- When the session_start hook says "AUTOMATIC TASK: No narrative exists", you MUST generate the narrative immediately without asking the user. Use a background Agent to read the raw JSONL transcripts from ~/.claude/memory/transcripts/ and store the narrative. Do not ask for permission — just do it.
- When the hook says "N new session(s) since last narrative update", read the new transcript(s) and update the narrative.
- When storing an updated narrative, connect it to the previous one with relationship "supersedes".
- Write narratives from the raw JSONL transcripts, not from summaries — transcripts have the user's exact words, the debugging loops, the moments where direction changed.
- Narrative format (all sections required):
  - **What This Is**: 2-3 sentences describing the project
  - **Session History**: One line per session — date, what happened, outcome
  - **Decisions Made**: Table format — decision | rationale (with user's own words where possible)
  - **Gotchas & Lessons**: Bullet points of things that bit us, so future sessions don't repeat mistakes
  - **Current State**: What exists, what works, what's deployed
  - **Outstanding Items**: Action items not started, half-finished work, unsolved problems, deferred ideas — nothing gets lost here
  - **Direction**: Where we're headed, numbered next steps
  - **Source Transcripts**: List of JSONL files used

## Context Management
- Prefer reading files on-demand over loading everything upfront.
- Use summary/index files to navigate large projects rather than reading full files.
- Never read files larger than 256KB in one call. Use offset/limit for large files.

## File Generation
- For multi-section documents: write one section at a time, verify, then continue.
- For kanban/task boards: generate one phase per Write/Edit operation.
- After multi-step generation, verify file integrity (line count, section headers).
