#!/bin/bash
# SessionEnd hook: auto-saves a session summary from the transcript.
# Extracts the last few substantial assistant messages as a rough summary.

INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id')
TRANSCRIPT=$(echo "$INPUT" | jq -r '.transcript_path')

if [ -z "$TRANSCRIPT" ] || [ ! -f "$TRANSCRIPT" ]; then
    exit 0
fi

# Archive raw transcript before anything else
TRANSCRIPT_DIR="$HOME/.claude/memory/transcripts"
mkdir -p "$TRANSCRIPT_DIR"
ARCHIVE_NAME="${SESSION_ID:-$(date +%Y%m%d_%H%M%S)}.jsonl"
cp "$TRANSCRIPT" "$TRANSCRIPT_DIR/$ARCHIVE_NAME" 2>/dev/null || true

DB="$HOME/.claude/memory/memory.db"
if [ ! -f "$DB" ]; then
    exit 0
fi

# Skip if PreCompact or Claude already saved a summary for this session
EXISTING=$(sqlite3 "$DB" "SELECT COUNT(*) FROM memories WHERE session_id='$SESSION_ID' AND type='session_summary';" 2>/dev/null)
if [ "$EXISTING" -gt "0" ]; then
    exit 0
fi

# Extract last few assistant text blocks as a rough summary
SUMMARY=$(/home/scott/projects/llm_memory/.venv/bin/python3 -c "
import json, sys

transcript_path = '$TRANSCRIPT'
messages = []
try:
    with open(transcript_path, 'r') as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                if entry.get('type') == 'assistant':
                    msg = entry.get('message', {})
                    content = msg.get('content', [])
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get('type') == 'text':
                                text = block['text'].strip()
                                if len(text) > 50:
                                    messages.append(text[:500])
                    elif isinstance(content, str) and len(content) > 50:
                        messages.append(content[:500])
            except:
                continue
except:
    sys.exit(0)

if messages:
    recent = messages[-3:]
    summary = ' | '.join(recent)[:1500]
    # Escape for SQL
    summary = summary.replace(\"'\", \"''\")
    print(summary)
" 2>/dev/null)

if [ -n "$SUMMARY" ]; then
    sqlite3 "$DB" "INSERT INTO memories (type, content, session_id, importance) VALUES ('session_summary', 'Auto-saved session summary: $SUMMARY', '$SESSION_ID', 6);" 2>/dev/null
fi

exit 0
