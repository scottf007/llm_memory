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

# Extract project name and last few assistant text blocks as a rough summary
read -r PROJECT SUMMARY < <(/home/scott/projects/llm_memory/.venv/bin/python3 -c "
import json, sys
from pathlib import Path

transcript_path = '$TRANSCRIPT'
messages = []
project = ''
try:
    with open(transcript_path, 'r') as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                # Derive project from cwd
                if not project:
                    cwd = entry.get('cwd', '')
                    if cwd:
                        parts = Path(cwd).parts
                        for i, part in enumerate(parts):
                            if part == 'projects' and i + 1 < len(parts):
                                project = parts[i + 1]
                                break
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
    summary = summary.replace(\"'\", \"''\")
    # Output: PROJECT<tab>SUMMARY (project may be empty)
    print(f'{project}\t{summary}')
" 2>/dev/null)

if [ -n "$SUMMARY" ]; then
    if [ -n "$PROJECT" ]; then
        sqlite3 "$DB" "INSERT INTO memories (type, content, project, session_id, importance, transcript_ref) VALUES ('session_summary', 'Auto-saved session summary: $SUMMARY', '$PROJECT', '$SESSION_ID', 6, '~/.claude/memory/transcripts/$ARCHIVE_NAME');" 2>/dev/null
    else
        sqlite3 "$DB" "INSERT INTO memories (type, content, session_id, importance, transcript_ref) VALUES ('session_summary', 'Auto-saved session summary: $SUMMARY', '$SESSION_ID', 6, '~/.claude/memory/transcripts/$ARCHIVE_NAME');" 2>/dev/null
    fi
fi

exit 0
