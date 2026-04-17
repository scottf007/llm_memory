# Delta-Extractor Agent — Prompt Design

Takes one session's stripped conversation + the current `{project}.json` and
produces a structured delta that the merger applies back into the JSON.

## Agent definition location

`~/.claude/agents/delta-extractor.md` (installed via `install.sh` like the
other agent definitions, once stable).

## Input contract

The launcher (called by `session_end.sh` or a backfill script) hands the
agent:

- `conversation_md_path`: `~/.claude/memory/conversations/{session_id}.md`
  — the stripped dialogue for the session just completed.
- `project_state_path`: `~/.claude/memory/projects/{project}.json`
  — current state of the project. May not exist yet (seeding first session).
- `session_id`, `session_started_at`, `session_ended_at`.
- `project`: project name.

## Output contract

Writes **one file** to a path the launcher specifies, containing JSON
with this shape:

```json
{
  "session_id": "...",
  "started": "...",
  "ended": "...",
  "topic": "Short 3-6 word session topic",
  "closure_status": "complete",  // or "interrupted"

  "journal": "2-4 paragraphs of prose narrating what happened this session. References prior work by ledger item ID where relevant (e.g. 'continued goal-014'). Quotes Scott verbatim when his words illuminate intent or frustration.",

  "resume_excerpt_lines": 120,

  "summary_delta": {
    "what":  "one-sentence description of what the project IS (optional)",
    "why":   "one-sentence description of why it exists (optional)",
    "stack": "one-line stack line (optional)",
    "scope": "one-line scope / constraint line (optional)"
  },

  "operations_delta": [
    {"item": "Repo",     "detail": "github.com/scottf007/foo (public)"},
    {"item": "Hosting",  "detail": "VPS at fairywren.studio :5043"}
  ],

  "ledger_delta": {
    "introduced": {
      "decisions":   [ { "text": "...", "rationale": "...", "quote": "optional verbatim", "importance": "load_bearing|standard|minor" } ],
      "goals":       [ { "text": "...", "progress": "optional short status" } ],
      "suggestions": [ { "text": "...", "originator": "claude|user" } ],
      "learnings":   [ { "text": "...", "evidence": "optional" } ],
      "done":        [ { "text": "...", "commit": "optional sha or null", "importance": "load_bearing|standard|minor" } ]
    },
    "resolutions": {
      "closed":         [ { "id": "goal-014", "evidence": "short justification from the transcript" } ],
      "archived":       [ { "id": "dec-003", "reason": "superseded by the new decision introduced this session" } ],
      "rejected":       [ { "id": "sug-007", "reason": "Scott's words/summary of why" } ],
      "contradictions": [ { "id": "dec-005", "by_decision_text": "text of new decision that contradicts it" } ],
      "drift":          [ { "note": "session opened on X, ended on Y" } ]
    }
  }
}
```

`summary_delta` and `operations_delta` are both **optional**. Only emit
them when the session actually establishes or refines project identity or
operational facts. Most sessions will omit both.

**Never invents IDs for new items.** The merger assigns `dec-001`, `goal-XX`
etc. based on its monotonic counters. New items are keyed positionally in
the arrays.

**Always uses existing IDs** when referring to prior items (resolutions).

## Core rules the agent must follow

1. **Read-only on the conversation.** Do not write to `conversation.md`.
2. **Read the current `{project}.json` first** to ground yourself in
   existing items. You must know the current decisions/goals/suggestions/
   learnings before you can describe deltas.
3. **Don't re-introduce an existing item.** If the session reaffirmed
   `dec-004`, don't add a new decision with the same content — add it
   to `last_touched_in` via a resolution-style entry, OR do nothing
   (the merger will update `last_touched_in` from the session_id).
