#!/bin/bash
# SubagentStart hook: injects project context (narrative + important notes) into subagents.
# Fires when a subagent is spawned.

INPUT=$(cat)
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')

DB="$HOME/.claude/memory/memory.db"
if [ ! -f "$DB" ]; then
    exit 0
fi

# Derive project name from cwd
PROJECT=""
if [ -n "$CWD" ]; then
    PROJECT=$(echo "$CWD" | sed -n 's|.*/projects/\([^/]*\).*|\1|p')
fi

if [ -z "$PROJECT" ]; then
    exit 0
fi

# Prefer the rendered .narrative.md file; fall back to DB record.
NARRATIVE_FILE="$HOME/.claude/memory/projects/$PROJECT.narrative.md"
if [ -f "$NARRATIVE_FILE" ]; then
    NARRATIVE=$(cat "$NARRATIVE_FILE" 2>/dev/null)
else
    NARRATIVE=$(sqlite3 "$DB" "SELECT content FROM memories WHERE type='narrative' AND project='$PROJECT' AND (status IS NULL OR status != 'archived') ORDER BY created_at DESC LIMIT 1;" 2>/dev/null)
fi

# Query important notes (importance >= 7)
NOTES=$(sqlite3 "$DB" "SELECT content FROM memories WHERE type='note' AND project='$PROJECT' AND importance >= 7 AND (status IS NULL OR status != 'archived') ORDER BY importance DESC, created_at DESC LIMIT 10;" 2>/dev/null)

# Build additionalContext string
CONTEXT="## Project: ${PROJECT}"
if [ -n "$NARRATIVE" ]; then
    CONTEXT="${CONTEXT}
## Narrative:
${NARRATIVE}"
fi
if [ -n "$NOTES" ]; then
    CONTEXT="${CONTEXT}

## Important Notes:
${NOTES}"
fi

# Output JSON using jq for proper escaping
jq -n --arg context "$CONTEXT" '{
    "hookSpecificOutput": {
        "hookEventName": "SubagentStart",
        "additionalContext": $context
    }
}'

exit 0
