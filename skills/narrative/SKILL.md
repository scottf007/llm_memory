---
name: narrative
description: Update project narratives from raw JSONL transcripts. Runs for all projects with unprocessed transcripts.
user_invocable: true
---

# /narrative — Update All Project Narratives

Update project narratives by reading raw JSONL transcripts and merging them into
the living narrative document. Works across all projects automatically.

## Process

1. **Discover work needed**: Call `narrative_coverage(project=PROJECT)` for the
   current project first, then for any other projects listed by session_start
   hook or known from memory.

   To find all projects with session_logs:
   ```bash
   sqlite3 ~/.claude/memory/memory.db "SELECT DISTINCT project FROM memories WHERE type='session_log' AND project <> '' ORDER BY project;"
   ```

2. **For each project with unprocessed transcripts**, spawn narrative-updater
   agents **sequentially** (one at a time per project). Different projects can
   run in parallel.

   ```
   Agent(
     description="Narrate PROJECT: FILENAME",
     subagent_type="narrative-updater",
     prompt="""Project: PROJECT_NAME
   Project directory: /path/to/project (or best guess from project name)
   Current narrative UUID: UUID_FROM_COVERAGE_RESULT

   Process this ONE transcript file:
     FILE_PATH

   Read the current narrative via memory_get, read this transcript, merge the new
   information, and store the updated narrative via memory_store with the updated
   transcript_ref array.

   File conventions: work in project dir, prefix temp files with tmp_, clean up when done.
   Do NOT use worktree isolation. Do NOT commit anything.""",
     run_in_background=True
   )
   ```

3. **Sequential within a project**: Each agent supersedes the previous narrative.
   Wait for one agent to complete before spawning the next for the same project.
   Note the new narrative UUID from each agent's result for the next prompt.

4. **Parallel across projects**: Agents for different projects write to different
   narratives, so they can run simultaneously.

5. **After all agents complete**: Report which projects were updated and how many
   transcripts were processed. Do not reload full narratives — they load on next
   session start.

## When No Work Is Needed

If all projects show 0 unprocessed transcripts, just say so. Don't force updates.

## Narrative Format Reference

Full spec: `docs/narrative-v2-format.md` in the llm_memory repo. Target ≤5,000
tokens. 8 required sections: The Idea, Approach, What's Done, What We've Learnt,
What We Want To Do, Suggested Work, Resuming, Source Transcripts.

Key principle: content dissolves from specific sections into standing sections.
Information changes form, never disappears.
