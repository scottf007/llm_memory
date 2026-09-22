#!/bin/bash
# Install the recovery timer only when a user systemd manager is available.
set -u

XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
UNIT_DIR="$XDG_CONFIG_HOME/systemd/user"
# Resolve the memory root the same way install.sh does. The units used to
# hardcode %h/.claude/memory, which is only correct on a machine that still has
# the pre-move layout or the compatibility symlink. A machine whose root is
# elsewhere got units pointing at a path that does not exist, and a oneshot
# that cannot start is close to invisible.
MEMORY_DIR="${LLM_MEMORY_HOME:-$HOME/.llm-memory}"
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
cat > "$SERVICE" <<EOF
[Unit]
Description=llm_memory narrative extraction worker
OnFailure=llm-memory-extract-failed.service

[Service]
Type=oneshot
ExecStart=$MEMORY_DIR/lib/.venv/bin/python3 $MEMORY_DIR/lib/extraction_worker.py run --once
Environment=LLM_MEMORY_HOME=$MEMORY_DIR
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
cat > "$UNIT_DIR/llm-memory-extract-failed.service" <<EOF
[Unit]
Description=Record llm_memory extraction worker failure

[Service]
Type=oneshot
Environment=LLM_MEMORY_HOME=$MEMORY_DIR
ExecStart=$MEMORY_DIR/lib/.venv/bin/python3 $MEMORY_DIR/lib/extraction_worker.py mark-failed --message systemd-extraction-worker-failed
EOF

# Daily update check. The updater, the GitHub SHA comparison and the VERSION
# gate already exist in install.sh --update; only the schedule was missing, so
# a machine stayed on whatever version it was last installed with. Installed
# code is per-machine and deliberately not synced, which makes this the only
# route a fix reaches another machine.
cat > "$UNIT_DIR/llm-memory-update.service" <<EOF
[Unit]
Description=llm_memory update check
After=network-online.target

[Service]
Type=oneshot
Environment=LLM_MEMORY_HOME=$MEMORY_DIR
ExecStart=/bin/bash $MEMORY_DIR/lib/install.sh --update
EOF
cat > "$UNIT_DIR/llm-memory-update.timer" <<'EOF'
[Unit]
Description=Check daily for a new llm_memory version

[Timer]
OnCalendar=daily
# Spread the GitHub call so several machines waking together do not collide,
# and still run after a machine that was asleep at the scheduled time.
RandomizedDelaySec=2h
Persistent=true
Unit=llm-memory-update.service

[Install]
WantedBy=timers.target
EOF

"${SYSTEMCTL_CMD[@]}" --user daemon-reload
MARKER="$UNIT_DIR/.llm-memory-extract-enabled"
if [ ! -f "$MARKER" ]; then
    "${SYSTEMCTL_CMD[@]}" --user enable --now llm-memory-extract.timer
    : > "$MARKER"
fi
# Separate marker: an operator who deliberately stopped the extraction timer
# (as happened on 2026-09-13 to halt a spend burn) must not have it re-enabled
# by the arrival of the update timer, and must still receive updates.
UPDATE_MARKER="$UNIT_DIR/.llm-memory-update-enabled"
if [ ! -f "$UPDATE_MARKER" ]; then
    "${SYSTEMCTL_CMD[@]}" --user enable --now llm-memory-update.timer
    : > "$UPDATE_MARKER"
fi
