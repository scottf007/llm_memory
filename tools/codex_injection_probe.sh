#!/bin/bash
# Controlled Codex-context injection probe.  The real invocation is reserved
# for the subject seat; --dry-run is safe to use without a Codex installation.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PROMPT="quote the sentinel line from your project context"

print_commands() {
    printf 'hook\trules-line\twrapper\n'
    printf 'codex exec --dangerously-bypass-hook-trust %q\n' "$PROMPT"
    printf 'codex exec %q\n' "$PROMPT"
    printf 'codex exec %q\n' "$PROMPT"
}

if [ "${1:-}" = "--dry-run" ]; then
    print_commands
    exit 0
fi
[ "$#" -eq 0 ] || { echo "usage: $0 [--dry-run]" >&2; exit 2; }
command -v codex >/dev/null 2>&1 || { echo "codex_injection_probe: codex not found" >&2; exit 1; }

PROBE_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/llm-memory-codex-probe.XXXXXX")
trap 'rm -rf "$PROBE_ROOT"' EXIT
PROJECT_ROOT="$PROBE_ROOT/projects/codex-injection-probe"
MEMORY_ROOT="$PROBE_ROOT/memory"
SENTINEL="LLM_MEMORY_CODEX_PROBE_SENTINEL_$(date +%s)"
mkdir -p "$PROJECT_ROOT/.codex" "$MEMORY_ROOT/projects"
printf '%s\n' "$SENTINEL" > "$MEMORY_ROOT/projects/codex-injection-probe.narrative.md"
cat > "$PROJECT_ROOT/.codex/hooks.json" <<EOF
{"SessionStart":[{"matcher":"startup|resume","hooks":[{"type":"command","command":"$SCRIPT_DIR/hooks/codex_session_start.sh","timeout":15}]}]}
EOF

printf 'row\tpass/fail\trollout-id\n'
latest_rollout() {
    find "$HOME/.codex/sessions" -name 'rollout-*.jsonl' -type f -printf '%T@ %p\n' 2>/dev/null \
        | sort -n | tail -1 | sed -E 's|.*rollout-[0-9T-]+-([0-9a-fA-F-]{36})\.jsonl$|\1|'
}

run_subject() {
    local row="$1"
    shift
    local output rollout result="fail"
    output=$("$@" 2>&1) || true
    if printf '%s' "$output" | grep -Fq "$SENTINEL"; then
        result="pass"
    fi
    rollout=$(latest_rollout)
    [ -n "$rollout" ] || rollout="unknown"
    printf '%s\t%s\t%s\n' "$row" "$result" "$rollout"
}

run_subject hook bash -c 'cd "$1" && LLM_MEMORY_HOME="$2" codex exec --dangerously-bypass-hook-trust "$3"' _ \
    "$PROJECT_ROOT" "$MEMORY_ROOT" "$PROMPT"
printf '%s\n' "Read and quote this required project context line: $SENTINEL" > "$PROJECT_ROOT/AGENTS.md"
run_subject rules-line bash -c 'cd "$1" && LLM_MEMORY_HOME="$2" codex exec "$3"' _ \
    "$PROJECT_ROOT" "$MEMORY_ROOT" "$PROMPT"
run_subject wrapper bash -c 'cd "$1" && LLM_MEMORY_HOME="$2" MEMORY_WRAP_PYTHON="$3" "$4/tools/memory_wrap" codex "$5"' _ \
    "$PROJECT_ROOT" "$MEMORY_ROOT" "$SCRIPT_DIR/.venv/bin/python3" "$SCRIPT_DIR" "$PROMPT"
