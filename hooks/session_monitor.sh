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

if [ "$SIZE_KB" -gt 500 ]; then
    echo "Session transcript is ${SIZE_KB}KB. Context is getting large. Run /narrative to checkpoint durable project context now."
elif [ "$SIZE_KB" -gt 300 ]; then
    echo "Session transcript is ${SIZE_KB}KB. Context checkpoint recommended. Consider running /narrative before continuing."
fi

exit 0
