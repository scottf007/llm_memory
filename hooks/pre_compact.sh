#!/bin/bash
# PreCompact hook: fires before context compaction.
# Outputs a message telling Claude to save important context to memory
# before the compaction summarizes and loses detail.

echo "COMPACTION IMMINENT. Before compaction proceeds, you MUST:
1. Call memory_store with type 'session_summary' to save: what you were working on, key decisions made, current progress, and any unfinished tasks.
2. If you made any corrections or learned preferences this session, store them as type 'correction' with importance 8+.
3. Include the project name in each memory_store call.
Do this NOW before context is lost."
