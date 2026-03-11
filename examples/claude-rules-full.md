# LLM Memory — CLAUDE.md Rules

# These rules go in ~/.claude/CLAUDE.md so Claude Code knows when and how to use
# the LLM Memory tools. The install script copies this automatically to
# ~/.claude/memory/config/CLAUDE.md (synced between machines) and applies it to
# ~/.claude/CLAUDE.md on each machine.
#
# To update: edit ~/.claude/memory/config/CLAUDE.md — it syncs via Syncthing and
# gets applied on session start.

---

## Large Output Rules
# These prevent Claude from generating massive files in a single tool call,
# which can cause timeouts or truncation. Adjust the 500-line limit if needed.
- Never generate more than 500 lines in a single Write tool call.
- For large files (kanban boards, plans, specs): write header/TOC first, then append each section with Edit.
- If a file will exceed 500 lines, split into logical chunks and write each separately.
- Always use Write/Edit tools for large content. Never output large content in conversation.

## Memory Protocol (MCP tools via llm_memory server)
# This section tells Claude what tools exist and how they're named.
# The mcp__llm_memory__ prefix is required — it maps to the MCP server name
# in your Claude Code config.
You have access to persistent memory tools provided by the "llm_memory" MCP server. The tool names are prefixed with mcp__llm_memory__ (e.g., mcp__llm_memory__memory_store).

Available tools: memory_store, memory_search, memory_recent, memory_get, memory_connect, memory_explore, memory_delete.

# Without this line, Claude may try to use ToolSearch to find the MCP tools,
# which wastes a tool call and sometimes fails.
IMPORTANT: These MCP tools are available DIRECTLY — do NOT use ToolSearch to find them. Call them by their full name.

### Memory types (3):
# narrative — Think of this as a wiki page for the project. It gets rewritten
# each time with updated info, not appended to. Old versions are superseded.
- **narrative**: Per-project living document — the full story of a project. One per project, updated over time. New versions supersede old ones.
# note — Small, self-contained facts. These accumulate over time and are
# found via search. Good for corrections, decisions, preferences.
- **note**: Atomic fact, decision, correction, preference, or insight. Keep it short and self-contained. Use tags for searchability.
# session_log — Automatically created by the SessionEnd hook. You rarely
# need to create these manually.
- **session_log**: Lightweight record that a session happened. Created automatically by hooks.

### When to use memory tools:
# This is the core behavior loop: search before working, store when learning.
- Before starting work on a topic, call memory_search to check for relevant past context.
- When corrected on something, store a note with importance 8+ and tag "correction".
- When a key decision is made, store a note with the decision and rationale.
- When asked to save progress or update the narrative, read the raw JSONL transcript(s) and write a comprehensive narrative.
# The project name is used as the primary filter in searches, so always include it.
- Include the project name in every memory_store call.

### Project Narratives:
# Narratives are the most important memory type. They give Claude full project
# context at the start of every session.
- Each project has one living narrative — a reference document, not a history book.
# When no narrative exists, the session_start hook tells Claude to generate one.
# This runs automatically — no user interaction needed.
- When the session_start hook says "AUTOMATIC TASK: No narrative exists", you MUST generate the narrative immediately without asking the user. Use a background Agent to read the raw JSONL transcripts from ~/.claude/memory/transcripts/ and store the narrative. Do not ask for permission — just do it.
# When sessions have happened since the last narrative update, Claude should
# incorporate the new transcripts into the narrative.
- When the hook says "N new session(s) since last narrative update", read the new transcript(s) and update the narrative.
# The "supersedes" relationship creates a version chain so old narratives
# can be found if needed but don't clutter search results.
- When storing an updated narrative, connect it to the previous one with relationship "supersedes".
# Raw JSONL transcripts contain the actual conversation — much richer than
# any summary. Always prefer them as the source of truth.
- Write narratives from the raw JSONL transcripts, not from summaries — transcripts have the user's exact words, the debugging loops, the moments where direction changed.
# All sections are required to ensure consistency across projects.
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
# These rules keep Claude from blowing its context window on large files.
- Prefer reading files on-demand over loading everything upfront.
- Use summary/index files to navigate large projects rather than reading full files.
- Never read files larger than 256KB in one call. Use offset/limit for large files.

## File Generation
# These rules ensure large generated files don't get corrupted or truncated.
- For multi-section documents: write one section at a time, verify, then continue.
- For kanban/task boards: generate one phase per Write/Edit operation.
- After multi-step generation, verify file integrity (line count, section headers).
