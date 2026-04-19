#!/bin/bash
# SessionEnd hook: archives transcript and extracts the stripped conversation.md
# sibling. The .md frontmatter is now the session registry — no session_log DB
# record is written.

INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id')
TRANSCRIPT=$(echo "$INPUT" | jq -r '.transcript_path')

if [ -z "$TRANSCRIPT" ] || [ ! -f "$TRANSCRIPT" ]; then
    exit 0
fi

TRANSCRIPT_DIR="$HOME/.claude/memory/transcripts"
mkdir -p "$TRANSCRIPT_DIR"
ARCHIVE_NAME="${SESSION_ID:-$(date +%Y%m%d_%H%M%S)}.jsonl"
cp "$TRANSCRIPT" "$TRANSCRIPT_DIR/$ARCHIVE_NAME" 2>/dev/null || true

CONVERSATIONS_DIR="$HOME/.claude/memory/conversations"
mkdir -p "$CONVERSATIONS_DIR"
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON3="$SCRIPT_DIR/.venv/bin/python3"
[ -x "$PYTHON3" ] || PYTHON3="python3"
"$PYTHON3" "$SCRIPT_DIR/extract_conversation.py" \
    "$TRANSCRIPT_DIR/$ARCHIVE_NAME" \
    --output "$CONVERSATIONS_DIR/${ARCHIVE_NAME%.jsonl}.md" 2>/dev/null || true

exit 0
