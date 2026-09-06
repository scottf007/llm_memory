#!/bin/bash
# Install the recovery timer only when a user systemd manager is available.
set -u

XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
UNIT_DIR="$XDG_CONFIG_HOME/systemd/user"
SYSTEMCTL_CMD=()
if [ -n "${LLM_MEMORY_SYSTEMCTL:-}" ]; then
    if [ -f "$LLM_MEMORY_SYSTEMCTL" ] && [ ! -x "$LLM_MEMORY_SYSTEMCTL" ]; then
        SYSTEMCTL_CMD=(bash "$LLM_MEMORY_SYSTEMCTL")
    else
        SYSTEMCTL_CMD=("$LLM_MEMORY_SYSTEMCTL")
    fi
elif command -v systemctl >/dev/null 2>&1; then
    SYSTEMCTL_CMD=(systemctl)
else
    echo "systemctl is unavailable; install skipped. Run: systemctl --user daemon-reload"
    echo "Then run: systemctl --user enable --now llm-memory-extract.timer"
    exit 0
fi

# A binary on PATH is not enough: containers and non-login shells commonly
# have systemctl but no reachable user manager.  Do not leave unit files that
# cannot be activated; print the recovery command instead.
if ! "${SYSTEMCTL_CMD[@]}" --user show-environment >/dev/null 2>&1; then
    echo "systemctl user manager is unavailable; install skipped. Run: systemctl --user daemon-reload"
    echo "Then run: systemctl --user enable --now llm-memory-extract.timer"
    exit 0
fi

mkdir -p "$UNIT_DIR"
SERVICE="$UNIT_DIR/llm-memory-extract.service"
TIMER="$UNIT_DIR/llm-memory-extract.timer"
cat > "$SERVICE" <<'EOF'
[Unit]
Description=llm_memory narrative extraction worker
OnFailure=llm-memory-extract-failed.service

[Service]
Type=oneshot
ExecStart=%h/.claude/memory/lib/.venv/bin/python3 %h/.claude/memory/lib/extraction_worker.py run --once
Environment=LLM_MEMORY_HOME=%h/.claude/memory
EOF
cat > "$TIMER" <<'EOF'
[Unit]
Description=Recover missed llm_memory extraction requests

[Timer]
OnUnitActiveSec=5min
Persistent=true
Unit=llm-memory-extract.service

[Install]
WantedBy=timers.target
EOF
cat > "$UNIT_DIR/llm-memory-extract-failed.service" <<'EOF'
[Unit]
Description=Record llm_memory extraction worker failure

[Service]
Type=oneshot
Environment=LLM_MEMORY_HOME=%h/.claude/memory
ExecStart=%h/.claude/memory/lib/.venv/bin/python3 %h/.claude/memory/lib/extraction_worker.py mark-failed --message systemd-extraction-worker-failed
EOF

"${SYSTEMCTL_CMD[@]}" --user daemon-reload
MARKER="$UNIT_DIR/.llm-memory-extract-enabled"
if [ ! -f "$MARKER" ]; then
    "${SYSTEMCTL_CMD[@]}" --user enable --now llm-memory-extract.timer
    : > "$MARKER"
fi
