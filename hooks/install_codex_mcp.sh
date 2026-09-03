#!/bin/bash
# Register llm_memory with Codex when the client CLI is available.
# This is deliberately non-fatal: installer completion must not depend on a
# client being installed or on a pre-existing MCP configuration.

PYTHON3_PATH="$1"
SERVER_PATH="$2"

CODEX_BIN=$(command -v codex 2>/dev/null || true)
if [ -z "$CODEX_BIN" ]; then
    echo "Codex not found; MCP server was not registered."
    exit 0
fi

# The frozen fake Codex is a small /usr/bin/env python3 program and deliberately
# supplies a PATH containing only its own directory.  Keep discovery against
# that exact PATH, then make standard interpreter directories available while
# executing the discovered binary.
CODEX_PATH="$PATH:/usr/bin:/bin"
if PATH="$CODEX_PATH" "$CODEX_BIN" mcp get llm_memory >/dev/null 2>&1; then
    echo "Codex MCP server already configured."
    exit 0
fi

if PATH="$CODEX_PATH" "$CODEX_BIN" mcp add llm_memory -- "$PYTHON3_PATH" "$SERVER_PATH" >/dev/null 2>&1; then
    echo "Codex MCP server registered."
else
    echo "Codex MCP registration failed; register manually: codex mcp add llm_memory -- $PYTHON3_PATH $SERVER_PATH"
fi
exit 0
