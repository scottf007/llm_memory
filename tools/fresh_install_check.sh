#!/usr/bin/env bash
# Prove a checkout can be installed on a clean Ubuntu machine.  With an
# explicit LLM_MEMORY_HOME this verifies that install (the hermetic test
# surface); otherwise it creates a tarball and invokes itself inside Docker
# or Podman.
set -u

CHECK_IDS=(lib-version venv-mcp mcp-list-tools hooks-installed session-narrative transcript-ingest)

dry_run() {
    printf '%s\n' "${CHECK_IDS[@]}"
}

report() {
    local status="$1" id="$2" message="$3"
    printf '%s %s: %s\n' "$status" "$id" "$message"
}

verify_install() {
    local memory_dir lib_dir py
    memory_dir="${LLM_MEMORY_HOME:-$HOME/.claude/memory}"
    lib_dir="$memory_dir/lib"
    py="$lib_dir/.venv/bin/python3"
    local failed=0 version="" tools=""

    if [ -s "$lib_dir/VERSION" ]; then
        version=$(tr -d '\r\n' < "$lib_dir/VERSION")
        report PASS lib-version "VERSION=$version"
    else
        report FAIL lib-version "VERSION is missing or empty at $lib_dir/VERSION"
        failed=1
    fi

    if [ -x "$py" ] && "$py" -c 'import mcp' >/dev/null 2>&1; then
        report PASS venv-mcp "venv imports mcp"
    else
        report FAIL venv-mcp "venv cannot import mcp"
        failed=1
    fi

    if [ "$failed" -eq 0 ]; then
        tools=$(LLM_MEMORY_HOME="$memory_dir" "$py" - "$lib_dir/server.py" <<'PY' 2>/dev/null
import anyio
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(command=sys.executable, args=[sys.argv[1]])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            print(",".join(sorted(tool.name for tool in result.tools)))

anyio.run(main)
PY
)
        if [ "$tools" = "memory_search,narrative_coverage,project_lookup,resume" ]; then
            report PASS mcp-list-tools "$tools"
        else
            report FAIL mcp-list-tools "expected four memory tools; got ${tools:-no response}"
            failed=1
        fi
    else
        report FAIL mcp-list-tools "skipped because venv-mcp failed"
        failed=1
    fi

    if [ -f "$HOME/.claude/settings.json" ] \
        && grep -q 'session_start.sh' "$HOME/.claude/settings.json" \
        && grep -q 'session_end.sh' "$HOME/.claude/settings.json"; then
        report PASS hooks-installed "Claude lifecycle hooks are registered"
    else
        report FAIL hooks-installed "Claude hooks are absent from $HOME/.claude/settings.json"
        failed=1
    fi

    local hook_output
    hook_output=$(printf '%s' '{"source":"startup","cwd":"/home/fixture/projects/fresh-install-fixture","session_id":"fixture-session-0001"}' \
        | HOME="$HOME" LLM_MEMORY_HOME="$memory_dir" timeout 45 bash "$lib_dir/hooks/session_start.sh" 2>&1) || hook_output=""
    if printf '%s' "$hook_output" | grep -q 'This is the seeded narrative for the fresh-install check.'; then
        report PASS session-narrative "seeded narrative reached SessionStart output"
    else
        report FAIL session-narrative "seeded narrative was not emitted by SessionStart"
        failed=1
    fi

    if HOME="$HOME" LLM_MEMORY_HOME="$memory_dir" "$py" "$lib_dir/process_transcripts.py" --quiet >/dev/null 2>&1 \
        && find "$memory_dir/conversations" -type f -name '*fixture-session-0001*.md' -print -quit 2>/dev/null | grep -q .; then
        report PASS transcript-ingest "seeded Claude transcript produced a conversation"
    else
        report FAIL transcript-ingest "seeded Claude transcript was not ingested"
        failed=1
    fi

    if [ "$failed" -ne 0 ]; then
        return 1
    fi
    printf 'SUMMARY: VERSION=%s TOOLS=%s\n' "$version" "$tools"
}

