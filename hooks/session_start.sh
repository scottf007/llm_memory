#!/bin/bash
# SessionStart hook: auto-loads memories into Claude's context.
# Fires on startup, resume, and after compaction.

INPUT=$(cat)
SOURCE=$(echo "$INPUT" | jq -r '.source // .trigger // empty')
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')

# Sync CLAUDE.md from shared config if newer
SHARED_CLAUDE_MD="$HOME/.claude/memory/config/CLAUDE.md"
LOCAL_CLAUDE_MD="$HOME/.claude/CLAUDE.md"
if [ -f "$SHARED_CLAUDE_MD" ]; then
    if [ ! -f "$LOCAL_CLAUDE_MD" ] || [ "$SHARED_CLAUDE_MD" -nt "$LOCAL_CLAUDE_MD" ]; then
        cp "$SHARED_CLAUDE_MD" "$LOCAL_CLAUDE_MD"
    fi
fi

# Auto-update: check GitHub for newer version and update in background
# Skip if the user has opted out via sentinel file
if [ "$SOURCE" != "compact" ] && [ ! -f "$HOME/.claude/memory/config/no-auto-update" ]; then
    LIB_DIR="$HOME/.claude/memory/lib"
    REPO="${LLM_MEMORY_REPO:-scottf007/llm_memory}"
    BRANCH="${LLM_MEMORY_BRANCH:-main}"
    if [ -f "$LIB_DIR/VERSION" ]; then
        LOCAL_SHA=$(cat "$LIB_DIR/VERSION")
        # Check GitHub API with a short timeout
        REMOTE_SHA=$(timeout 3 curl -sf "https://api.github.com/repos/$REPO/commits/$BRANCH" 2>/dev/null | jq -r '.sha' 2>/dev/null)
        if [ -n "$REMOTE_SHA" ] && [ "$REMOTE_SHA" != "null" ] && [ "$LOCAL_SHA" != "$REMOTE_SHA" ]; then
            # Update in background so we don't block session start
            (bash "$LIB_DIR/install.sh" --update >/dev/null 2>&1) &
            echo "LLM_MEMORY_UPDATING: Updating llm_memory in background (${LOCAL_SHA:0:8} → ${REMOTE_SHA:0:8}). Restart Claude Code for MCP server changes to take effect."
        fi
    fi
fi

# Sweep: collect transcripts not yet captured by hooks
if [ "$SOURCE" != "compact" ]; then
    TRANSCRIPT_DIR="$HOME/.claude/memory/transcripts"
    mkdir -p "$TRANSCRIPT_DIR"
    for src in "$HOME/.claude/projects"/*/*.jsonl; do
        [ -f "$src" ] || continue
        base=$(basename "$src")
        [ -f "$TRANSCRIPT_DIR/$base" ] || cp "$src" "$TRANSCRIPT_DIR/$base" 2>/dev/null
    done
fi

DB="$HOME/.claude/memory/memory.db"
if [ ! -f "$DB" ]; then
    exit 0
fi

# Derive project name from cwd
PROJECT=""
if [ -n "$CWD" ]; then
    PROJECT=$(echo "$CWD" | sed -n 's|.*/projects/\([^/]*\).*|\1|p')
fi

# Export session ID for other hooks
if [ -n "$CLAUDE_ENV_FILE" ]; then
    SESSION_ID=$(echo "$INPUT" | jq -r '.session_id')
    echo "export LLM_MEMORY_SESSION_ID='$SESSION_ID'" >> "$CLAUDE_ENV_FILE"
fi

# Auto-process all unprocessed transcripts (including synced ones)
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [ "$SOURCE" != "compact" ]; then
    "$SCRIPT_DIR/.venv/bin/python3" "$SCRIPT_DIR/process_transcripts.py" --quiet 2>/dev/null
fi

