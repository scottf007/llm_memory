#!/bin/bash
# PreCompact hook: fires before context compaction.
# Tells Claude to save important notes before context is lost.

echo "COMPACTION IMMINENT. Before compaction proceeds:
1. Store any unsaved corrections or important decisions as notes (type 'note', include project name, importance 8+ for corrections).
2. Update the project narrative NOW using memory_store (type 'narrative'). Summarize everything from this session before context is lost.
Do this NOW — context is about to be compacted."
