#!/bin/bash
# Launch the LLM Memory web dashboard
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${1:-8765}"
HOST="${2:-0.0.0.0}"

echo "LLM Memory Dashboard"
echo "http://$HOST:$PORT"
echo ""

exec "$SCRIPT_DIR/.venv/bin/python3" "$SCRIPT_DIR/dashboard.py" --host "$HOST" --port "$PORT"
