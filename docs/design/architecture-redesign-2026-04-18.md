# Architecture Redesign — 2026-04-18

## Problem Statement

The current structured pipeline (delta-extractor → merger → renderer → narrative memory record)
produces narratives that are unconsumable at session start. The `llm_memory` project's freshly
rendered narrative is 154KB / ~55K tokens — roughly 25% of a 200K context window for one
project. Target is 5-10K tokens per narrative so multiple projects can coexist in session_start
injection without dominating context.

Direct measurements from the 23-session `llm_memory.json` state at time of this doc:

| Section | Size | Cause |
|---|---|---|
| Approach | 45KB | 62 active decisions, 0% pruning — only 14/76 archived, all via explicit `contradictions` |
| What We've Learnt | 52KB | 83 active learnings, 0 archived ever in 23 sessions |
| What's Done | 26KB | 186 active done items, 50 tagged `load_bearing` (spec wanted 10-20%) |
| Resuming | 10KB | Bounded by design: journal + last-200-lines of conversation.md |

Importance grading is badly miscalibrated: 56% of active decisions are `load_bearing` vs the
spec's 10-20% target. Since the renderer always renders `load_bearing` items in full, loose
grading cascades into bloat.

Every root cause traces to the same thing: **the merger accumulates, nothing prunes.** Rule 4
in the delta-extractor spec mandates a closure sweep but only for `goals[]` and `suggestions[]`.
Decisions, learnings, and done have no corresponding sweep, so items introduced in session 3
stay active forever even when the approach has changed three times since.

## Target Architecture

### Data model

- **`{project}.json`** — per-project structured state, source of truth. Decisions, learnings,
  done, goals, suggestions, operations, summary, sessions. Notes (currently a separate memory
  type) get absorbed as structured ledger entries.
- **Per-item files** at `~/.claude/memory/items/{project}/{kind}/{item-uuid}.json` — derived
  from the JSON, written as the Syncthing substrate. Unique UUID filenames are conflict-safe
  for additive changes. `{project}.json` is derived locally from these files on each machine;
  the JSON itself is not synced.
- **`{project}.narrative.md`** — rendered cache loaded at session_start. Not synced.
- **memory.db** — FTS5 index, rebuilt from per-item files on change. Powers
  `project_lookup()`. Not synced.

### Write flow (JSON-as-truth with fan-out to files)

1. Delta-extractor (LLM) produces `delta.json` from one session's transcript + current
   `{project}.json`.
2. Merger reads `{project}.json`, applies pending incoming file changes (inbox merge),
   applies the delta, writes `{project}.json` atomically.
3. Merger fans out changed items to per-item files (create / update / archive).
4. Renderer regenerates `{project}.narrative.md`.
5. Indexer updates memory.db with changed items.

### Sync flow

1. Syncthing propagates per-item file changes between machines.
2. On each machine, the next read of `{project}.json` triggers an inbox merge:
   reconcile incoming file changes against current JSON, write updated JSON.
3. Syncthing conflicts (`*.sync-conflict-*.json`) are swept automatically by a resolver:
   identical content → drop duplicate; divergent archived state → later wins; divergent
   text → keep later `updated_at`, preserve other in `history[]`.

### Identifier strategy

- Item IDs are **UUID-suffixed**: `dec-a8c3b...`, `lrn-19f7c...`. No monotonic counters —
  concurrent machines can introduce items without collision.
- Display can still show ordinal numbers (`dec-1`, `dec-2`) by sorting on `created_at`
  at render time. Cross-item refs (contradictions, touches) use UUIDs internally.

### Closure & pruning

- **Rule 4 extended.** Mandatory closure sweep covers `decisions[]`, `learnings[]`, and
  `done[]` in addition to `goals[]` and `suggestions[]`. For each active item: is it still
  the current state? Is it design-shaping or was it plumbing? Archive with reason if not.
