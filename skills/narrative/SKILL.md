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
(`merger.py`), and the state renders to markdown (`renderer.py`). The rendered
`~/.claude/memory/projects/{project}.narrative.md` file is the narrative.
Projects that don't yet have a state JSON are bootstrapped with an empty stub.

## Step 1: Discover work

Call `narrative_coverage(project=PROJECT)` for the current project first, then
for any other projects surfaced by the session_start hook. `narrative_coverage`
computes unprocessed transcripts by diffing on-disk main-session transcripts
against `{project}.json.sessions[]` (i.e. sessions already merged).

To enumerate all projects with session activity:

```bash
python3 -c "
import sys; sys.path.insert(0, '/home/user/.claude/memory/lib')
from conversations import iter_sessions
seen = {fm.get('project') for fm in iter_sessions() if fm.get('project')}
for p in sorted(seen): print(p)
"
```

`narrative_coverage` also returns a `stale` list: sessions that ARE in
`sessions[]` but whose transcript kept growing after they were merged (a
multi-day session merged on day 1 and still running on day 6). Membership in
`sessions[]` only proves a session was merged once, not that it was merged in
full — so treat every entry in `stale` as work to redo, alongside
`unprocessed`. Each entry carries `merged_through`, `last_activity` and
`grew_days`.

Stale sessions take the same path as unprocessed ones, with two differences:
force a fresh extraction (skip the delta cache — the cached delta is the one
that already merged), and merge with `--rerun` (step 3).

If both `unprocessed` and `stale` are empty for a project, skip it.

## Step 2: Process each project

**Cross-project parallelism**: different projects write to different state
files / narratives, so they can run simultaneously.

**Within-project sequential**: each session's merged state feeds the next
session's input. Never run two sessions for the same project in parallel.

**Bootstrap vs. incremental**: if `{project}.json` doesn't exist, create the
empty stub (below) and run `narrative_coverage` — it will report every
main-session transcript the project has ever had as unprocessed, because the
merged-sessions list in the JSON is empty. If `{project}.json` already
exists, `narrative_coverage` returns the usual incremental diff.

### 2a. Create `{project}.json` if it doesn't exist

- Write the empty stub to `~/.claude/memory/projects/<project>.json`:

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

Then call `narrative_coverage(project=PROJECT)` — with an empty
`sessions[]`, it reports every main-session transcript as unprocessed. Use
that list.

After the bootstrap run finishes, `sessions[]` is populated and all
subsequent runs follow the normal incremental path automatically.

### 2b. Filter transcripts (applies to both paths)

Whichever list you picked in 2a (full history for bootstrap, or `unprocessed`
for incremental), keep only **main-session** transcripts. Skip any file
whose session_id starts with `agent-` — those are subagent transcripts and
must not trigger this pipeline. Sort the survivors chronologically by their
`started` timestamp (read the first JSONL record's `timestamp`, or derive
from file mtime if unavailable).

If a session's `~/.claude/memory/conversations/<session_id>.md` is missing,
**generate it rather than dropping the session**. The sweep in
`session_start.sh` is lazy — it only runs at session start against files newer
than its sentinel — so a session that ended after the last session start has
not been archived yet, and silently skipping it loses the work permanently:

```bash
# Find the live transcript (it may not be in the archive yet) and strip it.
SRC=$(find ~/.claude/projects -maxdepth 2 -name 'SESSION_ID.jsonl' | head -1)
cp -n "$SRC" ~/.claude/memory/transcripts/SESSION_ID.jsonl
python3 ~/.claude/memory/lib/extract_conversation.py "$SRC" \
  --output ~/.claude/memory/conversations/SESSION_ID.md --force
```

Only drop a session when no transcript exists in either location, and say so
in the Step 3 summary — a silently skipped session looks identical to one
that had nothing to say.

### 2c. For each main-session transcript, in order

1. **Check the delta cache** before spawning an agent:

   ```bash
   python3 ~/.claude/memory/lib/delta_cache.py check SESSION_ID ISO8601_START
   ```

   Prints `use_cache` or `reextract` on stdout; decision reason on stderr.
   Policy: exact hash match → always reuse; hash mismatch → re-extract with
   probability `exp(-age_days / 14d)`, deterministic per session_id; missing
   file → re-extract. If `use_cache`, skip step 2 and proceed to step 3.

