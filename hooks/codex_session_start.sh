#!/bin/bash
# Codex SessionStart hook.  It intentionally stays small and shares the same
# cwd-to-project rule as session_start.sh rather than inventing a resolver.

INPUT=$(cat)
SOURCE=$(printf '%s' "$INPUT" | jq -r '.source // empty' 2>/dev/null)
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
MEMORY_DIR="${LLM_MEMORY_HOME:-$HOME/.claude/memory}"
PROJECT=$(printf '%s' "$CWD" | sed -n 's|.*/projects/\([^/]*\).*|\1|p')

case "$SOURCE" in
    startup|resume) ;;
    *) exit 0 ;;
esac
[ -n "$PROJECT" ] || exit 0

NARRATIVE="$MEMORY_DIR/projects/$PROJECT.narrative.md"
[ -f "$NARRATIVE" ] && cat "$NARRATIVE"

NEW_SESSIONS=$(python3 - "$MEMORY_DIR" "$PROJECT" <<'PY' 2>/dev/null
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
project = sys.argv[2]
merged = set()
try:
    state = json.loads((root / "projects" / f"{project}.json").read_text())
    merged = {str(s.get("session_id", "")) for s in state.get("sessions", [])}
except (OSError, ValueError, AttributeError):
    pass

count = 0
for conversation in (root / "conversations").glob("*.md"):
    try:
        head = conversation.read_text(errors="replace").split("\n---\n", 1)[0]
    except OSError:
        continue
    values = dict(line.split(": ", 1) for line in head.splitlines() if ": " in line)
    if values.get("project") == project and values.get("session_id") not in merged:
        count += 1
print(count)
PY
)
if [ "${NEW_SESSIONS:-0}" -gt 0 ] 2>/dev/null; then
    echo "AUTOMATIC TASK: $NEW_SESSIONS new session(s) since last narrative update for project '$PROJECT'."
fi
exit 0
