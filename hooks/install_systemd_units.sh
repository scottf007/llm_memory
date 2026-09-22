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

# --- extraction units are OPT-IN ------------------------------------------
# Creating these units is not a neutral act. hooks/session_end.sh ends with
#     systemctl --user start --no-block llm-memory-extract.service
# so the SERVICE EXISTING is sufficient for extraction to run: every session
# end starts it. The timer is a recovery sweep, not the driver, which is why
# "the extraction timer is disabled" has never been protection.
#
# That belief cost USD 358 on 2026-09-13 and a further USD 19.91 on SCOTT-PC
# on 2026-09-22 across 7 extractions at ~USD 2.84 each -- none of which the
# spend ledger recorded, because _record_spend sits after an early return on
# the parse-failure path in _merge.
#
# Regenerating these on every --update also silently undid an operator's
# deliberate removal of them, on a nightly timer, with no output saying so.
#
# So: create them only when asked. Set LLM_MEMORY_ENABLE_EXTRACTION=1 to
# restore the previous behaviour. Nothing else changes -- the update timer
# below is always installed, because that is how fixes reach a machine.
EXTRACTION_OPT_IN="${LLM_MEMORY_ENABLE_EXTRACTION:-0}"

SERVICE="$UNIT_DIR/llm-memory-extract.service"
TIMER="$UNIT_DIR/llm-memory-extract.timer"
if [ "$EXTRACTION_OPT_IN" = "1" ]; then
# systemd --user does NOT inherit the login shell's PATH; a oneshot gets the
# manager's own minimal default, which has no $HOME on it. The worker shells
# out to the `claude` CLI, and a user-scoped npm/native install puts that in
# %h/.local/bin -- so the unit could never see a CLI that worked fine in a
# terminal. That is how last_success stayed null since setup on this machine:
# every activation died on FileNotFoundError('claude'). The worker now also
# searches these directories itself, but the unit must still be able to see a
# normally-installed CLI without relying on that fallback.
cat > "$SERVICE" <<EOF
[Unit]
Description=llm_memory narrative extraction worker
OnFailure=llm-memory-extract-failed.service

[Service]
Type=oneshot
ExecStart=$MEMORY_DIR/lib/.venv/bin/python3 $MEMORY_DIR/lib/extraction_worker.py run --once
Environment=LLM_MEMORY_HOME=$MEMORY_DIR
Environment=PATH=%h/.local/bin:%h/.npm-global/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
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
else
    echo "  Extraction units NOT installed (opt-in). The SessionEnd hook starts"
    echo "  llm-memory-extract.service directly, so creating it is what enables"
    echo "  extraction -- the timer is only a recovery sweep."
    echo "  Spend is banked on every exit from _merge and capped per day by"
    echo "  LLM_MEMORY_EXTRACT_DAY_CAP_USD (default 5)."
    echo "  Installing them drains runtime/extraction-requests/ at the next"
    echo "  session end -- including requests for sessions a manual /narrative"
    echo "  has already merged, which are paid for again. Count that directory"
    echo "  first; the request_ids in *.extraction-status.json are a stale"
    echo "  snapshot written by the worker, not a queue depth."
    echo "  Set LLM_MEMORY_ENABLE_EXTRACTION=1 to install them."
fi

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
if [ "$EXTRACTION_OPT_IN" = "1" ] && [ ! -f "$MARKER" ]; then
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
