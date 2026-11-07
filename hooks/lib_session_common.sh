#!/bin/bash
# Shared SessionStart primitives.  Both Claude and Codex hooks source this so
# the project resolver and the new-session accounting policy cannot drift.

resolve_project_from_cwd() {
    local cwd="$1"
    printf '%s' "$cwd" | sed -n 's|.*/projects/\([^/]*\).*|\1|p'
}

resolve_hook_python() {
    local script_dir="$1"
    local candidate="$script_dir/.venv/bin/python3"
    if [ -x "$candidate" ]; then
        printf '%s\n' "$candidate"
    elif command -v python3 >/dev/null 2>&1; then
        command -v python3
    else
        return 1
    fi
}

# Count session envelopes which have not been merged into project state.  A
# failed count is unknown, not zero: stdout is the only hook channel the model
# receives, so preserve the failure there instead of silently suppressing an
# overdue /narrative prompt.
count_new_sessions() {
    local project="$1"
    local script_dir="$2"
    local python_bin="$3"
    local count_file err_file status count first_error
    count_file=$(mktemp "${TMPDIR:-/tmp}/llm-memory-count.XXXXXX") || {
        NEW_SESSION_COUNT=""
        NEW_SESSION_COUNT_ERROR="could not create temporary count file"
        return 1
    }
    err_file=$(mktemp "${TMPDIR:-/tmp}/llm-memory-count.XXXXXX") || {
        rm -f "$count_file"
        NEW_SESSION_COUNT=""
        NEW_SESSION_COUNT_ERROR="could not create temporary error file"
        return 1
    }
    "$python_bin" -c "
import sys, json, pathlib
sys.path.insert(0, '$script_dir')
from conversations import list_sessions
proj='$project'
logged=set(list_sessions(proj))
try:
    from tools.memory_config import memory_root
    with open(memory_root() / 'projects' / (proj+'.json')) as f: d=json.load(f)
    merged={s.get('session_id') for s in d.get('sessions',[]) if not str(s.get('session_id','')).startswith(('audit-','agent-'))}
except FileNotFoundError:
    merged=set()
print(len(logged - merged))
" >"$count_file" 2>"$err_file"
    status=$?
    count=$(<"$count_file")
    first_error=$(sed -n '1p' "$err_file")
    rm -f "$count_file" "$err_file"
    if [ "$status" -ne 0 ] || [ -z "$count" ] || ! [[ "$count" =~ ^[0-9]+$ ]]; then
        NEW_SESSION_COUNT=""
        NEW_SESSION_COUNT_ERROR="${first_error:-no count output}"
        return 1
    fi
    NEW_SESSION_COUNT="$count"
    NEW_SESSION_COUNT_ERROR=""
    return 0
}
