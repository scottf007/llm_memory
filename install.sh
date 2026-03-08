#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
MEMORY_DIR="$HOME/.claude/memory"
HOOKS_DIR="$SCRIPT_DIR/hooks"

echo "=== LLM Memory — Install ==="
echo ""

# --- Step 1: Python environment ---
echo "[1/5] Setting up Python environment..."
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install -q -r "$SCRIPT_DIR/requirements.txt"
echo "  Dependencies installed."

# --- Step 2: Memory directory ---
echo "[2/5] Creating memory directory..."
mkdir -p "$MEMORY_DIR"
mkdir -p "$MEMORY_DIR/transcripts"
echo "  $MEMORY_DIR"

# --- Step 3: Register MCP server ---
echo "[3/5] Registering MCP server with Claude Code..."
SERVER_CONFIG=$(cat <<EOF
{"type":"stdio","command":"$VENV_DIR/bin/python3","args":["$SCRIPT_DIR/server.py"]}
EOF
)
if command -v claude &> /dev/null; then
    claude mcp add-json llm_memory "$SERVER_CONFIG" --scope user
    echo "  MCP server registered."
else
    echo "  WARNING: 'claude' CLI not found. Add manually:"
    echo "    claude mcp add-json llm_memory '$SERVER_CONFIG' --scope user"
fi

# --- Step 4: Install hooks ---
echo "[4/5] Installing lifecycle hooks..."
LLM_MEMORY_INSTALLING=1 bash "$HOOKS_DIR/install_hooks.sh"

# --- Step 5: Initialize database and discover projects ---
echo "[5/5] Initializing database and scanning for existing transcripts..."

"$VENV_DIR/bin/python3" -c "
import sys
sys.path.insert(0, '$SCRIPT_DIR')

from server import init_db
init_db()
print('  Database initialized.')
"

# Discover projects from existing transcripts
"$VENV_DIR/bin/python3" -c "
import sys
sys.path.insert(0, '$SCRIPT_DIR')

from process_transcripts import find_transcripts, extract_session_data
from collections import defaultdict

transcripts = find_transcripts()
if not transcripts:
    print('  No existing transcripts found.')
    print('  Memories will be created as you use Claude Code.')
    sys.exit(0)

# Group by project
projects = defaultdict(lambda: {'count': 0, 'turns': 0})
for path, session_id in transcripts:
    data = extract_session_data(path)
    p = data['project']
    projects[p]['count'] += 1
    projects[p]['turns'] += data['turn_count']

print(f'  Found {len(transcripts)} transcripts across {len(projects)} projects:')
print()
for name in sorted(projects):
    info = projects[name]
    print(f'    {name:25s} {info[\"count\"]:3d} sessions, {info[\"turns\"]:5d} turns')
print()
print('  Processing transcripts into session logs...')
"

# Process transcripts into session_logs
"$VENV_DIR/bin/python3" "$SCRIPT_DIR/process_transcripts.py"

echo ""
echo "=== Install complete ==="
echo ""
echo "What happens next:"
echo "  1. Start a new Claude Code session (the MCP server activates on startup)"
echo "  2. Session hooks will auto-load your project context"
echo "  3. To generate a project narrative, start a session in the project"
echo "     directory and ask: 'Write the narrative for this project'"
echo ""
echo "Your projects are ready for narratives. Start in each project directory"
echo "and Claude will read the raw transcripts to build a rich project history."
