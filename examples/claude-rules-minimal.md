# LLM Memory — Minimal CLAUDE.md

Add this to `~/.claude/CLAUDE.md` to enable memory tools.

## Memory Protocol (MCP tools via llm_memory server)
You have access to persistent memory tools provided by the "llm_memory" MCP server. The tool names are prefixed with mcp__llm_memory__ (e.g., mcp__llm_memory__memory_store).

Available tools: memory_store, memory_search, memory_recent, memory_get, memory_connect, memory_explore, memory_delete.

IMPORTANT: These MCP tools are available DIRECTLY — do NOT use ToolSearch to find them. Call them by their full name.

### Memory types:
- **narrative**: Per-project living document. One per project, updated over time.
- **note**: Atomic fact, decision, correction, or insight. Use tags for searchability.
- **session_log**: Record that a session happened. Created automatically by hooks.

### When to use memory tools:
- Before starting work, call memory_search to check for relevant past context.
- When corrected, store a note with importance 8+ and tag "correction".
- When a key decision is made, store a note with the decision and rationale.
- Include the project name in every memory_store call.
