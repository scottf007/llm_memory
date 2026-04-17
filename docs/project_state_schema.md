# Project State Schema (proposal, draft v0.1)

The `{project}.json` file is the source of truth for a project's history,
decisions, and ongoing work. The narrative record stored in the memory DB
becomes a *rendered view* of this file — the render is deterministic,
so overclaims and silent drops become impossible by construction.

Lives at: `~/.claude/memory/projects/{project}.json`

Syncthing-carried like other `~/.claude/memory/` contents. SQLite remains
a derived index.

---

## Top-level structure

```json
{
  "schema_version": "0.1",
  "project": "llm_memory",
  "last_updated": "2026-04-17T00:29:57Z",

  "summary":     { ... },
  "operations":  [ ... ],
  "decisions":   [ ... ],
  "goals":       [ ... ],
  "suggestions": [ ... ],
  "learnings":   [ ... ],
  "done":        [ ... ],
  "sessions":    [ ... ],
  "narrative":   { ... }
}
```

Six ledger arrays + summary/operations blocks + sessions + narrative
metadata. All items have short stable IDs so cross-references are
readable.

---

## `summary` — stable project identity

Authored once, mutates only on a genuine pivot. Always rendered verbatim
into "The Idea" at the top of the narrative.

```json
"summary": {
  "what": "A persistent memory system for Claude Code so context survives across sessions and projects.",
  "why":  "Give future Claude sessions awareness of past work, decisions, and ongoing goals without manual rehydration.",
  "stack": "Python + SQLite + FTS5 + FastAPI + MCP server + lifecycle hooks",
  "scope": "Fully local, no cloud, no API keys"
}
```

## `operations` — how to actually use/run the thing

Exempt from archiving. Always rendered in full. Session 17's Operations
section, structured.

```json
"operations": [
  { "item": "Repo",           "detail": "github.com/scottf007/llm_memory (public)" },
  { "item": "Dev env",        "detail": "WSL2 on desktop + laptop SCOTT-XPS, Python 3.12" },
  { "item": "MCP server",     "detail": "Claude Code subprocess via stdio, configured in ~/.claude/settings.json under mcpServers.llm_memory" },
  { "item": "Dashboard",      "detail": "FastAPI on :8765, `python dashboard.py`" },
  { "item": "Data — records", "detail": "~/.claude/memory/records/ (JSON, source of truth)" },
  ...
]
```

---

## Common item fields (shared across decisions / goals / suggestions / learnings / done)

Every ledger item has:

| Field | Type | Notes |
|---|---|---|
| `id` | string | Short stable — `dec-001`, `goal-007`, `sug-042`, `lrn-015`, `work-033` |
| `text` | string | One-sentence core statement |
| `status` | enum | `active` or `archived` |
| `importance` | enum | `load_bearing`, `standard`, `minor`. Default `standard`. Only `decisions` and `done` actively use it; other item types can carry it but most won't. Used by the renderer to distinguish the load-bearing spine from detail. |
| `introduced_in` | session_id | Which session introduced this item |
| `last_touched_in` | session_id | Most recent session that affirmed / modified |
| `archived_in` | session_id / null | When it moved to archived (null while active) |
| `archived_reason` | string / null | Why — e.g. "superseded by dec-004", "Scott abandoned", "fixed in code" |

Only `active` items render into the narrative. `archived` items stay in
the JSON forever, queryable.

---

## Categorization rules — what goes where

- `decisions[]` is for **architectural choices only** — how the system is shaped, what approaches were picked, what was reversed. Not convention notes, not install details, not doc updates.
- `operations[]` (the top-level block) is for running-state facts — hosts, ports, paths, deploy commands. If a "decision" is really an operational detail ("dashboard runs on port 8765"), it belongs in `operations[]`, not `decisions[]`.
- `done[]` is for shipped concrete work — specific commits, specific files written, specific features landed. Not for policies or conventions.
- Conventions (e.g. "don't add Co-Authored-By to commits", "README documents prereqs") go in `done[]` as the action taken, optionally with `importance: "minor"`. They're not architecture.
- A true `load_bearing` decision has: Scott quote driving it, OR an architectural shift / reversal of a prior decision, OR something a future Claude needs to understand the system at a glance. There should be ~10-15 per mature project, not 60+.

