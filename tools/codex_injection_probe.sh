#!/bin/bash
# Controlled Codex-context injection probe. The real invocation is reserved
# for the subject seat; --dry-run is safe without a Codex installation.
set -euo pipefail

PROMPT="quote the sentinel line from your project context"

print_commands() {
    printf 'hook\trules-line\twrapper\n'
    printf 'codex exec --dangerously-bypass-hook-trust %q\n' "$PROMPT"
    printf 'codex exec %q\n' "$PROMPT"
    printf 'tools/memory_wrap codex %q\n' "$PROMPT"
}

if [ "${1:-}" = "--dry-run" ]; then
    print_commands
    # Keep stdout's four-line T6 contract stable. These details are on stderr.
    printf '%s\n' 'scratch project paths: /tmp/llm-memory-codex-probe/hook/project /tmp/llm-memory-codex-probe/rules-line/project /tmp/llm-memory-codex-probe/wrapper/project' >&2
    printf '%s\n' 'scratch HOME layout: copy only ~/.codex/auth.json; do not copy config.toml, hooks.json, memories, or sessions.' >&2
    printf '%s\n' 'rules-line MCP registration: codex mcp add llm_memory -- <python> <lib>/server.py' >&2
    printf '%s\n' 'wrapper MCP registration: codex mcp add llm_memory -- <python> <lib>/server.py' >&2
    printf '%s\n' 'wrapper MEMORY_WRAP_PYTHON: installed lib venv, then harness python3 (must import mcp).' >&2
    exit 0
fi
[ "$#" -eq 0 ] || { echo "usage: $0 [--dry-run]" >&2; exit 2; }
command -v codex >/dev/null 2>&1 || { echo "codex_injection_probe: codex not found" >&2; exit 1; }

