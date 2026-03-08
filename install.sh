#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
MEMORY_DIR="$HOME/.claude/memory"
echo "=== LLM Memory — Install ==="

# Create venv
echo "Creating Python virtual environment..."
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install -q -r "$SCRIPT_DIR/requirements.txt"
echo "Dependencies installed."

# Create memory database directory
mkdir -p "$MEMORY_DIR"
echo "Memory directory: $MEMORY_DIR"

# Add MCP server config using claude CLI (writes to ~/.claude.json)
echo "Configuring Claude Code MCP server..."
SERVER_CONFIG=$(cat <<EOF
{"type":"stdio","command":"$VENV_DIR/bin/python3","args":["$SCRIPT_DIR/server.py"]}
EOF
)
if command -v claude &> /dev/null; then
    claude mcp add-json llm_memory "$SERVER_CONFIG" --scope user
    echo "MCP server registered with Claude Code."
else
    echo "WARNING: 'claude' CLI not found. Add the MCP server manually:"
    echo "  claude mcp add-json llm_memory '$SERVER_CONFIG' --scope user"
fi

echo ""
echo "=== Install complete ==="
echo "Restart Claude Code to activate the memory server."
echo "The server will start automatically when Claude Code launches."
