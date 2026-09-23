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
`$MEMORY_ROOT/projects/{project}.narrative.md` file is the narrative.
Projects that don't yet have a state JSON are bootstrapped with an empty stub.

Before any step, resolve the store root with the same convention as the
runtime code and hooks:

```bash
MEMORY_ROOT="${LLM_MEMORY_HOME:-$HOME/.llm-memory}"
printf '%s\n' "$MEMORY_ROOT"
```

Keep this value for the whole run. Bash tool calls do not share exported shell
state, so every independently executed shell snippet below repeats the
assignment before expanding `$MEMORY_ROOT`. The `Agent(...)` prompt is not a
shell, so interpolate the printed absolute value there before launching the
extractor; do not hand the subagent a literal `$MEMORY_ROOT` token.

Before a manual drain, take the same per-project gate as the automatic worker.
This is the filesystem form of `narrative_lock.project_lock`: the lock file is
the shared contract, including for a manual run that cannot import Python.
Keep every read/extract/merge/render command for one project inside one
`flock` invocation; running separate shell snippets releases the gate between
steps and can race a SessionEnd request.

**You MUST pass the held descriptor down, or the drain deadlocks against
itself.** `merger.py` and `renderer.py` each take this same lock internally via
`narrative_lock.active_project_lock`. If you hold an outer `flock` and do not
tell them, they block on the lock you are holding and exit **3** with
`LLM_MEMORY_WARN: narrative update already running`. That failure is
indistinguishable from a real concurrent run, so the drain looks gated when it
actually did nothing. `active_project_lock` reads `LLM_MEMORY_NARRATIVE_LOCK`
(format `<project>:<fd>`) and `inherited_lock` verifies the descriptor really
is the same inode as the project lock file -- the variable alone is never
sufficient authority, so a wrong value fails closed rather than skipping the
gate.

Open the lock on an explicit descriptor, export it, and run the steps inline.
Replace `PROJECT` with the resolved project name:

```bash
MEMORY_ROOT="${LLM_MEMORY_HOME:-$HOME/.llm-memory}"
PROJECT="PROJECT"
LOCK="$MEMORY_ROOT/runtime/locks/narrative/$PROJECT.lock"
mkdir -p "$(dirname "$LOCK")"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "LLM_MEMORY_WARN: narrative update already running for $PROJECT; retry after it finishes"
  exit 1
fi
export LLM_MEMORY_NARRATIVE_LOCK="$PROJECT:9"
# ... every merger.py / renderer.py / resolver call for this project goes here,
# in this same shell, while fd 9 is held.
```

`exec 9>` leaves the descriptor without close-on-exec, so child processes
inherit it and `inherited_lock` can fstat it. Do not wrap the steps in a
separate `bash script.sh` invocation unless that script also inherits fd 9 --
the descriptor, not the variable, is what grants the right to proceed.

The single outer lock is what makes the whole read/extract/merge/render
sequence indivisible rather than locking only each individual write.

A `delta-extractor` Agent call cannot run inside that shell. Extraction does
not mutate project state, so run the agent first, then take the lock for the
merge/render/resolve steps.

## Step 1: Discover work

Call `narrative_coverage(project=PROJECT)` for the current project first, then
for any other projects surfaced by the session_start hook. `narrative_coverage`
computes unprocessed transcripts by diffing on-disk main-session transcripts
against `{project}.json.sessions[]` (i.e. sessions already merged).

To enumerate all projects with session activity:

```bash
MEMORY_ROOT="${LLM_MEMORY_HOME:-$HOME/.llm-memory}"
python3 -c "
import sys; sys.path.insert(0, '$MEMORY_ROOT/lib')
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

- Write the empty stub to `$MEMORY_ROOT/projects/<project>.json`:

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

Use `unprocessed_sorted` from `narrative_coverage` for both bootstrap and
incremental runs. It already contains only qualifying main-session
transcripts, sorted chronologically. Each entry is `{ "path", "timestamp" }`:
`timestamp` is the first JSONL record's timestamp, with file mtime as its
fallback. Use `path` as the transcript and `timestamp` as `ISO8601_START`;
do not re-read timestamps or re-sort them in Bash. The legacy `unprocessed`
field remains a path-only compatibility field for existing consumers.

If a session's `$MEMORY_ROOT/conversations/<session_id>.md` is missing,
**generate it rather than dropping the session**. The sweep in
`session_start.sh` is lazy — it only runs at session start against files newer
than its sentinel — so a session that ended after the last session start has
not been archived yet, and silently skipping it loses the work permanently:

```bash
# Find the live transcript (it may not be in the archive yet) and strip it.
MEMORY_ROOT="${LLM_MEMORY_HOME:-$HOME/.llm-memory}"
SRC=$(find "$HOME/.claude/projects" -maxdepth 2 -name 'SESSION_ID.jsonl' | head -1)
cp -n "$SRC" "$MEMORY_ROOT/transcripts/SESSION_ID.jsonl"
python3 "$MEMORY_ROOT/lib/extract_conversation.py" "$SRC" \
  --output "$MEMORY_ROOT/conversations/SESSION_ID.md" --force