---

## `decisions` — architectural/approach choices with rationale

```json
{
  "id": "dec-002",
  "text": "JSONL transcripts are source of truth",
  "rationale": "Compaction only affects context window; on-disk JSONL keeps everything",
  "quote": "I didnt want you to do it from chunks, as they miss things",
  "status": "active",
  "introduced_in": "1acd03b0-54eb-494d-9bcb-18d19906105f",
  "last_touched_in": "11ac820c-8f93-4fb5-ba8f-36d7ebe93468",
  "archived_in": null,
  "archived_reason": null,
  "supersedes": ["dec-003"]
}
```

Decisions that get reversed don't get deleted — they go `status: archived`,
`archived_reason: "superseded by dec-XXX"`, and the new decision records
them in its `supersedes` array. Rationale: the *why behind the current
decision* often lives in the story of what was tried before.

Renders into narrative's "Approach" table when `status=active`.

---

## `goals` — things Scott (or Claude-with-Scott's-acceptance) wants done

```json
{
  "id": "goal-028",
  "text": "Build JSONL dialogue extractor as preprocessing step",
  "status": "active",
  "introduced_in": "11ac820c-...",
  "progress": "extract_conversation.py written, wired into process_transcripts.py + session_end.sh; narrative-updater agent reads .md; 20 llm_memory transcripts extracted and audited (4 deep + 2 re-audits post-fix)",
  "linked_suggestion": "sug-023",
  "blocks": [],
  "blocked_by": []
}
```

Progress is a prose field — captures where the item actually is, not
just its title. Updates on each touch.

Renders into narrative's "What We Want To Do" when `status=active`.
Moves to `done[]` when completed (preserve id as cross-reference).

---

## `suggestions` — Claude's proposals

```json
{
  "id": "sug-007",
  "text": "Fix bash `!=` escaping in /narrative skill's sqlite3 query",
  "originator": "claude",
  "status": "archived",
  "introduced_in": "2f8ae111-...",
  "flagged_in_learnings": "lrn-018",
  "cycles_pending": 4,
  "archived_in": "b8cb0d09-...",
  "archived_reason": "Commit bfd069f changed != to <>. Example of documented ≠ fixed — sat in narrative for 7 days while firing every run."
}
```

`cycles_pending` = how many session_ends this item survived while still
`active` and not newly introduced. Starts at 0 on introduction; the
merger increments at session_end for every active item that wasn't
introduced or resolved that session. Applies to both **suggestions**
and **goals** — anything that can stay open pending action. When
`cycles_pending >= 5`, the renderer surfaces a stale callout in the
rendered narrative prompting a decide-or-archive review.

This is the mechanism that covers the three stale-item categories the
narrative audit found: (1) still valid but dragging → keep; (2) obsolete
/ subject no longer exists → archive; (3) already done elsewhere →
archive. The callout brings them to attention; a human (or a future
agent) makes the call.

Renders into narrative's "Suggested Work" when `status=active`.
Archived on acceptance (moves to a decision/goal), rejection, or
when Scott acts past it.

---

## `learnings` — gotchas captured

```json
{
  "id": "lrn-042",
  "text": "Documented ≠ fixed: flagging an item in What We've Learnt doesn't prevent recurrence",
  "status": "active",
  "introduced_in": "b8cb0d09-...",
  "evidence": ["sug-007 fired in sessions 12, 14, 15, 16 while sitting in narrative"],
  "still_relevant_because": "Structural — Claude reads the narrative but doesn't auto-enforce lessons",
  "fixed_in_code": false
}
```

Learnings can get archived, but cautiously. `fixed_in_code: true` +
`can_recur: false` is the bar. Most learnings stay active — they're
cheap to keep and load-bearing for future sessions.

---

## `done` — shipped work

```json
{
  "id": "work-033",
  "text": "`narrative_coverage` MCP tool (8th tool) — diffs on-disk transcripts against narrative's transcript_ref",
  "status": "active",
  "completed_in": "c2f2634e-...",
  "related_decisions": ["dec-015", "dec-016"],
  "commit": "8285525"
}
```

`status: active` here just means "still part of the project, not
ripped out." If work gets removed, flip to `archived` with reason.