container_proof() {
    local engine="" image="${LLM_MEMORY_FRESH_INSTALL_IMAGE:-ubuntu:24.04}"
    if command -v docker >/dev/null 2>&1; then
        engine=docker
    elif command -v podman >/dev/null 2>&1; then
        engine=podman
    else
        echo 'ERROR: docker or podman is required for the fresh-install proof.' >&2
        return 1
    fi

    local repo_root work archive tree_sha mutation="${1:-}"
    repo_root=$(cd "$(dirname "$0")/.." && pwd -P)
    work=$(mktemp -d "${TMPDIR:-/tmp}/llm-memory-fresh-install.XXXXXX")
    trap 'rm -rf "$work"' RETURN
    archive="$work/llm_memory.tar.gz"
    tree_sha=$(git -C "$repo_root" rev-parse HEAD)
    if [ -n "$(git -C "$repo_root" status --porcelain)" ]; then
        echo "NOTE: working tree dirty; proving HEAD $tree_sha, not the working tree" >&2
    fi
    git -C "$repo_root" archive --format=tar.gz \
        --prefix="llm_memory-$tree_sha/" "$tree_sha" > "$archive"

    "$engine" run --rm -i \
        -e DEBIAN_FRONTEND=noninteractive \
        -e HOME=/tmp/llm-memory-home \
        -e LLM_MEMORY_HOME=/tmp/llm-memory-home/.claude/memory \
        -e LLM_MEMORY_TARBALL_URL=/archive/llm_memory.tar.gz \
        -e LLM_MEMORY_TARBALL_SHA="$tree_sha" \
        -e LLM_MEMORY_FRESH_INSTALL_INNER=1 \
        -e LLM_MEMORY_FRESH_INSTALL_MUTATION="$mutation" \
        -v "$repo_root:/work:ro" -v "$work:/archive:ro" \
        "$image" bash -s <<'CONTAINER'
set -eu
apt-get update -qq
apt-get install -y -qq ca-certificates curl jq python3 sqlite3 tar
if [ "${LLM_MEMORY_FRESH_INSTALL_MUTATION:-}" != no-venv ]; then
    apt-get install -y -qq python3-venv
fi
install_log=$(mktemp)
if ! bash /work/install.sh >"$install_log" 2>&1; then
    cat "$install_log"
    echo 'ERROR: installer failed on clean Ubuntu.' >&2
    exit 1
fi
cat "$install_log"
if [ "${LLM_MEMORY_FRESH_INSTALL_MUTATION:-}" = no-venv ]; then
    if ! grep -q 'Missing: python3-venv' "$install_log"; then
        echo 'ERROR: no-venv mutation did not require python3-venv.' >&2
        exit 1
    fi
    if grep -q 'sudo: command not found' "$install_log"; then
        echo 'ERROR: no-venv mutation tried sudo as root.' >&2
        exit 1
    fi
fi
if ! grep -q "WARNING: 'claude' CLI not found. Add manually:" "$install_log"; then
    echo 'ERROR: missing Claude manual-registration warning.' >&2
    exit 1
fi
if ! grep -q "claude mcp add-json llm_memory" "$install_log"; then
    echo 'ERROR: missing Claude manual-registration command.' >&2
    exit 1
fi
if ! grep -q "Codex not found; MCP server was not registered." "$install_log"; then
    echo 'ERROR: missing Codex no-client warning.' >&2
    exit 1
fi
mkdir -p "$LLM_MEMORY_HOME/config" "$LLM_MEMORY_HOME/projects" "$HOME/.claude/projects/fixture-encoded"
touch "$LLM_MEMORY_HOME/config/no-auto-update"
cat > "$LLM_MEMORY_HOME/projects/fresh-install-fixture.narrative.md" <<'EOF'
# Fixture narrative

This is the seeded narrative for the fresh-install check.
EOF
cat > "$HOME/.claude/projects/fixture-encoded/fixture-session-0001.jsonl" <<'EOF'
{"type":"user","cwd":"/home/fixture/projects/fresh-install-fixture","timestamp":"2026-09-06T00:00:00.000Z","message":{"content":"hello from the fresh-install fixture"}}
{"type":"assistant","timestamp":"2026-09-06T00:00:01.000Z","message":{"content":[{"type":"text","text":"hi -- fixture reply"}]}}
EOF
bash /work/tools/fresh_install_check.sh
CONTAINER
}

case "${1:-}" in
    --dry-run)
        dry_run
        ;;
    --mutate)
        if [ "${2:-}" != no-venv ]; then
            echo "Usage: $0 [--dry-run|--mutate no-venv]" >&2
            exit 2
        fi
        container_proof no-venv
        ;;
    '')
        if [ -n "${LLM_MEMORY_HOME:-}" ] || [ "${LLM_MEMORY_FRESH_INSTALL_INNER:-}" = 1 ]; then
            verify_install
        else
            container_proof
        fi
        ;;
    *)
        echo "Usage: $0 [--dry-run|--mutate no-venv]" >&2
        exit 2
        ;;
esac