- **Statistical decay, not counters.** Each item carries `last_touched_at` (ISO
  timestamp), not `cycles_pending` (integer). Relevance score derived at render / sweep
  time:

  ```
  score = importance_weight * exp(-age_in_days / half_life)
  ```

  with `half_life ≈ 30 days` (tunable), `importance_weight` = {load_bearing: 3.0,
  standard: 1.0, minor: 0.3}. Score drives rendering (top-N by score) and stale-sweep
  (items below a percentile).

- **Rendering filter — three tests, all three must hold:**
  1. **Design-shaping** — does this item shape how the product thinks/behaves, or is it
     maintenance that keeps it running? Plumbing fixes dissolve after N sessions regardless
     of importance tag.
  2. **Non-obvious** — would a future Claude re-derive this from reading the current code?
     If yes, don't render it.
  3. **Current** — does this still match the state of the codebase today?

### Narrative vs Resume split

- **Narrative** is dense reference: Idea, Approach (top-N current load-bearing decisions),
  Operations, Current State, open Goals/Suggestions, pointer to `project_lookup()` for
  drill-down. Loaded at every session_start.
- **Resume** is demand-loaded. A tool like `resume(project)` returns the last session's
  journal + tail-200-lines of its conversation.md. Session_start injects only a pointer
  ("last session ended 2h ago, closure_status: complete — call `resume()` to pick up").
  Agents never see it unless they ask.

### Reorganization of memory types

Notes, narratives-as-records, and session_logs collapse into the project JSON or rendered
files:

- **Notes** are project-scoped structured data under a looser schema. All 80 active notes
  audited: zero cross-project. Absorb as `decisions[]` / `learnings[]` / `done[]` entries
  with appropriate tags and importance.
- **Narratives** are rendered files, not DB records. session_start reads
  `{project}.narrative.md` directly instead of `SELECT content FROM memories WHERE
  type='narrative'`.
- **Session logs** already exist richer in `{project}.json` `sessions[]` array. Drop the
  DB copy.
- memory.db retains two jobs: (1) FTS5 index over item files for `project_lookup()`, and
  (2) rebuildable from items/ on startup or corruption.

### Retention (deferred)

Old archived items must eventually hard-delete from disk to keep per-item file counts
bounded. Policy TBD — will figure this out later.

## Downsides Considered & Accepted

- **Sync conflicts on project JSON** — mitigated by JSON not being synced; files are.
- **Schema evolution cost** — must migrate N project JSONs instead of one ALTER TABLE. Cheap
  since schemas evolve infrequently.
- **Full-file JSON rewrites on every change** — milliseconds; not a concern until projects
  hit thousands of sessions.
- **Cross-item atomicity during fan-out** — mitigated by writing the JSON first (atomic),
  then fanning out files. Recovery just re-fans from the JSON.
- **Retention of archived files** — deferred.

## Rationale: Why This Over Alternatives

