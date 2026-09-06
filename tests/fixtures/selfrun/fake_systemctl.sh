#!/bin/bash
# Fake `systemctl` for hermetic selfrun-extraction tests. Never touches the
# real service manager. Every invocation is appended verbatim (one line, the
# full argv) to $FAKE_SYSTEMCTL_LOG so tests can assert exactly what a hook
# or installer dispatched. Exits 0 unless $FAKE_SYSTEMCTL_FAIL is set, so
# failure-path tests can force a non-zero systemctl without a second script.
set -u
LOG="${FAKE_SYSTEMCTL_LOG:-}"
if [ -n "$LOG" ]; then
    printf '%s\n' "$*" >> "$LOG"
fi
if [ -n "${FAKE_SYSTEMCTL_FAIL:-}" ]; then
    exit 1
fi
exit 0
