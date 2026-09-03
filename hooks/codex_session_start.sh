#!/bin/bash
# Codex SessionStart hook.  It shares its project and new-session accounting
# primitives with session_start.sh.

INPUT=$(cat)
SOURCE=$(printf '%s' "$INPUT" | jq -r '.source // empty' 2>/dev/null)
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
MEMORY_DIR="${LLM_MEMORY_HOME:-$HOME/.claude/memory}"
export LLM_MEMORY_HOME="$MEMORY_DIR"
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=hooks/lib_session_common.sh
source "$SCRIPT_DIR/hooks/lib_session_common.sh"
PROJECT=$(resolve_project_from_cwd "$CWD")

case "$SOURCE" in
    startup|resume) ;;
    *) exit 0 ;;
esac
[ -n "$PROJECT" ] || exit 0

NARRATIVE="$MEMORY_DIR/projects/$PROJECT.narrative.md"
[ -f "$NARRATIVE" ] && cat "$NARRATIVE"

COUNT_PYTHON=$(resolve_hook_python "$SCRIPT_DIR" 2>/dev/null || true)
if [ -n "$COUNT_PYTHON" ] && count_new_sessions "$PROJECT" "$SCRIPT_DIR" "$COUNT_PYTHON"; then
    NEW_SESSIONS="$NEW_SESSION_COUNT"
else
    NEW_SESSIONS=""
    echo "LLM_MEMORY_WARN: new-session count unavailable ($NEW_SESSION_COUNT_ERROR)"
fi
if [ "${NEW_SESSIONS:-0}" -gt 0 ] 2>/dev/null; then
    echo "AUTOMATIC TASK: $NEW_SESSIONS new session(s) since last narrative update for project '$PROJECT'."
fi
exit 0
