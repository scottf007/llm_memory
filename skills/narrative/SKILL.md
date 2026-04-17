---
name: narrative
description: Update project narratives from raw JSONL transcripts. Runs for all projects with unprocessed transcripts.
user_invocable: true
---

# /narrative — Update All Project Narratives

Update project narratives by processing unprocessed JSONL transcripts into the
living narrative document. Works across all projects automatically.

One pipeline, no branching: each session becomes a structured delta
(`delta-extractor` agent), deltas merge into a per-project JSON state
(`merger.py`), and the state renders to markdown (`renderer.py`) which is then
stored as a `narrative` memory. Projects that don't yet have a state JSON are
bootstrapped with an empty stub — no legacy freeform path exists.

## Step 1: Discover work

Call `narrative_coverage(project=PROJECT)` for the current project first, then
for any other projects surfaced by the session_start hook or memory.

To find all projects with session_logs:

```bash
sqlite3 ~/.claude/memory/memory.db "SELECT DISTINCT project FROM memories WHERE type='session_log' AND project <> '' ORDER BY project;"
```

If `unprocessed` is empty for a project, skip it.

## Step 2: Process each project

**Cross-project parallelism**: different projects write to different state
files / narratives, so they can run simultaneously.

**Within-project sequential**: each session's merged state feeds the next
session's input. Never run two sessions for the same project in parallel.

### 2a. Bootstrap `{project}.json` if missing

If `~/.claude/memory/projects/{project}.json` does NOT exist, create it with
an empty stub before doing anything else. No agent required — just write:

```json
{
  "schema_version": "0.1",
  "project": "<project_name>",
  "last_updated": null,
  "summary": {},
  "operations": [],
  "decisions": [],
  "goals": [],
  "suggestions": [],
  "learnings": [],
  "done": [],
  "sessions": [],
  "narrative": {"rendered_at": null, "record_uuid": null, "drift_audit": null}
}
```

Write it to `~/.claude/memory/projects/<project_name>.json` with the project
name substituted. The delta-extractor and merger take over from there.

### 2b. Filter transcripts

From `unprocessed`, keep only **main-session** transcripts. Skip any file
whose session_id starts with `agent-` — those are subagent transcripts and
must not trigger this pipeline. Sort the survivors chronologically by their
`started` timestamp (read the first JSONL record's `timestamp`, or derive
from file mtime if unavailable).

### 2c. For each main-session transcript, in order

1. Spawn the `delta-extractor` agent with the session's inputs. Wait for it
   to finish and write its delta JSON before proceeding.

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
   output_path:          ~/.claude/memory/deltas/SESSION_ID.delta.json

   Read the project state JSON and the conversation markdown. Produce the
   structured delta per your prompt spec and write it as JSON (only) to
   output_path. Do not modify the project state — the merger does that.
   Do NOT use worktree isolation. Do NOT commit anything.""",
     run_in_background=False
   )
   ```

2. Once the agent completes, run the merger:

   ```bash
   python3 ~/.claude/memory/lib/merger.py \
     ~/.claude/memory/projects/PROJECT_NAME.json \
     ~/.claude/memory/deltas/SESSION_ID.delta.json
   ```

   The merge must succeed before launching the next delta-extractor for this
   project — the next agent reads the updated `{project}.json` as input.

3. Before the first delta-extractor call for a run, ensure the deltas dir
   exists: `mkdir -p ~/.claude/memory/deltas`. The merger is idempotent
   per session_id, so leftover delta files are safe to keep — they're
   useful for debugging if the render later looks wrong.

### 2d. Render once per project

After all deltas for the project have merged:

```bash
python3 ~/.claude/memory/lib/renderer.py \
  ~/.claude/memory/projects/PROJECT_NAME.json \
  ~/.claude/memory/projects/PROJECT_NAME.narrative.md
```

### 2e. Store the narrative memory

Read the rendered markdown and call:

```
memory_store(
  type="narrative",
  project="PROJECT_NAME",
  content=<contents of PROJECT_NAME.narrative.md>,
  transcript_ref=[<all transcript paths now processed>]
)
```

This creates a new narrative row that supersedes the prior one.

## Step 3: Summary

When all projects finish, emit a compact summary:

```
Narrative update complete:
  - PROJECT_A: N session(s) processed -> ~/.claude/memory/projects/PROJECT_A.narrative.md
  - PROJECT_B: M session(s) processed -> ~/.claude/memory/projects/PROJECT_B.narrative.md
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