Renders into narrative's "What's Done" — but summarised, not listed
in full. The list is long; the narrative gets a prose roll-up plus a
pointer to the JSON.

---

## `sessions` — per-session records (the journal + ledger delta + resume excerpt)

```json
{
  "session_id": "13dad5a6-855c-4e0b-a3c7-46028dc4f138",
  "started": "2026-03-08T00:37:22Z",
  "ended":   "2026-03-08T01:46:00Z",
  "topic": "Chunking design lands",
  "closure_status": "complete",  // or "interrupted"
  "jsonl":            "~/.claude/memory/transcripts/13dad5a6.jsonl",
  "conversation_md":  "~/.claude/memory/conversations/13dad5a6.md",
  "status": "active",
  "journal": "Scott opens by asking Claude to read a handoff doc...(full prose, multi-paragraph)",

  "resume_excerpt": "=== user ===\n...\n=== assistant ===\n...\n(last ~50-200 lines of conversation_md, verbatim, only present on the most recent still-active session)",

  "ledger_delta": {
    "introduced": {
      "decisions":   ["dec-003", "dec-004"],
      "goals":       ["goal-002"],
      "suggestions": ["sug-001", "sug-002"],
      "learnings":   ["lrn-001"],
      "done":        ["work-001"]
    },
    "resolutions": {
      "closed":         [{"id": "goal-001", "evidence": "Tests green, commit dc1ba94"}],
      "archived":       [],
      "rejected":       [],
      "contradictions": [],
      "drift":          []
    }
  }
}
```

One session record per processed transcript.

**`closure_status`** is set by the delta-extractor at capture time based on where the conversation landed at session close. `complete` = natural resting point (work finished, Scott signed off cleanly, no mid-thread cut). `interrupted` = work mid-thread, unanswered question, explicit "come back to this" language, or session ended with an unresolved goal/suggestion still in flight. When unsure, default to `interrupted` — it's the safer signal for resumption. Renderer uses this for the Resuming section's Status line.

**`journal`** — prose connecting this session to prior work.

