# LLM Memory — CLAUDE.md Rules

These rules go in your `~/.claude/CLAUDE.md` file so Claude Code knows when and how
to use the LLM Memory tools. Copy this file directly:

```bash
cp claude-rules-example.md ~/.claude/CLAUDE.md
```

Or if you already have a `~/.claude/CLAUDE.md`, merge the sections below into it.

---

## Large Output Rules
- Never generate more than 500 lines in a single Write tool call.
- For large files (kanban boards, plans, specs): write header/TOC first, then append each section with Edit.
- If a file will exceed 500 lines, split into logical chunks and write each separately.
- Always use Write/Edit tools for large content. Never output large content in conversation.

## Memory Protocol (MCP tools via llm_memory server)
You have access to persistent memory tools provided by the "llm_memory" MCP server. These are your primary tools for cross-session memory. The tool names are prefixed with mcp__llm_memory__ (e.g., mcp__llm_memory__memory_store).

Available tools: memory_store, memory_search, memory_recent, memory_get, memory_connect, memory_explore, memory_delete.

IMPORTANT: These MCP tools are available DIRECTLY — do NOT use ToolSearch to find them. Call them by their full name, e.g., mcp__llm_memory__memory_store, mcp__llm_memory__memory_search, etc.

- At session start, call memory_recent to load context from previous sessions.
- At task boundaries (completing a subtask, key decision, resolving an issue), call memory_store.
- Before starting work on a topic, call memory_search to check for relevant past context.
- When corrected on something, call memory_store with type "correction" and importance 8+.
- When finishing a session or asked to save progress, call memory_store with type "session_summary".
- Include the project name in every memory_store call.
- Use memory_connect to link related memories (supports, contradicts, supersedes, implements, depends_on, related_to).

## Chunk Summaries
At natural breakpoints during a session (topic change, subtask completion, before compaction, or when context is getting long), store a chunk_summary memory capturing the work just completed.

### When to create chunk summaries:
- After completing a distinct subtask or feature
- When switching topics or projects within a session
- Before compaction (in addition to session_summary)
- After resolving a complex debugging session
- Every ~20-30 minutes of active work

### What to include:
- Decisions made and their rationale
- Key learnings and insights discovered
- Outcomes (what worked, what was built/changed)
- File paths modified and why
- Current state / what comes next

### What to filter out:
- Circular debugging loops (just record the resolution)
- Wrong assumptions that were immediately corrected
- Dead-end research paths (unless the dead-end itself is informative)
- Verbatim code blocks (summarize the change instead)
- Routine operations (file reads, directory listings)

### How to create them:
- Use type "chunk_summary" with importance 5-7 (raise to 8+ for critical decisions)
- Number chunks per session in the content: "Chunk 1: ..."
- Include the project name
- Set transcript_ref to the archived transcript path if known (e.g. "~/.claude/memory/transcripts/SESSION_ID.jsonl")
- Link sequential chunks with memory_connect using "related_to"
- Link the final chunk to the session_summary with "supports"

## Context Management
- After every ~10 tool calls, assess whether important unsaved decisions exist. If so, memory_store them.
- Prefer reading files on-demand over loading everything upfront.
- Use summary/index files to navigate large projects rather than reading full files.
- Never read files larger than 256KB in one call. Use offset/limit for large files.

## File Generation
- For multi-section documents: write one section at a time, verify, then continue.
- For kanban/task boards: generate one phase per Write/Edit operation.
- After multi-step generation, verify file integrity (line count, section headers).
