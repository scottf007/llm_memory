#!/bin/bash
# Launch the LLM Memory web dashboard
# Works from either the git clone or the installed lib/ directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MEMORY_DIR="${LLM_MEMORY_HOME:-$HOME/.claude/memory}"
export LLM_MEMORY_HOME="$MEMORY_DIR"
LIB_DIR="$MEMORY_DIR/lib"
PORT="${1:-8765}"
HOST="${2:-0.0.0.0}"

# Use local venv if it exists (git clone), otherwise use lib/
if [ -d "$SCRIPT_DIR/.venv" ]; then
    VENV="$SCRIPT_DIR/.venv"
    DASHBOARD="$SCRIPT_DIR/dashboard.py"
elif [ -d "$LIB_DIR/.venv" ]; then
    VENV="$LIB_DIR/.venv"
    DASHBOARD="$LIB_DIR/dashboard.py"
else
    echo "ERROR: No Python venv found. Run install.sh first."
    exit 1
fi

echo "LLM Memory Dashboard"
echo "http://$HOST:$PORT"
echo ""

exec "$VENV/bin/python3" "$DASHBOARD" --host "$HOST" --port "$PORT"
