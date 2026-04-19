#!/bin/bash
# SessionEnd hook: archives transcript and extracts the stripped conversation.md
# sibling. The .md frontmatter is now the session registry — no session_log DB
# record is written.
#
# NOTE: This hook MUST be registered with async=false (see install_hooks.sh).
# If async=true, Claude exits as soon as the hook launches and the python
# subprocess that writes the .md gets reaped before it completes — silently
# dropping the session from the /narrative pipeline. This is especially
# visible on the final session of a `claude --resume` chain where there's no
# follow-on session_start.sh sweep to recover the missed .md.

INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
TRANSCRIPT=$(echo "$INPUT" | jq -r '.transcript_path // empty')
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')

TRANSCRIPT_DIR="$HOME/.claude/memory/transcripts"
CONVERSATIONS_DIR="$HOME/.claude/memory/conversations"
LOG_DIR="$HOME/.claude/memory/logs"
mkdir -p "$TRANSCRIPT_DIR" "$CONVERSATIONS_DIR" "$LOG_DIR"
LOG="$LOG_DIR/session_end.log"

log() { echo "[$(date -Iseconds)] $*" >> "$LOG"; }

# If transcript_path is missing or unreadable, fall back to locating the jsonl
# under ~/.claude/projects/. Claude Code has been observed to pass an empty or
# stale transcript_path in some edge cases (notably `--resume` chains); if we
# can identify the session_id, we can still find the raw jsonl ourselves.
if [ -z "$TRANSCRIPT" ] || [ ! -f "$TRANSCRIPT" ]; then
    if [ -n "$SESSION_ID" ]; then
        CANDIDATE=$(find "$HOME/.claude/projects" -maxdepth 2 -name "${SESSION_ID}.jsonl" -type f 2>/dev/null | head -1)
        if [ -n "$CANDIDATE" ] && [ -f "$CANDIDATE" ]; then
            TRANSCRIPT="$CANDIDATE"
            log "fallback transcript lookup for $SESSION_ID -> $TRANSCRIPT"
        fi
    fi
fi

if [ -z "$TRANSCRIPT" ] || [ ! -f "$TRANSCRIPT" ]; then
    log "no transcript (session_id=${SESSION_ID:-none} cwd=${CWD:-none}); skipping"
    exit 0
fi

ARCHIVE_NAME="${SESSION_ID:-$(date +%Y%m%d_%H%M%S)}.jsonl"
ARCHIVE_PATH="$TRANSCRIPT_DIR/$ARCHIVE_NAME"
if ! cp "$TRANSCRIPT" "$ARCHIVE_PATH" 2>>"$LOG"; then
    log "cp failed: $TRANSCRIPT -> $ARCHIVE_PATH"
    # Continue with the source path — extract can read it directly.
    ARCHIVE_PATH="$TRANSCRIPT"
fi

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON3="$SCRIPT_DIR/.venv/bin/python3"
[ -x "$PYTHON3" ] || PYTHON3="python3"

OUT_PATH="$CONVERSATIONS_DIR/${ARCHIVE_NAME%.jsonl}.md"
if "$PYTHON3" "$SCRIPT_DIR/extract_conversation.py" \
        "$ARCHIVE_PATH" \
        --output "$OUT_PATH" 2>>"$LOG"; then
    log "wrote $OUT_PATH"
else
    log "extract failed for $ARCHIVE_PATH -> $OUT_PATH"
fi

exit 0
