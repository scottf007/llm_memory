#!/bin/bash
# PostToolUse hook: monitors session JSONL size and warns Claude
# when context is getting large. Runs after every tool call.
# Exit 0 always — never block tool use.

INPUT=$(cat)
JSONL=$(echo "$INPUT" | jq -r '.transcript_path // empty')

# Fallback: find most recent JSONL if transcript_path not provided
if [ -z "$JSONL" ] || [ ! -f "$JSONL" ]; then
    JSONL=$(find "$HOME/.claude/projects" -name "*.jsonl" -type f -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)
fi

if [ -z "$JSONL" ] || [ ! -f "$JSONL" ]; then
    exit 0
fi

# Get file size in KB
SIZE_BYTES=$(stat --format=%s "$JSONL" 2>/dev/null || stat -f%z "$JSONL" 2>/dev/null)
SIZE_KB=$((SIZE_BYTES / 1024))

# Also check time since last memory_store
if [ -f /tmp/llm_memory_last_save ]; then
    LAST_SAVE=$(cat /tmp/llm_memory_last_save)
    NOW=$(date +%s)
    ELAPSED=$(( NOW - LAST_SAVE ))
    if [ "$ELAPSED" -gt 600 ] && [ "$SIZE_KB" -gt 200 ]; then
        echo "It has been $(( ELAPSED / 60 )) minutes since your last memory_store and the session is ${SIZE_KB}KB. Consider saving progress."
        exit 0
    fi
fi

if [ "$SIZE_KB" -gt 500 ]; then
    echo "Session transcript is ${SIZE_KB}KB. Context is getting large. Save current progress and key decisions with memory_store now."
elif [ "$SIZE_KB" -gt 300 ]; then
    echo "Session transcript is ${SIZE_KB}KB. Context checkpoint recommended. Consider saving important context with memory_store before continuing."
fi

exit 0