**`resume_excerpt`** — the last ~50-200 lines of `conversation_md`,
verbatim. Only populated on the *most recent still-active session*
(pruned from older sessions so the JSON doesn't bloat). Regenerated at
each render from the current tail of that session's conversation.md.
This is what loads into "Resuming" so Scott picks up mid-flow instead
of reading a 2-sentence summary.

**`ledger_delta`** — structured change-set for this session.

Sessions can be `status: archived` once all their unique content is
absorbed into active ledger items, *but the journal prose stays in the
JSON*. Archiving a session just means it doesn't render in narrative's
"Source Transcripts"; drill-down is still possible.

---

## `narrative` — render metadata

```json
"narrative": {
  "rendered_at": "2026-04-17T00:29:57Z",
  "record_uuid": "e0014379a7a188b3464add6bcc7ebe7b",
  "rendered_from_schema_version": "0.1",
  "drift_audit": {
    "last_run": "2026-04-17T00:35:00Z",
    "overclaims": [],
    "lost_items": [],
    "notes": ""
  }
}
```

Tracks the most recent render. The `record_uuid` links to the
`type=narrative` record in the memory DB (so existing MCP tools keep
working). `drift_audit` captures the results of the audit — empty when
the render is clean.

---

## Render strategy (query → 8-section narrative)

The renderer is deterministic code, not an LLM. Given `{project}.json`:

| Narrative section | Query |
|---|---|
| The Idea | `summary` block, verbatim |
| Approach | `decisions` where `status=active`, table form |
| Operations | `operations` block, verbatim (full) |
| What's Done | `done` where `status=active`, prose roll-up + pointer |
| What We've Learnt | `learnings` where `status=active`, bullet form, flag `lrn` entries with recurring evidence |
| What We Want To Do | `goals` where `status=active`, ordered by priority/introduced_in |
| Suggested Work | `suggestions` where `status=active`; surface `cycles_pending >= 3` with warning |
| Resuming | last active session: (a) status flag, (b) `journal` excerpt, (c) `resume_excerpt` verbatim — the actual last ~50-200 lines of dialogue so Scott picks up mid-flow |
| Source Transcripts | `sessions` where `status=active`, annotated table (plus one-line pointer for archived sessions: "N older sessions — see JSON") |

No 5K token cap. Render is as long as the active ledger makes it.

---

## Drift audit (code, not LLM)

Run over `{project}.json` + rendered narrative + project codebase to
detect:

1. **Overclaims**: `suggestions[].status=archived` but the archived
   text still appears in rendered Suggested Work. (Shouldn't happen —
   the render is deterministic — but catches schema bugs.)
2. **Suggestions closed by code but not yet flipped to archived**: grep
   the codebase for keywords / commit messages matching an active
   suggestion's text; flag candidates.
3. **Goals claimed done but still active**: same pattern.
4. **Stale suggestions**: `cycles_pending >= 3` but no evidence of
   action in recent commits → flag for user attention.

Results land in `narrative.drift_audit`. Runnable via `/narrative --audit`.

---

## Update flow (per session_end)

1. `session_end.sh` extracts `conversation.md` (already built today).
2. **delta-extractor agent** reads `conversation.md` + current
   `{project}.json`. Emits:
   - New `journal` prose for this session.
   - A `ledger_delta` JSON blob specifying new items introduced and
     existing items closed / archived / rejected / contradicted.
3. **merger** (code): appends the session record, creates new items
   with auto-incremented IDs, updates existing items' `status` and
   `archived_in` / `archived_reason`, increments `cycles_pending` on
   still-active suggestions, writes `{project}.json`.
4. **renderer** (code): produces new narrative markdown from
   `{project}.json`. Stores via `memory_store(type=narrative)` with
   `supersedes` pointing at the prior narrative record.
5. **(optional) drift audit** runs; results noted in `narrative.drift_audit`.

---

## Migration from the current state (per project)

One-time, background:

1. Run the walk-and-ledger process (what we did today for llm_memory)
   across each project's historical sessions. Emit `{project}.json`
   seeded with sessions, decisions, goals, suggestions, learnings,
   done. Set each item's `status` based on current relevance.
2. Populate `summary` from the current narrative's "The Idea" block.
3. Populate `operations` from the current narrative's Operations table.
4. Render narrative from the JSON; compare against current narrative;
   capture drift in `narrative.drift_audit`.
5. Replace the current narrative record with the rendered one.

For llm_memory specifically, we have the raw material (the 19
summaries + journey doc + comparison). Conversion to `{project}.json`
is largely mechanical at this point.

---

## Calibration decisions (locked)

1. **Storage location**: `~/.claude/memory/projects/{project}.json`.
   One file per project, visible at `ls ~/.claude/memory/projects/`.
   Cross-cutting non-project records keep using the existing
   `records/{uuid}.json` pattern. Clean split by intent: `records/` =
   individual memory items, `projects/` = per-project aggregate state.
2. **ID scope**: short bare IDs (`dec-001`, `sug-042`) scoped within
   a project's JSON. No prefix stored. If cross-project references ever
   become a real need, prefix at query time (`llm_memory:dec-001`) —
   no schema change required.
3. **Schema versioning policy**: **lax**. Every JSON stores
   `schema_version`. Code reads-with-defaults for additive changes
   (new fields just appear as `undefined` on older files). Breaking
   changes (renames, restructures) ship a one-shot migration script
   that runs over all project JSONs on next install/session_start.
   Not boxed in — if we change direction later, we migrate.
4. **Cycles counter on learnings**: dropped. Each learning stores
   `introduced_in` (session_id); `sessions[]` is ordered; "how many
   sessions since introduced" is derivable at audit time. No stored
   state.

## Next steps

- **Draft the delta-extractor agent prompt** (separate doc, after this
  schema locks). Input: `conversation.md` + current `{project}.json`.
  Output: `journal` prose + `ledger_delta` JSON. Tested against an
  existing llm_memory session.
- **Draft the renderer** (pure code, no LLM): `{project}.json` → 9-section
  narrative markdown, plus the drift-audit helpers.
- **Build the merger** (code): `ledger_delta` + `{project}.json` →
  updated `{project}.json` with auto-incremented IDs and status flips.
- **Migrate llm_memory first** as the pilot (we already have the walk
  and ledger data from this session).
- **Wire into `session_end.sh`** once the pilot is stable.