4. **Closure check is mandatory and exhaustive.** Before emitting the
   delta, iterate over EVERY active `goal` and `suggestion` in the
   prior `{project}.json`. For each one, ask: does this session's
   conversation show evidence it was completed? If yes → add to
   `resolutions.closed` with a verbatim quote from the conversation
   as evidence. If no → do nothing (leave it active). If unclear →
   leave it active (conservative — don't auto-close ambiguous cases).
   Do not rely on Scott saying "that closes goal-X" — evidence of the
   work landing counts: commits made, files written, tests passing,
   commands run to success, features demonstrated working. More
   generally, be explicit about resolutions: for each prior item that
   changes status, say which one and why, use the exact ID, and never
   drop items silently — if something is truly no longer relevant,
   mark it archived with a reason. The merger will not modify items
   the delta doesn't mention.
5. **Categorize strictly.** `decisions[]` is for architectural choices only — the shape of the system, approaches picked or reversed, non-obvious choices a future Claude needs to understand. Conventions (commit message style, branding), install details (deps, file paths), operations facts (URLs, ports, hosts), and doc/readme updates do NOT belong in `decisions[]` — they belong in `done[]` (often with `importance: "minor"`) or, if running-state, in the top-level `operations` block. Ask: "would a future Claude need this to understand the shape of the system?" If no, it's not a decision. If it's specific work that shipped, it's a done item.
6. **Tag `importance` on decisions and done items.** Default is `standard`. Use `load_bearing` when the item is foundational — a Scott quote drives it, it reverses or shapes the architecture, a future Claude needs it to orient. Use `minor` for conventions, install hygiene, README polish, small fixes. Aim for ~10-20% `load_bearing`, ~60% `standard`, ~20-30% `minor` — if you're tagging most items `load_bearing`, you're grading too loosely.
7. **Preserve Scott's voice.** For decisions and suggestions, include
   his verbatim quote in the `quote` field when available. For
   rejections, include his words as the reason.
8. **Classify originator on suggestions.** If Scott proposed it, mark
   `originator: "user"` — those should probably be goals, not
   suggestions; flag in the journal if you're unsure.
9. **Bootstrap project identity from conversation.** If this is the first session for a project (no prior `{project}.json` data) OR the session explicitly establishes what the project is, emit `summary_delta` with whatever of {what, why, stack, scope} can be grounded in conversation content. For `what`: one sentence on what the project does. For `why`: one sentence on purpose/motivation. For `stack`: languages/frameworks if mentioned. For `scope`: explicit constraints Scott states (e.g. "fully local, no cloud"). Don't invent — only emit fields you can point to text for. Sessions that don't talk about project identity leave `summary_delta` empty or omit it.
10. **Emit `operations_delta` when operational facts surface.** Operational facts = how to access/run/deploy the thing — repo URL, hostnames, ports, deploy commands, credential locations (pointers only, never secrets), dashboards, CI/CD, monitoring. Items should be stable state, not one-time setup actions (`setup_foo.py` ran = done item; "runs on port 5043" = operations item). Dedupe by `item` name — if the same item later changes value, emit the new row with same `item` and the merger will update.
11. **Refinement semantics.** If a later session modifies summary or operations (e.g. stack evolved from Python to Python+Rust), emit the updated value. Merger applies shallow-merge on summary (changed keys overwrite) and upsert-by-item on operations.
12. **Journal is not a summary.** It's a paragraph of narrative that
    reads cleanly when concatenated with prior sessions' journals.
    Short is fine. Reference ledger items by ID.
13. **Output JSON only in the output file.** No wrapping text, no
    commentary. The merger parses it as-is.
14. **Set `closure_status` deliberately.** At session close, read the end of the conversation and decide: was work finished at a natural resting point (`complete`), or was it interrupted (`interrupted`)? Signals for `interrupted`: session ends on an unanswered question, a mid-tool-call, explicit "come back to this" / "let me exit now" / "will pick up later" language, OR the delta itself introduces a goal/suggestion that's still pending at session close. Signals for `complete`: Scott signs off cleanly ("thanks" / "great" / "done" / "exit"), work demonstrably landed, all introduced items either closed in-session or were explicitly parked for another day. When unsure, default to `interrupted` — safer for resumption.

## Process checklist (the agent follows this order)

1. Read `{project}.json` if it exists. Parse current IDs by type.
2. Read the conversation markdown.
3. Identify **new items** surfaced this session:
   - Decisions (non-obvious architectural/approach choices — skip
     things that are obvious from code).
   - User-stated goals (*"I want X done"* style).
   - Claude suggestions (proposals the user didn't explicitly drive).
   - Learnings / gotchas that could bite a future session.
   - Work completed (concrete: files changed, commits made, commands
     run to success).
4. Identify **resolutions** of existing items:
   - Did anything open close? (tests green, commit landed, feature
     shipped.)
   - Did Scott explicitly reject / abandon something?
   - Did a new decision supersede an old one? (link `supersedes`).
   - Did the session drift from its opening topic? Note it.
5. **Exhaustive closure pass**: independent of step 4, iterate over
   every active goal and suggestion from the prior `{project}.json`.
   For each, decide: closed-by-this-session / still-open / unclear.
   Add closures to `resolutions.closed` with evidence quotes. This is
   separate from step 4's drift/rejection/contradiction handling —
   it's a dedicated pass for completion recognition.
6. Draft journal prose (2-4 paragraphs).
7. If this is the most recent session, read the tail ~50-200 lines of
   conversation.md and note `resume_excerpt_lines: <count>` — the
   renderer will extract the actual content at render time.
8. Decide `closure_status` based on the end of the conversation.
9. If the session establishes or refines project identity or operational facts, emit `summary_delta` and/or `operations_delta` accordingly. Omit either field entirely when nothing in the session warrants it.
10. Emit the JSON blob.

## Edge cases

- **First-ever session for a project** (no `{project}.json` yet):
  everything is "introduced"; no resolutions possible. Note
  "first session on record" in the journal.
- **Trivial session** (one turn, "hi" → greeting, no work):
  journal is one sentence; `ledger_delta` can have empty arrays
  across the board. Do NOT invent items.
- **Session continues a compacted parent**: mention continuity in the
  journal; look at the tail of the prior session's `ledger_delta` for
  likely-still-open items.
- **Subagent transcripts**: agent sessions (`agent-*` session_ids)
  usually don't introduce ledger items — their work is delegated by
  and reported back to a parent session. Default to empty
  `ledger_delta` unless there's clear independent work. Journal
  should note "agent session spawned by session-XXX for purpose Y."
- **Work done silently**: the delta-extractor must not wait for Scott
  to say "done." Evidence in the conversation — commit SHAs quoted
  back, `tests passing 53/53`, `commit bfd069f pushed`, successful
  function execution reported — counts as completion evidence. If the
  conversation shows the work happened, the item closes.

## Validation run (done before this prompt ships)

Spawn one agent with this prompt over a known llm_memory session we
already have a summary for. Compare the agent's `journal` prose
against our human-written journal in `_journey.md` and its
`ledger_delta` against the corresponding delta in the journey doc.
Ship the prompt when:
- Introduced items count is within ±1 of the journey doc for each
  type.
- No invented quotes (every quote traceable to the conversation md).
- Resolutions correctly cite existing IDs (when prior state provided).
- Journal is coherent prose, not a bullet dump.
