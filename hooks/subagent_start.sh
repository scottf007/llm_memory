#!/bin/bash
# SubagentStart hook: injects project context (narrative + important notes) into subagents.
# Fires when a subagent is spawned.

MEMORY_DIR="${LLM_MEMORY_HOME:-$HOME/.claude/memory}"
export LLM_MEMORY_HOME="$MEMORY_DIR"

INPUT=$(cat)
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')

DB="$MEMORY_DIR/memory.db"
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

# Narrative source of truth is the rendered .narrative.md file.
NARRATIVE_FILE="$MEMORY_DIR/projects/$PROJECT.narrative.md"
if [ -f "$NARRATIVE_FILE" ]; then
    NARRATIVE=$(cat "$NARRATIVE_FILE" 2>/dev/null)
else
    NARRATIVE=""
fi

# Build additionalContext string
CONTEXT="## Project: ${PROJECT}"
if [ -n "$NARRATIVE" ]; then
    CONTEXT="${CONTEXT}
## Narrative:
${NARRATIVE}"
fi

# Output JSON using jq for proper escaping
jq -n --arg context "$CONTEXT" '{
    "hookSpecificOutput": {
        "hookEventName": "SubagentStart",
        "additionalContext": $context
    }
}'

exit 0