```

Only drop a session when no transcript exists in either location, and say so
in the Step 3 summary — a silently skipped session looks identical to one
that had nothing to say.

### 2c. For each main-session transcript, in order

0. **Re-check that this session is still unprocessed**, immediately before
   spending anything on it. The coverage snapshot from Step 1 ages: a run over
   several projects can be tens of minutes old by the time it reaches the last
   one, and nothing in this skill holds a lock across that gap (the extract
   step cannot run under the project lock, by design — see the note above the
   `flock` recipe).

   Substitute **all three** placeholders — `PROJECT_NAME`, `SESSION_ID` and
   `CATEGORY`. A literal `PROJECT_NAME` left in place is merely a missing file,
   which answers `still_unprocessed` and tells you nothing.
   `CATEGORY` is the literal word `stale` for
   a session from the `stale` list, or `fresh` for one from
   `unprocessed_sorted` — it is not optional and there is no default, because a
   stale session guessed as fresh is reported `already_merged` and silently
   loses its `--rerun`. `SESSION_ID` must be the `session_id` exactly as Step 1
   produced it; a shortened display prefix matches nothing and is
   indistinguishable from a session that was never merged.

   ```bash
   MEMORY_ROOT="${LLM_MEMORY_HOME:-$HOME/.llm-memory}"
   python3 - "$MEMORY_ROOT/projects/PROJECT_NAME.json" SESSION_ID CATEGORY <<'PY'
   import json, sys
   if len(sys.argv) != 4 or sys.argv[3] not in ("stale", "fresh"):
       # Never guess a category. Guessing "fresh" for a stale session answers
       # already_merged with full confidence and drops its --rerun.
       print("bad_invocation")
       raise SystemExit
   state_path, sid, category = sys.argv[1], sys.argv[2], sys.argv[3]
   try:
       with open(state_path) as fh:
           sessions = json.load(fh).get("sessions")
       if sessions is None or sessions == []:
           merged = set()                  # nothing merged yet; proceed
       elif not isinstance(sessions, list):
           raise TypeError("sessions is not a list")
       else:
           # Count a row only on a non-empty string id. Anything else -- a
           # non-dict, a missing or null id, a blank one, or an unhashable one
           # such as a list -- falls out of the set rather than aborting the
           # project, so one bad row cannot hide every valid id beside it.
           merged = {s["session_id"] for s in sessions
                     if isinstance(s, dict)
                     and isinstance(s.get("session_id"), str)
                     and s["session_id"]}
           # But a non-empty list that yielded no id at all is not an empty
           # membership set -- it is a file we failed to understand.
           if not merged:
               raise TypeError("sessions has no readable session_id")
   except FileNotFoundError:
       # A project that has never merged has no state file. Skipping here would
       # drop its first run entirely, so absence must mean "proceed".
       print("still_unprocessed")
   except (OSError, ValueError, TypeError, AttributeError):
       # Torn, unreadable or unexpectedly shaped state is not evidence either
       # way, and must not be answered with a confident token.
       print("state_unreadable")
   else:
       # A stale session IS in sessions[] -- that is what its earlier merge
       # recorded. Membership therefore cannot decide it, and it is exactly the
       # case that must be re-extracted and merged with --rerun.
       print("still_unprocessed" if category == "stale"
             else ("already_merged" if sid in merged else "still_unprocessed"))
   PY
   ```

   The printed token is the whole decision; do not re-apply the reasoning above
   it by hand:

   - `still_unprocessed` → proceed to step 1.
   - `already_merged` → **skip this session entirely.** Another run merged it
     after your snapshot. Do not extract, do not merge, and say so in the Step
     3 summary.
   - `state_unreadable` → **stop processing this project** and report it in the
     Step 3 summary. Do not extract against a state file you could not read.
   - `bad_invocation` → you substituted the placeholders wrongly. Fix the call;
     do not proceed and do not guess a category.

   Why this is step 0 and not a later guard: `merger.py` already refuses a
   delta whose session is in `sessions[]`, but it refuses *after* the extractor
   has been paid for. Checking the merge verb (step 3) makes that loss visible;
   only checking first makes it free.

   **What this closes, and what it does not.** For a `fresh` session it closes
   the window where the other run has already merged by the time you reach this
   one — the common shape in a multi-project run, whose Step 1 snapshot can be
   tens of minutes old by the last project. It does **not** close two runs
   aligned on the same session: if both read `sessions[]` before either merges,
   both see `still_unprocessed` and both pay. Extraction is the slow part, so
   that is the likely interleaving for two runs started together.

   It closes nothing at all for a `stale` session. That branch never reads
   membership — it cannot, since membership is what being stale implies — so it
   cannot tell that another run has already finished the `--rerun`. Two runs
   re-merging the same stale session both pay, whatever their timing.

   Membership is not a lease. Nothing between this read and the merge records
   "I am extracting this session," and the extract step cannot sit inside the
   project lock. Closing the aligned case needs a claim written under the lock,
   released before the extractor runs, and cleared at merge. That does not
   exist today; this check is not a substitute for it.

   Observed 2026-09-22: two `/narrative` runs on one machine both took a
   coverage snapshot, both saw utilityswitch `a09a564e` as unprocessed, and
   both extracted it. One delta was refused at merge with `already in
   sessions[] -- delta NOT applied`, after the spend.

1. **Check the delta cache** before spawning an agent:

   ```bash
   MEMORY_ROOT="${LLM_MEMORY_HOME:-$HOME/.llm-memory}"
   python3 "$MEMORY_ROOT/lib/delta_cache.py" check SESSION_ID ISO8601_START
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
   conversation_md_path: ${MEMORY_ROOT}/conversations/SESSION_ID.md
   project_state_path:   ${MEMORY_ROOT}/projects/PROJECT_NAME.json
   session_id:           SESSION_ID
   session_started_at:   ISO8601_START
   session_ended_at:     ISO8601_END
   output_path:          ${MEMORY_ROOT}/deltas/SESSION_ID.delta.json
   contested_path:       ${MEMORY_ROOT}/projects/PROJECT_NAME.contested.json

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
   MEMORY_ROOT="${LLM_MEMORY_HOME:-$HOME/.llm-memory}"
   python3 "$MEMORY_ROOT/lib/delta_cache.py" stamp \
     "$MEMORY_ROOT/deltas/SESSION_ID.delta.json"
   ```

3. **Run the merger** on whichever delta is now on disk (cached or fresh):

   ```bash
   MEMORY_ROOT="${LLM_MEMORY_HOME:-$HOME/.llm-memory}"
   python3 "$MEMORY_ROOT/lib/merger.py" \
     "$MEMORY_ROOT/projects/PROJECT_NAME.json" \
     "$MEMORY_ROOT/deltas/SESSION_ID.delta.json"
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
   exists in that Bash call:
   `MEMORY_ROOT="${LLM_MEMORY_HOME:-$HOME/.llm-memory}"; mkdir -p "$MEMORY_ROOT/deltas"`.
   The merger is idempotent
   per session_id; the cache is idempotent by `extractor_hash`. Leftover
   delta files are a feature, not debt — they act as the pre-processed
   cache so repeat runs skip LLM calls.