if [ "$SOURCE" = "compact" ]; then
    # After compaction: reload narrative + recent notes
    if [ -n "$PROJECT" ]; then
        NARRATIVE=$(sqlite3 "$DB" "SELECT content FROM memories WHERE type='narrative' AND project='$PROJECT' ORDER BY created_at DESC LIMIT 1;" 2>/dev/null)
        NOTES=$(sqlite3 -separator '|' "$DB" "SELECT uuid, substr(content, 1, 300), importance FROM memories WHERE type='note' AND project='$PROJECT' AND importance >= 6 ORDER BY importance DESC, created_at DESC LIMIT 10;" 2>/dev/null)
        # Check if narrative is stale
        NARRATIVE_DATE=$(sqlite3 "$DB" "SELECT created_at FROM memories WHERE type='narrative' AND project='$PROJECT' ORDER BY created_at DESC LIMIT 1;" 2>/dev/null)
        if [ -n "$NARRATIVE_DATE" ]; then
            COMPACT_NEW_SESSIONS=$(sqlite3 "$DB" "SELECT COUNT(*) FROM memories WHERE type='session_log' AND project='$PROJECT' AND created_at > '$NARRATIVE_DATE';" 2>/dev/null)
        else
            COMPACT_NEW_SESSIONS="0"
        fi
    else
        NARRATIVE=""
        NOTES=$(sqlite3 -separator '|' "$DB" "SELECT uuid, substr(content, 1, 300), importance FROM memories WHERE type='note' AND importance >= 6 ORDER BY importance DESC, created_at DESC LIMIT 10;" 2>/dev/null)
        COMPACT_NEW_SESSIONS="0"
    fi
    if [ -n "$NARRATIVE" ] || [ -n "$NOTES" ]; then
        echo "=== POST-COMPACTION CONTEXT (auto-injected from llm_memory) ==="
        if [ -n "$PROJECT" ]; then
            echo "## Active project: $PROJECT"
        fi
        if [ -n "$NARRATIVE" ]; then
            echo "$NARRATIVE"
        fi
        if [ -n "$NOTES" ]; then
            echo "## Important Notes:"
            echo "$NOTES"
        fi
        if [ "$COMPACT_NEW_SESSIONS" -gt "0" ] 2>/dev/null; then
            echo ""
            echo "AUTOMATIC TASK: $COMPACT_NEW_SESSIONS new session(s) since last narrative update for project '$PROJECT'."
            echo "You MUST update the project narrative NOW. Do not ask the user for permission."
            echo "Use an Agent to do this in the background so the user is not blocked."
        fi
        echo "=== END POST-COMPACTION CONTEXT ==="
    fi