**Why not stick with freeform LLM narratives from raw transcripts?** That approach silently
dropped items every regeneration — operations details, decisions from older sessions, work
not-yet-glamorous. Already documented as a project learning ("Freeform narrative updates
drop operational details"). Structured pipeline was built to stop silent loss. Keep it.

**Why not full event-sourcing (deltas as permanent sync substrate)?** Cleaner conflict
behaviour (deltas never mutate, never conflict) but adds replay cost to every read and a
third derivation layer. Per-item files are the middle ground: conflict-free for additive
changes, direct reads from the JSON, one derivation layer. Acceptable trade-off.

**Why statistics instead of integer cycles?** `cycles_pending` doesn't capture real time
(20 sessions in a day ≠ 20 sessions over six months) and hardcoded thresholds don't adapt
to project size. Score-based ranking self-adjusts.

## Implementation Plan

Ordered by impact-per-effort. Each phase leaves the system in a working state.

### Phase 1 — Extend closure sweep (days, unblocks current bloat)

Target: shrink rendered narrative from 55K to ~10K tokens without data-model changes.

- [ ] Extend Rule 4 in `agents/delta-extractor.md` to mandate closure sweeps over
      `decisions[]`, `learnings[]`, and `done[]` in addition to goals/suggestions.
- [ ] Update prompt with the **design-shaping / non-obvious / current** three-part filter.
- [ ] Tighten `load_bearing` grading language in Rule 6 — add explicit negative examples
      and a reminder that >20% load_bearing means over-grading.
- [ ] Merger already honours archives across all ledger kinds (verified at
      `lib/merger.py:151`). No merger changes needed in this phase.
- [ ] One-off **audit agent** — reads current `{project}.json`, proposes archives for the
      62 active decisions and 83 active learnings that are no longer design-shaping /
      non-obvious / current. Human approves, merger applies.

### Phase 2 — Statistical decay + rendering filter (days)

- [ ] Add `last_touched_at` ISO timestamp to every item (migration: set from
      `last_touched_in` session's `started` timestamp).
- [ ] Merger: on every delta, auto-update `last_touched_at` for any item explicitly
      referenced (by ID in resolutions) OR fuzzy-matched by text (probably later).
- [ ] Renderer: compute decay score per item, render top-N by score per section.
      `load_bearing` items always render regardless of score; `standard` dissolve below
      threshold; `minor` never render individually (summarised).
- [ ] Remove `cycles_pending` and `STALE_CYCLES_THRESHOLD` in renderer.py; replace with
      score-based stale callout.

### Phase 3 — Resume demand-loading (days)

- [ ] New MCP tool `resume(project)` returns last session's journal + tail-N-lines of its
      `conversation.md`.
- [ ] Renderer strips the embedded `## Resuming` section; leaves a one-line pointer
      instead.
- [ ] `subagent_start.sh` does NOT inject narrative (agents don't need it). Only
      `session_start.sh` does.

### Phase 4 — Fuzzy lookup (week)

- [ ] New MCP tool `project_lookup(project, query, kind=None, status=None, limit=10)`.
      Runs BM25 / FTS5 over ledger items with optional filters.
- [ ] Update session_start hook output to mention `project_lookup()` is available so
      future-Claude knows to call it instead of scanning the JSON.

### Phase 5 — Per-item files + JSON-as-truth sync (weeks)

- [ ] Migrate item IDs to UUID suffixes.
- [ ] Item file layout: `~/.claude/memory/items/{project}/{kind}/{id}.json`.
- [ ] Merger fan-out: after writing `{project}.json`, diff prior items vs new, write
      created/updated/archived per-item files.
- [ ] Inbox-merge step: on read of `{project}.json`, check for per-item file changes
      not reflected in JSON (compare per-file `updated_at` vs JSON's
      last_rebuilt_at); reconcile.
- [ ] Syncthing config: sync `items/`, exclude `projects/` (the JSON) and
      `memory.db`.
- [ ] Conflict-sweeper: resolver for `*.sync-conflict-*.json` files with merge rules.

### Phase 6 — Memory-type cleanup (week)

- [ ] Migrate all 80 note records into the appropriate project JSONs as `decisions[]` /
      `learnings[]` / `done[]` entries.
- [ ] Retire `note` and `session_log` types from memory.db.
- [ ] Retire `narrative` memory type — session_start reads
      `{project}.narrative.md` directly.
- [ ] memory.db schema simplifies to: one FTS5 table over ledger items with columns
      `(item_id, project, kind, text, rationale, quote, tags, status, last_touched_at)`.

### Phase 7 — Retention policy

- TBD. Figure out later.

## Open Questions

- Half-life value for decay (30 days? 60 days? session-count-based instead of time-based?)
- Threshold for rendering filter (top-N items? score percentile? size budget?)
- `project_lookup` as new MCP tool vs Python reader via Bash — decide when we get there.

## Status

Design captured. Phase 1 is the next implementation step (tracked as TaskCreate #6).
