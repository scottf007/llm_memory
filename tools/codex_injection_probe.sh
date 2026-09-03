#!/bin/bash
# Controlled Codex-context injection probe.  The real invocation is reserved
# for the subject seat; --dry-run is safe to use without a Codex installation.
set -euo pipefail

PROMPT="quote the sentinel line from your project context"

print_commands() {
    printf 'hook\trules-line\twrapper\n'
    printf 'codex exec --dangerously-bypass-hook-trust %q\n' "$PROMPT"
    printf 'codex exec %q\n' "$PROMPT"
    printf 'codex exec %q\n' "$PROMPT"
}

if [ "${1:-}" = "--dry-run" ]; then
    print_commands
    echo "scratch HOME layout: copy only ~/.codex/auth.json; do not copy config.toml, hooks.json, memories, or sessions." >&2
    exit 0
fi
[ "$#" -eq 0 ] || { echo "usage: $0 [--dry-run]" >&2; exit 2; }
command -v codex >/dev/null 2>&1 || { echo "codex_injection_probe: codex not found" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

REAL_HOME="$HOME"
AUTH_SOURCE="$REAL_HOME/.codex/auth.json"
[ -f "$AUTH_SOURCE" ] || {
    echo "codex_injection_probe: refusing to run: required auth file is absent at $AUTH_SOURCE" >&2
    exit 1
}

PROBE_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/llm-memory-codex-probe.XXXXXX")
SUBJECT_HOME=$(mktemp -d "${TMPDIR:-/tmp}/llm-memory-codex-home.XXXXXX")
trap 'rm -rf "$PROBE_ROOT" "$SUBJECT_HOME"' EXIT
PROJECT_ROOT="$PROBE_ROOT/projects/codex-injection-probe"
MEMORY_ROOT="$PROBE_ROOT/memory"
SENTINEL="LLM_MEMORY_CODEX_PROBE_SENTINEL_$(date +%s)"
mkdir -p "$PROJECT_ROOT/.codex" "$MEMORY_ROOT/projects" "$SUBJECT_HOME/.codex"
# Seed only the credential needed for codex exec.  Hooks, config, memories,
# and rollout history remain isolated so the measurement cannot touch them.
cp "$AUTH_SOURCE" "$SUBJECT_HOME/.codex/auth.json"
printf '%s\n' "$SENTINEL" > "$MEMORY_ROOT/projects/codex-injection-probe.narrative.md"
cat > "$PROJECT_ROOT/.codex/hooks.json" <<EOF
{"SessionStart":[{"matcher":"startup|resume","hooks":[{"type":"command","command":"$SCRIPT_DIR/hooks/codex_session_start.sh","timeout":15}]}]}
EOF

printf 'row\tpass/fail\texit-status\trollout-id\tstderr-last-3\n'
latest_rollout() {
    find "$SUBJECT_HOME/.codex/sessions" -name 'rollout-*.jsonl' -type f -printf '%T@ %p\n' 2>/dev/null \
        | sort -n | tail -1 | sed -E 's|.*rollout-[0-9T-]+-([0-9a-fA-F-]{36})\.jsonl$|\1|'
}

run_subject() {
    local row="$1"
    shift
    local stdout_file="$PROBE_ROOT/$row.stdout"
    local stderr_file="$PROBE_ROOT/$row.stderr"
    local status rollout result="fail" stderr_tail
    if "$@" >"$stdout_file" 2>"$stderr_file"; then
        status=0
    else
        status=$?
    fi
    if grep -Fq "$SENTINEL" "$stdout_file"; then
        result="pass"
    fi
    rollout=$(latest_rollout)
    [ -n "$rollout" ] || rollout="unknown"
    stderr_tail=$(tail -n 3 "$stderr_file" | tr '\n\t' '  ')
    [ -n "$stderr_tail" ] || stderr_tail="-"
    printf '%s\t%s\t%s\t%s\t%s\n' "$row" "$result" "$status" "$rollout" "$stderr_tail"
}

run_subject hook bash -c 'cd "$1" && HOME="$2" LLM_MEMORY_HOME="$3" codex exec --dangerously-bypass-hook-trust "$4"' _ \
    "$PROJECT_ROOT" "$SUBJECT_HOME" "$MEMORY_ROOT" "$PROMPT"
printf '%s\n' "Read and quote this required project context line: $SENTINEL" > "$PROJECT_ROOT/AGENTS.md"
run_subject rules-line bash -c 'cd "$1" && HOME="$2" LLM_MEMORY_HOME="$3" codex exec "$4"' _ \
    "$PROJECT_ROOT" "$SUBJECT_HOME" "$MEMORY_ROOT" "$PROMPT"
run_subject wrapper bash -c 'cd "$1" && HOME="$2" LLM_MEMORY_HOME="$3" MEMORY_WRAP_PYTHON="$4" "$5/tools/memory_wrap" codex "$6"' _ \
    "$PROJECT_ROOT" "$SUBJECT_HOME" "$MEMORY_ROOT" "$SCRIPT_DIR/.venv/bin/python3" "$SCRIPT_DIR" "$PROMPT"