5. **If the delta-extractor's Write to `$MEMORY_ROOT/deltas/...` fails
   or prompts repeatedly,** that's a missing permission, not a sandbox
   block. Do NOT reroute through `/tmp/` and `mv` — that just multiplies
   the prompts. Tell the user to add `Write(<resolved-memory-root>/**)`
   to `~/.claude/settings.json`'s `permissions.allow` (it ships in
   `settings.yaml` as of the post-2026-05-11 install; older installs need
   to re-run `install.sh` or add it manually) and stop.

### 2d. Render after every merge

```bash
MEMORY_ROOT="${LLM_MEMORY_HOME:-$HOME/.llm-memory}"
python3 "$MEMORY_ROOT/lib/renderer.py" \
  "$MEMORY_ROOT/projects/PROJECT_NAME.json" \
  "$MEMORY_ROOT/projects/PROJECT_NAME.narrative.md"
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
hooks read it directly.

### 2e. Drain the cascade review backlog (last step, after the final render)

Run this once per project, **after** the last `renderer.py` call for that
project — including when the renderer exits **2**. Exit 2 means "artifacts
written, integrity work remains", not a failed render, so treat it as continue,
not abort:

```bash
MEMORY_ROOT="${LLM_MEMORY_HOME:-$HOME/.llm-memory}"
python3 "$MEMORY_ROOT/lib/tools/resolve_cascade_reviews.py" \
  "$MEMORY_ROOT/projects/PROJECT_NAME.json"
```

Why this step is not optional. A fuzzy claim match (U2/U3/U4) never archives
anything by itself — the merger opens a `cascade_reviews` row and stops. This
resolver is the only route from that row to a decision, so without it the
backlog grows forever and the renderer's review-backlog footer counts up with
nothing draining it.

It never writes `{project}.json`. It emits a review delta and hands it to
`merger.py --rerun` against the most recent real session, so the change lands
through the one audited write path and shows up in that session's
`ledger_delta_applied.resolutions` under `cascade_confirm` / `cascade_reject` /
`cascaded` / `cascade_invalidated`. **If it reports any confirms or rejects,
re-run 2d** — state changed, so the narrative and the certificate sidecar are
now one merge behind.

`no open reviews; nothing to resolve.` is the normal, common output. Nothing
further to do for that project.

Two variants, for when the default local-model judgment is not what you want:

- `--emit-prompts` prints the open reviews and their confirmation prompts as
  JSON and stops, resolving nothing. Use it when you want to answer the
  judgments yourself rather than delegate them, then feed your answers back as
  `--decisions FILE`, a JSON list of `{"child": ID, "parent": ID, "decision":
  "confirm"|"reject", "reason": TEXT}`.
- `--dry-run` builds the delta and prints it without merging.

An open review that nobody answers **rejects** — a row with no decision must
never be read as a confirmation. Note the asymmetry before overriding anything:
confirming archives an item permanently, and rejecting blocks that exact pair
permanently. Neither has an undo, so `--emit-prompts` is the right reflex
whenever a pair looks contestable.

To resolve a single review by hand:

```bash
MEMORY_ROOT="${LLM_MEMORY_HOME:-$HOME/.llm-memory}"
python3 "$MEMORY_ROOT/lib/tools/cascade_review.py" list "$MEMORY_ROOT/projects/PROJECT_NAME.json"
python3 "$MEMORY_ROOT/lib/tools/cascade_review.py" confirm "$MEMORY_ROOT/projects/PROJECT_NAME.json" \
  --child work-abcd1234 --parent dec-abcd1234 --reason "restates the same claim"
```

Do not hand-edit `cascade_reviews` in the JSON. That bypasses the fingerprint
staleness check, which is the only thing standing between a claim that moved
since the review was proposed and a terminal archive.

## Step 3: Summary

When all projects finish, emit a compact summary:

```
Narrative update complete:
  - PROJECT_A: N session(s) processed -> $MEMORY_ROOT/projects/PROJECT_A.narrative.md
  - PROJECT_B: M session(s) processed -> $MEMORY_ROOT/projects/PROJECT_B.narrative.md
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
