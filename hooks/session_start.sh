#!/bin/bash
# SessionStart hook: auto-loads recent memories into Claude's context.
# Fires on startup, resume, and after compaction.

INPUT=$(cat)
SOURCE=$(echo "$INPUT" | jq -r '.source // .trigger // empty')
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')

DB="$HOME/.claude/memory/memory.db"
if [ ! -f "$DB" ]; then
    exit 0
fi

# Derive project name from cwd (e.g. /home/scott/projects/finance_nexus → finance_nexus)
PROJECT=""
if [ -n "$CWD" ]; then
    # Extract the directory name after "projects/"
    PROJECT=$(echo "$CWD" | sed -n 's|.*/projects/\([^/]*\).*|\1|p')
fi

# Export session ID for other hooks
if [ -n "$CLAUDE_ENV_FILE" ]; then
    SESSION_ID=$(echo "$INPUT" | jq -r '.session_id')
    echo "export LLM_MEMORY_SESSION_ID='$SESSION_ID'" >> "$CLAUDE_ENV_FILE"
fi

# Auto-process transcripts for this project if no memories exist yet
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [ -n "$PROJECT" ] && [ "$SOURCE" != "compact" ]; then
    EXISTING=$(sqlite3 "$DB" "SELECT COUNT(*) FROM memories WHERE project='$PROJECT';" 2>/dev/null)
    if [ "$EXISTING" -eq "0" ] 2>/dev/null; then
        "$SCRIPT_DIR/.venv/bin/python3" "$SCRIPT_DIR/process_transcripts.py" --project "$PROJECT" --quiet 2>/dev/null
    fi
fi

if [ "$SOURCE" = "compact" ]; then
    # After compaction: reload the most recent session summary + chunk summaries
    if [ -n "$PROJECT" ]; then
        SUMMARY=$(sqlite3 "$DB" "SELECT content FROM memories WHERE type='session_summary' AND project='$PROJECT' ORDER BY created_at DESC LIMIT 1;" 2>/dev/null)
        CHUNKS=$(sqlite3 -separator '|' "$DB" "SELECT id, substr(content, 1, 300) FROM memories WHERE type='chunk_summary' AND project='$PROJECT' ORDER BY created_at DESC LIMIT 5;" 2>/dev/null)
    else
        SUMMARY=$(sqlite3 "$DB" "SELECT content FROM memories WHERE type='session_summary' ORDER BY created_at DESC LIMIT 1;" 2>/dev/null)
        CHUNKS=$(sqlite3 -separator '|' "$DB" "SELECT id, substr(content, 1, 300) FROM memories WHERE type='chunk_summary' ORDER BY created_at DESC LIMIT 5;" 2>/dev/null)
    fi
    if [ -n "$SUMMARY" ] || [ -n "$CHUNKS" ]; then
        echo "=== POST-COMPACTION CONTEXT (auto-injected from llm_memory) ==="
        if [ -n "$PROJECT" ]; then
            echo "## Active project: $PROJECT"
        fi
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
    # Fresh startup or resume: load memories, prioritizing current project
    if [ -n "$PROJECT" ]; then
        # Project-specific: load this project's summaries and chunks first
        SUMMARIES=$(sqlite3 -separator '|' "$DB" "SELECT id, project, substr(content, 1, 300) FROM memories WHERE type='session_summary' AND project='$PROJECT' ORDER BY created_at DESC LIMIT 3;" 2>/dev/null)
        CHUNKS=$(sqlite3 -separator '|' "$DB" "SELECT id, project, substr(content, 1, 300) FROM memories WHERE type='chunk_summary' AND project='$PROJECT' ORDER BY created_at DESC LIMIT 5;" 2>/dev/null)
        IMPORTANT=$(sqlite3 -separator '|' "$DB" "SELECT id, type, project, substr(content, 1, 300), importance FROM memories WHERE importance >= 7 AND type != 'session_summary' AND project='$PROJECT' ORDER BY created_at DESC LIMIT 10;" 2>/dev/null)
    else
        SUMMARIES=$(sqlite3 -separator '|' "$DB" "SELECT id, project, substr(content, 1, 300) FROM memories WHERE type='session_summary' ORDER BY created_at DESC LIMIT 3;" 2>/dev/null)
        CHUNKS=$(sqlite3 -separator '|' "$DB" "SELECT id, project, substr(content, 1, 300) FROM memories WHERE type='chunk_summary' ORDER BY created_at DESC LIMIT 5;" 2>/dev/null)
        IMPORTANT=$(sqlite3 -separator '|' "$DB" "SELECT id, type, project, substr(content, 1, 300), importance FROM memories WHERE importance >= 7 AND type != 'session_summary' ORDER BY created_at DESC LIMIT 10;" 2>/dev/null)
    fi

    if [ -n "$SUMMARIES" ] || [ -n "$IMPORTANT" ] || [ -n "$CHUNKS" ]; then
        echo "=== LOADED MEMORIES (auto-injected from llm_memory) ==="
        if [ -n "$PROJECT" ]; then
            echo "## Active project: $PROJECT"
        fi
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
