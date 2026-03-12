#!/bin/bash
# SubagentStop hook: notifies parent when a narrative-updater agent finishes.
# Fires when a subagent completes.
# Does NOT reload the full narrative — that happens at session start / post-compaction.
# Just tells the parent what changed so it has a lightweight update.

INPUT=$(cat)
AGENT_TYPE=$(echo "$INPUT" | jq -r '.agent_type // empty')
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')

# Check if this is a narrative-related agent and a signal file exists
if echo "$AGENT_TYPE" | grep -qi "narrative"; then
    if [ -n "$CWD" ] && [ -f "$CWD/.narrative_updated" ]; then
        SUMMARY=$(cat "$CWD/.narrative_updated")
        if [ -n "$SUMMARY" ] && [ "$SUMMARY" != "updated" ]; then
            echo "Narrative updated: $SUMMARY"
        else
            echo "Narrative updated for this project. Full narrative will load on next session start or compaction."
        fi
        rm -f "$CWD/.narrative_updated"
    fi
fi

exit 0
