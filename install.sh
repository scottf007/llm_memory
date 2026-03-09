#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
MEMORY_DIR="$HOME/.claude/memory"
HOOKS_DIR="$SCRIPT_DIR/hooks"

echo "=== LLM Memory — Install ==="
echo ""

# --- Step 1: Python environment ---
echo "[1/8] Setting up Python environment..."
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install -q -r "$SCRIPT_DIR/requirements.txt"
echo "  Dependencies installed."

# --- Step 2: Memory directory ---
echo "[2/8] Creating memory directory..."
mkdir -p "$MEMORY_DIR"
mkdir -p "$MEMORY_DIR/transcripts"
mkdir -p "$MEMORY_DIR/records"
mkdir -p "$MEMORY_DIR/config"
echo "  $MEMORY_DIR"

# --- Step 3: Setting up shared config ---
echo "[3/8] Setting up shared config..."
# Copy CLAUDE.md to synced config dir if not already there
if [ ! -f "$MEMORY_DIR/config/CLAUDE.md" ]; then
    if [ -f "$SCRIPT_DIR/claude-rules-example.md" ]; then
        cp "$SCRIPT_DIR/claude-rules-example.md" "$MEMORY_DIR/config/CLAUDE.md"
        echo "  Copied CLAUDE.md to $MEMORY_DIR/config/"
    fi
fi
# Apply CLAUDE.md if no global one exists, or if config version is newer
if [ ! -f "$HOME/.claude/CLAUDE.md" ] || [ "$MEMORY_DIR/config/CLAUDE.md" -nt "$HOME/.claude/CLAUDE.md" ]; then
    cp "$MEMORY_DIR/config/CLAUDE.md" "$HOME/.claude/CLAUDE.md"
    echo "  Applied CLAUDE.md to ~/.claude/CLAUDE.md"
fi

# Create Syncthing ignore file
cat > "$MEMORY_DIR/.stignore" << 'STIGNORE'
memory.db
memory.db-wal
memory.db-shm
STIGNORE
echo "  Created .stignore for Syncthing"

# --- Step 4: Register MCP server ---
echo "[4/8] Registering MCP server with Claude Code..."
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

# --- Step 5: Install hooks ---
echo "[5/8] Installing lifecycle hooks..."
LLM_MEMORY_INSTALLING=1 bash "$HOOKS_DIR/install_hooks.sh"

# --- Step 6: Apply shared settings ---
echo "[6/8] Applying shared settings..."
if [ -f "$SCRIPT_DIR/settings.yaml" ]; then
    "$VENV_DIR/bin/python3" "$SCRIPT_DIR/apply_settings.py" "$SCRIPT_DIR/settings.yaml"
    echo "  Applied shared settings from settings.yaml"
fi

# --- Step 7: Initialize database and discover projects ---
echo "[7/8] Initializing database and scanning for existing transcripts..."

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

# --- Step 8: Process transcripts ---
echo "[8/8] Processing transcripts into session logs..."
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
echo "For multi-device sync with Syncthing:"
echo "  Share ~/.claude/memory/ between your devices"
echo "  The .stignore file excludes the local database"
echo "  Records and transcripts will sync automatically"
echo ""
echo "Your projects are ready for narratives. Start in each project directory"
echo "and Claude will read the raw transcripts to build a rich project history."