else
    # Fresh startup or resume
    if [ -n "$PROJECT" ]; then
        NARRATIVE=$(sqlite3 "$DB" "SELECT content FROM memories WHERE type='narrative' AND project='$PROJECT' ORDER BY created_at DESC LIMIT 1;" 2>/dev/null)
        NOTES=$(sqlite3 -separator '|' "$DB" "SELECT uuid, substr(content, 1, 300), importance FROM memories WHERE type='note' AND project='$PROJECT' AND importance >= 6 ORDER BY importance DESC, created_at DESC LIMIT 10;" 2>/dev/null)
        LOGS=$(sqlite3 -separator '|' "$DB" "SELECT uuid, project, substr(content, 1, 200) FROM memories WHERE type='session_log' AND project='$PROJECT' ORDER BY created_at DESC LIMIT 3;" 2>/dev/null)

        # Check if narrative needs updating (new session_logs since last narrative)
        NARRATIVE_DATE=$(sqlite3 "$DB" "SELECT created_at FROM memories WHERE type='narrative' AND project='$PROJECT' ORDER BY created_at DESC LIMIT 1;" 2>/dev/null)
        if [ -n "$NARRATIVE_DATE" ]; then
            NEW_SESSIONS=$(sqlite3 "$DB" "SELECT COUNT(*) FROM memories WHERE type='session_log' AND project='$PROJECT' AND created_at > '$NARRATIVE_DATE';" 2>/dev/null)
        else
            NEW_SESSIONS=$(sqlite3 "$DB" "SELECT COUNT(*) FROM memories WHERE type='session_log' AND project='$PROJECT';" 2>/dev/null)
        fi
    else
        NARRATIVE=""
        NOTES=$(sqlite3 -separator '|' "$DB" "SELECT uuid, substr(content, 1, 300), importance FROM memories WHERE type='note' AND importance >= 6 ORDER BY importance DESC, created_at DESC LIMIT 10;" 2>/dev/null)
        LOGS=$(sqlite3 -separator '|' "$DB" "SELECT uuid, project, substr(content, 1, 200) FROM memories WHERE type='session_log' ORDER BY created_at DESC LIMIT 3;" 2>/dev/null)
        NEW_SESSIONS="0"
    fi

    echo "=== LOADED MEMORIES (auto-injected from llm_memory) ==="
    if [ -n "$PROJECT" ]; then
        echo "## Active project: $PROJECT"
    fi

    if [ -n "$NARRATIVE" ]; then
        echo "## Project Narrative:"
        echo "$NARRATIVE"
    fi
    if [ -n "$LOGS" ]; then
        echo "## Recent Sessions:"
        echo "$LOGS"
    fi
    if [ -n "$NOTES" ]; then
        echo "## Important Notes:"
        echo "$NOTES"
    fi

    # Determine what action is needed
    if [ -z "$NARRATIVE" ] && [ -n "$PROJECT" ]; then
        # No narrative exists — count available transcripts
        TRANSCRIPT_COUNT=$(ls "$HOME/.claude/memory/transcripts/"*.jsonl 2>/dev/null | wc -l)
        SESSION_COUNT=$(sqlite3 "$DB" "SELECT COUNT(*) FROM memories WHERE type='session_log' AND project='$PROJECT';" 2>/dev/null)
        if [ "$SESSION_COUNT" -gt "0" ] 2>/dev/null; then
            echo ""
            echo "AUTOMATIC TASK: No narrative exists for project '$PROJECT' but $SESSION_COUNT session(s) found."
            echo "You MUST generate the project narrative NOW before doing anything else. Do not ask the user for permission."
            echo "Use an Agent to do this in the background so the user is not blocked."
            echo "Read the raw JSONL transcripts from ~/.claude/memory/transcripts/ for this project"
            echo "and store the narrative with memory_store type='narrative', project='$PROJECT'. Use this format:"
            echo ""
            echo "  ## The Idea             — 2-3 sentences"
            echo "  ## Approach             — table: non-obvious decision | rationale"
            echo "  ## What's Done          — current state + key file paths"
            echo "  ## What We've Learnt    — gotchas that could bite again"
            echo "  ## What We Want To Do   — user-stated goals only"
            echo "  ## Suggested Work       — Claude recommendations"
            echo "  ## Resuming             — last piece of work, status: complete/interrupted"
            echo "  ## Source Transcripts   — last 5-10 as lookup table + pointer to DB"
        fi
    elif [ "$NEW_SESSIONS" -gt "0" ] 2>/dev/null; then
        echo ""
        echo "AUTOMATIC TASK: $NEW_SESSIONS new session(s) since last narrative update for project '$PROJECT'."
        echo "You MUST update the project narrative NOW. Do not ask the user for permission."
        echo "Use an Agent to do this in the background so the user is not blocked."
    fi

    # Cross-project: find projects with no narrative OR stale narrative
    NEEDS_NARRATIVE=$(sqlite3 "$DB" "
        SELECT DISTINCT project FROM memories
        WHERE type='session_log' AND project != ''
        AND (
            project NOT IN (
                SELECT DISTINCT project FROM memories WHERE type='narrative'
            )
            OR project IN (
                SELECT m.project FROM memories m
                WHERE m.type='narrative'
                GROUP BY m.project
                HAVING MAX(m.created_at) < (
                    SELECT MAX(s.created_at) FROM memories s
                    WHERE s.type='session_log' AND s.project = m.project
                )
            )
        )
        ORDER BY project;" 2>/dev/null)
    if [ -n "$NEEDS_NARRATIVE" ]; then
        OTHER_PROJECTS=""
        while IFS= read -r p; do
            [ "$p" = "$PROJECT" ] && continue
            [ -z "$p" ] && continue
            OTHER_PROJECTS="${OTHER_PROJECTS:+$OTHER_PROJECTS, }$p"
        done <<< "$NEEDS_NARRATIVE"
        if [ -n "$OTHER_PROJECTS" ]; then
            echo ""
            echo "OTHER PROJECTS NEEDING NARRATIVES: $OTHER_PROJECTS"
            echo "When convenient, switch to those project directories and generate their narratives."
        fi
    fi

    echo "Use memory_search/memory_get for more context. Use memory_store to save new memories."
    echo "=== END LOADED MEMORIES ==="
fi

exit 0
