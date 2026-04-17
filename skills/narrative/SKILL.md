---
name: narrative
description: Update project narratives from raw JSONL transcripts. Runs for all projects with unprocessed transcripts.
user_invocable: true
---

# /narrative — Update All Project Narratives

Update project narratives by processing unprocessed JSONL transcripts into the
living narrative document. Works across all projects automatically.

Two pipelines are supported, selected **per project**:

- **Path A — structured-state pipeline** (preferred): project has a
  `~/.claude/memory/projects/{project}.json` file. Sessions are processed by
  the `delta-extractor` agent, merged into the JSON by `merger.py`, then
  rendered to markdown by `renderer.py`, then stored as a `narrative` memory.
- **Path B — legacy pipeline** (fallback): project has no `{project}.json`
  yet. Sessions are processed by the `narrative-updater` agent exactly as
  before. Over time, projects move to Path A via a one-time backfill.

## Step 1: Discover work

Call `narrative_coverage(project=PROJECT)` for the current project first, then
for any other projects surfaced by the session_start hook or memory.

To find all projects with session_logs:

```bash
sqlite3 ~/.claude/memory/memory.db "SELECT DISTINCT project FROM memories WHERE type='session_log' AND project <> '' ORDER BY project;"
```

For each project, decide pipeline:

```bash
test -f ~/.claude/memory/projects/{project}.json && echo "PATH_A" || echo "PATH_B"
```

If `unprocessed` is empty for a project, skip it.

## Step 2: Process each project

**Cross-project parallelism**: different projects write to different state
files / narratives, so they can run simultaneously.

**Within-project sequential**: each session's merged state feeds the next
session's input. Never run two sessions for the same project in parallel.

### Path A — structured-state pipeline

1. **Filter transcripts.** From `unprocessed`, keep only **main-session**
   transcripts. Skip any file whose session_id starts with `agent-` — those
   are subagent transcripts and must not trigger this pipeline. Sort the
   survivors chronologically by their `started` timestamp (read the first
   JSONL record's `timestamp`, or derive from file mtime if unavailable).

2. **For each main-session transcript, in order:**

   a. Spawn the `delta-extractor` agent with the session's inputs. Wait for
      it to finish and write its delta JSON before proceeding.

      ```
      Agent(
        description="Delta PROJECT: SESSION_ID",
        subagent_type="delta-extractor",
        prompt="""Project: PROJECT_NAME
      conversation_md_path: ~/.claude/memory/conversations/SESSION_ID.md
      project_state_path:   ~/.claude/memory/projects/PROJECT_NAME.json
      session_id:           SESSION_ID
      session_started_at:   ISO8601_START
      session_ended_at:     ISO8601_END
      output_path:          /tmp/tmp_delta_SESSION_ID.json

      Read the project state JSON and the conversation markdown. Produce the
      structured delta per your prompt spec and write it as JSON (only) to
      output_path. Do not modify the project state — the merger does that.
      Do NOT use worktree isolation. Do NOT commit anything.""",
        run_in_background=False
      )
      ```

   b. Once the agent completes, run the merger:

      ```bash
      python3 ~/.claude/memory/lib/merger.py \
        ~/.claude/memory/projects/PROJECT_NAME.json \
        /tmp/tmp_delta_SESSION_ID.json
      ```

      The merge must succeed before the next session — the next
      delta-extractor needs the updated `{project}.json` as its input.

   c. (Optional) Remove `/tmp/tmp_delta_SESSION_ID.json` after a successful
      merge. The merger is idempotent per session_id, so leaving it is safe.

3. **Render once per project**, after all deltas for that project have
   merged:

   ```bash
   python3 ~/.claude/memory/lib/renderer.py \
     ~/.claude/memory/projects/PROJECT_NAME.json \
     ~/.claude/memory/projects/PROJECT_NAME.narrative.md
   ```

4. **Store the narrative memory**: read the rendered markdown and call:

   ```
   memory_store(
     type="narrative",
     project="PROJECT_NAME",
     content=<contents of PROJECT_NAME.narrative.md>,
     transcript_ref=[<all transcript paths now processed>]
   )
   ```

   This creates a new narrative row that supersedes the prior one.

### Path B — legacy pipeline (no `{project}.json`)

Use the existing `narrative-updater` agent. One agent per transcript,
sequential within the project. This is the v1 path; a future backfill will
migrate the project to Path A.

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

Wait for each agent to complete before spawning the next for the same project
(each supersedes the prior narrative; carry the new UUID forward into the
next prompt).

## Step 3: Summary

When all projects finish, emit a compact summary:

```
Narrative update complete:
  - PROJECT_A (Path A): N session(s) processed -> ~/.claude/memory/projects/PROJECT_A.narrative.md
  - PROJECT_B (Path B): M transcript(s) processed -> narrative memory UUID ...
  - PROJECT_C: no unprocessed transcripts
```

Do not reload full narratives — they load on next session start.

## When No Work Is Needed

If every project shows 0 unprocessed transcripts, say so and stop. Don't force
updates.

## Narrative Format Reference

Full spec: `docs/narrative-v2-format.md` in the llm_memory repo. Target ≤5,000
tokens. 8 required sections: The Idea, Approach, What's Done, What We've
Learnt, What We Want To Do, Suggested Work, Resuming, Source Transcripts.

Key principle: content dissolves from specific sections into standing
sections. Information changes form, never disappears.
