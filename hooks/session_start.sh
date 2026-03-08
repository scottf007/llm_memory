#!/bin/bash
# SessionStart hook: auto-loads recent memories into Claude's context.
# Fires on startup, resume, and after compaction.

INPUT=$(cat)
SOURCE=$(echo "$INPUT" | jq -r '.source // .trigger // empty')

DB="$HOME/.claude/memory/memory.db"
if [ ! -f "$DB" ]; then
    exit 0
fi

# Export session ID for other hooks
if [ -n "$CLAUDE_ENV_FILE" ]; then
    SESSION_ID=$(echo "$INPUT" | jq -r '.session_id')
    echo "export LLM_MEMORY_SESSION_ID='$SESSION_ID'" >> "$CLAUDE_ENV_FILE"
fi

if [ "$SOURCE" = "compact" ]; then
    # After compaction: reload the most recent session summary + chunk summaries
    SUMMARY=$(sqlite3 "$DB" "SELECT content FROM memories WHERE type='session_summary' ORDER BY created_at DESC LIMIT 1;" 2>/dev/null)
    CHUNKS=$(sqlite3 -separator '|' "$DB" "SELECT id, substr(content, 1, 300) FROM memories WHERE type='chunk_summary' ORDER BY created_at DESC LIMIT 5;" 2>/dev/null)
    if [ -n "$SUMMARY" ] || [ -n "$CHUNKS" ]; then
        echo "=== POST-COMPACTION CONTEXT (auto-injected from llm_memory) ==="
        if [ -n "$SUMMARY" ]; then
            echo "$SUMMARY"
        fi
        if [ -n "$CHUNKS" ]; then
            echo "## Recent Chunk Summaries:"
            echo "$CHUNKS"
        fi
        echo "=== END POST-COMPACTION CONTEXT ==="
    fi
else
    # Fresh startup or resume: load high-importance memories + recent summaries
    SUMMARIES=$(sqlite3 -separator '|' "$DB" "SELECT id, project, substr(content, 1, 300) FROM memories WHERE type='session_summary' ORDER BY created_at DESC LIMIT 3;" 2>/dev/null)
    IMPORTANT=$(sqlite3 -separator '|' "$DB" "SELECT id, type, project, substr(content, 1, 300), importance FROM memories WHERE importance >= 7 AND type != 'session_summary' ORDER BY created_at DESC LIMIT 10;" 2>/dev/null)
    CHUNKS=$(sqlite3 -separator '|' "$DB" "SELECT id, project, substr(content, 1, 300) FROM memories WHERE type='chunk_summary' ORDER BY created_at DESC LIMIT 5;" 2>/dev/null)

    if [ -n "$SUMMARIES" ] || [ -n "$IMPORTANT" ] || [ -n "$CHUNKS" ]; then
        echo "=== LOADED MEMORIES (auto-injected from llm_memory) ==="
        if [ -n "$SUMMARIES" ]; then
            echo "## Recent Session Summaries:"
            echo "$SUMMARIES"
        fi
        if [ -n "$CHUNKS" ]; then
            echo "## Recent Chunk Summaries:"
            echo "$CHUNKS"
        fi
        if [ -n "$IMPORTANT" ]; then
            echo "## High-Importance Memories:"
            echo "$IMPORTANT"
        fi
        echo "Use memory_search/memory_recent for more context. Use memory_store to save new memories."
        echo "=== END LOADED MEMORIES ==="
    fi
fi

exit 0
