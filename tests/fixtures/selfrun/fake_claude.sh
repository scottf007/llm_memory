#!/bin/bash
# Fake Claude/local backend command for hermetic selfrun-extraction tests.
# Stands in for `$LLM_MEMORY_CLAUDE_CMD`. Never spends real money, never
# talks to a network.
#
# Env contract:
#   FAKE_CLAUDE_LOG_DIR   required. Each invocation writes call-<n>.argv
#                         (one arg per line) and call-<n>.stdin (the prompt
#                         piped in) to this directory, n starting at 1 and
#                         monotonically increasing across the test.
#   FAKE_CLAUDE_RESPONSE_QUEUE
#                         required. A directory containing response-1.json,
#                         response-2.json, ... consumed in order, one per
#                         invocation. If the queue is shorter than the call
#                         count, the last response file repeats.
#   FAKE_CLAUDE_EXIT_CODE optional. If set, exit with this code after
#                         logging (and before printing a response), so
#                         failure/backend-error tests need no separate script.
#   FAKE_CLAUDE_SLEEP_ON_CALL / FAKE_CLAUDE_SLEEP_SECONDS
#                         optional. If the current call number equals
#                         FAKE_CLAUDE_SLEEP_ON_CALL, sleep for
#                         FAKE_CLAUDE_SLEEP_SECONDS AFTER logging the call
#                         but BEFORE responding — opens a deterministic
#                         window for a test to act mid-drain.
set -u
LOG_DIR="${FAKE_CLAUDE_LOG_DIR:?FAKE_CLAUDE_LOG_DIR not set}"
mkdir -p "$LOG_DIR"

COUNTER_FILE="$LOG_DIR/.call-count"
N=0
[ -f "$COUNTER_FILE" ] && N=$(cat "$COUNTER_FILE")
N=$((N + 1))
echo "$N" > "$COUNTER_FILE"

for a in "$@"; do printf '%s\n' "$a"; done > "$LOG_DIR/call-$N.argv"
cat > "$LOG_DIR/call-$N.stdin"
touch "$LOG_DIR/call-$N.started"

if [ "${FAKE_CLAUDE_SLEEP_ON_CALL:-}" = "$N" ]; then
    sleep "${FAKE_CLAUDE_SLEEP_SECONDS:-1}"
fi

if [ -n "${FAKE_CLAUDE_EXIT_CODE:-}" ] && [ "${FAKE_CLAUDE_EXIT_CODE}" != "0" ]; then
    exit "$FAKE_CLAUDE_EXIT_CODE"
fi

QUEUE="${FAKE_CLAUDE_RESPONSE_QUEUE:?FAKE_CLAUDE_RESPONSE_QUEUE not set}"
RESP="$QUEUE/response-$N.json"
if [ ! -f "$RESP" ]; then
    # Repeat the highest-numbered response file for calls beyond the queue.
    RESP=$(ls "$QUEUE"/response-*.json 2>/dev/null | sort -t- -k2 -n | tail -1)
fi
if [ -z "$RESP" ] || [ ! -f "$RESP" ]; then
    echo "fake_claude.sh: no response queued for call $N" >&2
    exit 1
fi
cat "$RESP"
exit 0
