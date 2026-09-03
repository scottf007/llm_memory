#!/bin/bash
# Codex SessionEnd hook: sweep exactly the session that just ended.

INPUT=$(cat)
SESSION_ID=$(printf '%s' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON3="$SCRIPT_DIR/.venv/bin/python3"
[ -x "$PYTHON3" ] || PYTHON3="python3"

if [ -z "$SESSION_ID" ] || ! "$PYTHON3" "$SCRIPT_DIR/process_transcripts.py" --client codex --session "$SESSION_ID" --quiet >/dev/null 2>&1; then
    echo "LLM_MEMORY_WARN: codex session sweep failed."
fi
exit 0
