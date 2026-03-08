#!/bin/bash
# SessionEnd hook: archives transcript and creates a session_log entry.

INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id')
TRANSCRIPT=$(echo "$INPUT" | jq -r '.transcript_path')

if [ -z "$TRANSCRIPT" ] || [ ! -f "$TRANSCRIPT" ]; then
    exit 0
fi

# Archive raw transcript
TRANSCRIPT_DIR="$HOME/.claude/memory/transcripts"
mkdir -p "$TRANSCRIPT_DIR"
ARCHIVE_NAME="${SESSION_ID:-$(date +%Y%m%d_%H%M%S)}.jsonl"
cp "$TRANSCRIPT" "$TRANSCRIPT_DIR/$ARCHIVE_NAME" 2>/dev/null || true

DB="$HOME/.claude/memory/memory.db"
if [ ! -f "$DB" ]; then
    exit 0
fi

# Skip if we already logged this session
EXISTING=$(sqlite3 "$DB" "SELECT COUNT(*) FROM memories WHERE session_id='$SESSION_ID' AND type='session_log';" 2>/dev/null)
if [ "$EXISTING" -gt "0" ]; then
    exit 0
fi

# Extract project name, turn count, and brief summary from transcript
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
read -r PROJECT TURN_COUNT SUMMARY < <("$SCRIPT_DIR/.venv/bin/python3" -c "
import json, sys
from pathlib import Path

transcript_path = '$TRANSCRIPT'
messages = []
project = ''
turn_count = 0
try:
    with open(transcript_path, 'r') as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                if not project:
                    cwd = entry.get('cwd', '')
                    if cwd:
                        parts = Path(cwd).parts
                        for i, part in enumerate(parts):
                            if part == 'projects' and i + 1 < len(parts):
                                project = parts[i + 1]
                                break
                if entry.get('type') == 'user':
                    turn_count += 1
                if entry.get('type') == 'assistant':
                    msg = entry.get('message', {})
                    content = msg.get('content', [])
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get('type') == 'text':
                                text = block['text'].strip()
                                if len(text) > 50:
                                    messages.append(text[:200])
                    elif isinstance(content, str) and len(content) > 50:
                        messages.append(content[:200])
            except:
                continue
except:
    sys.exit(0)

if messages:
    summary = messages[-1][:300].replace(\"'\", \"''\")
    print(f'{project}\t{turn_count}\t{summary}')
" 2>/dev/null)

if [ -n "$SUMMARY" ]; then
    CONTENT="Session $SESSION_ID for ${PROJECT:-unknown}, $TURN_COUNT turns. $SUMMARY"
    if [ -n "$PROJECT" ]; then
        sqlite3 "$DB" "INSERT INTO memories (type, content, project, session_id, importance, transcript_ref) VALUES ('session_log', '$CONTENT', '$PROJECT', '$SESSION_ID', 3, '~/.claude/memory/transcripts/$ARCHIVE_NAME');" 2>/dev/null
    else
        sqlite3 "$DB" "INSERT INTO memories (type, content, session_id, importance, transcript_ref) VALUES ('session_log', '$CONTENT', '$SESSION_ID', 3, '~/.claude/memory/transcripts/$ARCHIVE_NAME');" 2>/dev/null
    fi
fi

exit 0