2. **If `reextract`,** spawn the `delta-extractor` agent. Wait for it to
   finish and write its delta JSON, then stamp the current extractor hash
   into the file so subsequent runs can cache-hit it:

   ```
   Agent(
     description="Delta PROJECT: SESSION_ID",
     subagent_type="delta-extractor",
     prompt="""Project: PROJECT_NAME
   conversation_md_path: /home/user/.claude/memory/conversations/SESSION_ID.md
   project_state_path:   /home/user/.claude/memory/projects/PROJECT_NAME.json
   session_id:           SESSION_ID
   session_started_at:   ISO8601_START
   session_ended_at:     ISO8601_END
   output_path:          /home/user/.claude/memory/deltas/SESSION_ID.delta.json
   contested_path:       /home/user/.claude/memory/projects/PROJECT_NAME.contested.json

   Read the project state JSON and the conversation markdown. Produce the
   structured delta per your prompt spec and write it as JSON (only) to
   output_path. If contested_path exists, also emit `revaluations` per your
   Rule 14 — the renderer wrote it because a section ran out of budget. If it
   does not exist, skip that rule silently; nothing was cut.
   Do not modify the project state — the merger does that.
   Do NOT use worktree isolation. Do NOT commit anything.""",
     run_in_background=False
   )
   ```

   ```bash
   python3 ~/.claude/memory/lib/delta_cache.py stamp \
     ~/.claude/memory/deltas/SESSION_ID.delta.json
   ```

3. **Run the merger** on whichever delta is now on disk (cached or fresh):

   ```bash
   python3 ~/.claude/memory/lib/merger.py \
     ~/.claude/memory/projects/PROJECT_NAME.json \
     ~/.claude/memory/deltas/SESSION_ID.delta.json
   ```

   For a session from the `stale` list, add `--rerun`. Without it the merger
   refuses the delta (the session_id is already in `sessions[]`) and prints
   `Skipped ... (already merged)` — the rebuild still runs, so check the verb:
   `Merged` / `Re-merged` means the delta was applied, `Skipped` means it was
   not. With `--rerun` the merger de-duplicates re-emitted items by text,
   applies the new resolutions and revaluations, and refreshes the session's
   `ended` watermark so it stops reporting as stale.

   The merge must succeed before launching the next delta-extractor for this
   project — the next agent reads the updated `{project}.json` as input.

4. Before the first delta-extractor call for a run, ensure the deltas dir
   exists: `mkdir -p ~/.claude/memory/deltas`. The merger is idempotent
   per session_id; the cache is idempotent by `extractor_hash`. Leftover
   delta files are a feature, not debt — they act as the pre-processed
   cache so repeat runs skip LLM calls.

5. **If the delta-extractor's Write to `~/.claude/memory/deltas/...` fails
   or prompts repeatedly,** that's a missing permission, not a sandbox
   block. Do NOT reroute through `/tmp/` and `mv` — that just multiplies
   the prompts. Tell the user to add `Write(/home/user/.claude/memory/**)`
   to `~/.claude/settings.json`'s `permissions.allow` (it ships in
   `settings.yaml` as of the post-2026-05-11 install; older installs need
   to re-run `install.sh` or add it manually) and stop.

### 2d. Render after every merge

```bash
python3 ~/.claude/memory/lib/renderer.py \
  ~/.claude/memory/projects/PROJECT_NAME.json \
  ~/.claude/memory/projects/PROJECT_NAME.narrative.md
```

Render after **each** session's merge, not once at the end of the project.
The renderer is pure code and takes well under a second, and rendering is what
refreshes `{project}.contested.json`.

That sidecar is the input to the next session's Rule 14 re-valuation pass. It
is written once per render but consumed once per session, so if a project has
several sessions in one run and you only render at the end, every extractor
after the first grades against a snapshot the earlier ones have already acted
on — re-grading items that are already settled and overwriting judgement with
a stale view. Rendering each time keeps the sidecar current, and it disappears
by itself once nothing is being cut.

The rendered `.narrative.md` file is the narrative — session_start + subagent_start
hooks read it directly. Nothing else to do; the pipeline ends here.

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

Full spec: `docs/narrative-v2-format.md` in the llm_memory repo. Size is
enforced by the renderer per section (`SECTION_TOKEN_BUDGETS`), not by you —
you don't need to trim anything. 8 required sections: The Idea, Approach, What's Done, What We've
Learnt, What We Want To Do, Suggested Work, Resuming, Source Transcripts.

Key principle: content dissolves from specific sections into standing
sections. Information changes form, never disappears.
