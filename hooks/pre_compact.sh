#!/bin/bash
# PreCompact hook: fires before context compaction.
# Tells Claude to save important notes before context is lost.

echo "COMPACTION IMMINENT. Before compaction proceeds:
1. Store any unsaved corrections or important decisions as notes (type 'note', include project name, importance 8+ for corrections).
2. The raw transcript is preserved — the project narrative will be updated from it at next session start.
Do this NOW before context is lost."