SCRIPT_DIR="$(cd -- "${BASH_SOURCE[0]%/*}/.." >/dev/null 2>&1 && pwd)"
REAL_HOME="$HOME"
AUTH_SOURCE="$REAL_HOME/.codex/auth.json"
[ -f "$AUTH_SOURCE" ] || {
    echo "codex_injection_probe: refusing to run: required auth file is absent at $AUTH_SOURCE" >&2
    exit 1
}

PROBE_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/llm-memory-codex-probe.XXXXXX")
trap 'rm -rf "$PROBE_ROOT"' EXIT
SENTINEL="LLM_MEMORY_CODEX_PROBE_SENTINEL_$(date +%s)"

latest_rollout() {
    local home="$1" sessions
    sessions="$home/.codex/sessions"
    # Fresh homes do not have sessions yet. The old find pipeline returned 1
    # here; under pipefail, rollout=$(latest_rollout) then exited the probe.
    [ -d "$sessions" ] || return 0
    find "$sessions" -name 'rollout-*.jsonl' -type f -printf '%T@ %p\n' 2>/dev/null \
        | sort -n | tail -1 | sed -E 's|.*rollout-[0-9T-]+-([0-9a-fA-F-]{36})\.jsonl$|\1|'
}

prepare_row() {
    local row="$1"
    ROW_ROOT="$PROBE_ROOT/$row"
    ROW_PROJECT="$ROW_ROOT/project"
    ROW_HOME="$ROW_ROOT/home"
    ROW_MEMORY="$ROW_ROOT/memory"
    mkdir -p "$ROW_PROJECT/.codex" "$ROW_HOME/.codex" "$ROW_MEMORY/projects"
    cp "$AUTH_SOURCE" "$ROW_HOME/.codex/auth.json"
    printf '%s\n' "$SENTINEL" > "$ROW_MEMORY/projects/codex-injection-probe.narrative.md"
}

install_hook_config() {
    cat > "$ROW_PROJECT/.codex/hooks.json" <<EOF
{"SessionStart":[{"matcher":"startup|resume","hooks":[{"type":"command","command":"$SCRIPT_DIR/hooks/codex_session_start.sh","timeout":15}]}]}
EOF
}

install_rules_line() {
    cat > "$ROW_PROJECT/AGENTS.md" <<'EOF'
**Memory.** At the start of every session, before answering anything, call the
`llm_memory` MCP tool `resume` with `project` set to the last path segment of
the repository root (for `/home/user/projects/llm_memory`, that is
`llm_memory`). If it returns nothing useful, call `project_lookup` with the
same `project` and the topic you are about to work on. Do this without asking
permission. If both return empty, say so explicitly rather than assuming the
project has no history.
EOF
}

register_mcp() {
    local stdout_file="$1" stderr_file="$2" python_bin="$3"
    [ -n "$python_bin" ] || return 1
    HOME="$ROW_HOME" LLM_MEMORY_HOME="$ROW_MEMORY" codex mcp add llm_memory -- "$python_bin" "$SCRIPT_DIR/server.py" >"$stdout_file" 2>"$stderr_file"
}

resolve_wrapper_python() {
    local candidate installed_lib
    installed_lib="${LLM_MEMORY_LIB:-$REAL_HOME/.claude/memory/lib}"
    for candidate in "$installed_lib/.venv/bin/python3" "$(command -v python3 2>/dev/null || true)"; do
        [ -n "$candidate" ] && [ -x "$candidate" ] || continue
        if "$candidate" -c 'import mcp' >/dev/null 2>&1; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

run_subject() {
    local row="$1" stdout_file stderr_file before after status result="fail" stderr_tail
    shift
    stdout_file="$ROW_ROOT/$row.stdout"
    stderr_file="$ROW_ROOT/$row.stderr"
    before=$(latest_rollout "$ROW_HOME")
    if "$@" >"$stdout_file" 2>"$stderr_file"; then status=0; else status=$?; fi
    grep -Fq "$SENTINEL" "$stdout_file" && result="pass"
    after=$(latest_rollout "$ROW_HOME")
    if [ -n "$after" ] && [ "$after" != "$before" ]; then ROLLOUT="$after"; else ROLLOUT="none"; fi
    stderr_tail=$(tail -n 3 "$stderr_file" | tr '\n\t' '  ')
    [ -n "$stderr_tail" ] || stderr_tail="-"
    printf '%s\t%s\t%s\t%s\t%s\n' "$row" "$result" "$status" "$ROLLOUT" "$stderr_tail"
}

register_or_note() {
    local row="$1" python_bin="$2" stdout_file="$ROW_ROOT/$1.mcp.stdout" stderr_file="$ROW_ROOT/$1.mcp.stderr"
    if ! register_mcp "$stdout_file" "$stderr_file" "$python_bin"; then
        printf 'codex mcp add failed; subject still executed\n' >> "$stderr_file"
    fi
}

printf 'row\tpass/fail\texit-status\trollout-id\tstderr-last-3\n'

prepare_row hook
install_hook_config
run_subject hook bash -c 'cd "$1" && HOME="$2" LLM_MEMORY_HOME="$3" codex exec --dangerously-bypass-hook-trust "$4"' _ "$ROW_PROJECT" "$ROW_HOME" "$ROW_MEMORY" "$PROMPT"

prepare_row rules-line
install_rules_line
RULES_PYTHON=$(resolve_wrapper_python || true)
register_or_note rules-line "$RULES_PYTHON"
run_subject rules-line bash -c 'cd "$1" && HOME="$2" LLM_MEMORY_HOME="$3" codex exec "$4"' _ "$ROW_PROJECT" "$ROW_HOME" "$ROW_MEMORY" "$PROMPT"

prepare_row wrapper
WRAPPER_PYTHON=$(resolve_wrapper_python || true)
if [ -z "$WRAPPER_PYTHON" ]; then
    printf 'wrapper\tfail\t1\tnone\tno interpreter can import mcp for MEMORY_WRAP_PYTHON\n'
else
    register_or_note wrapper "$WRAPPER_PYTHON"
    run_subject wrapper bash -c 'cd "$1" && HOME="$2" LLM_MEMORY_HOME="$3" MEMORY_WRAP_PYTHON="$4" "$5/tools/memory_wrap" codex "$6"' _ "$ROW_PROJECT" "$ROW_HOME" "$ROW_MEMORY" "$WRAPPER_PYTHON" "$SCRIPT_DIR" "$PROMPT"
fi
