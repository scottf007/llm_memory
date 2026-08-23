# Release Notes

Narrative history of llm_memory, assembled from the git log (69 commits,
2026-03-08 → 2026-08-10). Where `CHANGELOG.md` records *what* changed in
Keep-a-Changelog form, this file records *why*, and what it means if you are
upgrading an existing install.

Versions after 1.1.0 are assigned retroactively here — the repo's `v2026.*`
tags are per-commit build stamps from March, not releases.

**Upgrading:** `install.sh` pulls the current `main` tarball, so any upgrade
crossing 2.0.0 needs the migration notes below. Everything else is drop-in;
MCP server changes need a Claude Code restart before the new tool surface
appears.

---

## 2.3.0 — 2026-08-10

### Sessions that keep running after they are merged

A session merged on day 1 and still running on day 6 reported as fully
processed. `narrative_coverage` decided "processed" by testing membership in
`{project}.json.sessions[]`, which only proves a session was merged *once*.
Worse, re-extracting it did nothing: `apply_delta` returned early on the
already-merged session id while the fan-out step still rewrote the file and
printed `Merged ...`, so the discard was invisible. A 206k-token
re-extraction of example_project — 14 items, 26 revaluations, 11 resolutions —
was lost to this exact path before it was found.

**Added**

- `narrative_coverage` returns a `stale` list beside `unprocessed`. Each entry
  carries `merged_through`, `last_activity`, `grew_days` and the transcript
  path.
- Staleness is measured from the transcript's last message timestamp versus
  the `ended` recorded on the session. Content-based, so it needs no schema
  change and applies retroactively to every session already on disk. File
  mtime was evaluated and rejected — `extract_conversation.py` rewrites the
  stripped conversation wholesale, so mtime flags files that never changed.
- `merger.py --rerun` applies a delta for an already-merged session,
  de-duplicating re-emitted items by normalised text and refreshing the
  session's `ended` watermark so it stops reporting stale. Prior passes are
  kept under `sessions[].reruns[]`.
- `/narrative` reads the `stale` list, forces a fresh extraction for those
  sessions (the cached delta is by definition the one that already merged),
  and merges them with `--rerun`.

**Fixed**

- The merger no longer reports a no-op as success. Without `--rerun` an
  already-merged session prints `Skipped ... (already merged)`; the applied
  paths print `Merged` / `Re-merged`.
- `What's Done` no longer renders `Nothing shipped yet` when every done item
  is archived. It now points at the archive with a count, matching the
  existing "N dissolved" idiom. A project with genuinely no recorded work
  gets its own wording.

**Changed**

- `done[]` removed from the state-auditor's three-part-filter sweep. Every
  build-log entry is historical and captured by git, so the filter archived
  all of them by construction — 425 in example_project, which is what emptied
  the section. Bounding that section is the renderer's job, via scoring and
  per-section budgets. Archive a done item only when the delta pipeline
  supersedes it or its subject was physically deleted.
- Stale threshold set at 24h of growth. It gates a re-extraction, not just a
  report; at 1h a sweep of 29 projects flagged 32 sessions, 11 of which had
  grown only a few hours of closing messages.

---

## 2.2.0 — 2026-08-06

### The narrative stops growing without bound

The rendered narrative is injected at every session start, so its size is a
per-session cost paid forever. It was unbounded in project age: `load_bearing`
items bypassed the decay filter entirely and goals and suggestions had no
filter at all. example_project reached ~33k tokens (~223KB). The documented
"target ≤5,000 tokens" had nothing enforcing it.

**Changed**

- `load_bearing` gets a score *floor* rather than a render exemption. It still
  sorts to the top and stays eligible indefinitely, but it competes for space.
- Per-section token budgets (`SECTION_TOKEN_BUDGETS`). Items render in score
  order until the budget is spent; the remainder collapses into the existing
  `project_lookup` pointer. The budget is soft — a section may reach 1.5× —
  but only for items above `SOFT_OVERFLOW_SCORE`, so valuable material can
  run over and filler cannot. Operations is deliberately exempt.
- Suggestions now decay, as the format spec always asked. Goals do not, per
  spec, but get a budget backstop that announces itself when it fires.
- The stale callout uses an unfloored score, so the floor cannot hide the
  items that callout exists to surface.

**Added**

- Optional per-item `value` (0.0–1.0) orders items *within* an importance
  tier. The bucket is the coarse class a model grades reliably; the float is
  the fine ordering it can only give relatively. Without it, ranking inside a
  tier falls back to recency, which is backwards for load-bearing items.
  Absent or malformed values are neutral, so existing items need no migration.
  NaN is explicitly rejected — it survives `isinstance` and then poisons every
  comparison downstream, silently reordering the section.
- Contested re-valuation. When a section runs out of budget the renderer
  writes `{project}.contested.json` naming the items the budget actually had
  to choose between. The next `/narrative` run asks the extractor to re-grade
  only those, rather than re-auditing the whole ledger. The sidecar is removed
  when nothing is cut, so its presence is the signal.

**Fixed**

- The merger derives its items root and index path from the state file's
  location. Running it against a copy outside `~/.claude/memory/projects`
  writes items and index beside that copy and says so, instead of mutating the
  real memory tree — which makes dry runs safe.

---

## 2.1.0 — 2026-05-25

Pipeline hardening after the 2.0.0 rewrite settled.

- Sub-agent transcripts (`agent-*`) and short one-shot sessions are filtered
  out of `narrative_coverage`, so the pipeline stops burning extractor runs on
  single-prompt SDK noise.
- `delta-extractor` and `state-auditor` run on Sonnet.
- `/narrative` stopped prompting four times per run.
- Absolute paths passed to the delta-extractor subagent.
- Fixed the project-discovery snippet's `sys.path` and dropped the last
  `memory_store` references from the docs.

---

## 2.0.0 — 2026-04-20

### The narrative becomes a projection of structured data

The largest change in the project's history. Before this, a narrative was a
document an LLM wrote and rewrote; overclaims and silent drops were possible
on every pass. After it, `{project}.json` is the source of truth, the renderer
is pure code, and the LLM's only job is to emit a structured delta.

**The pipeline**

`delta-extractor` (one agent per session, reads the stripped conversation plus
current state) → `merger.py` (applies the delta, assigns stable IDs, archives
with reasons) → `renderer.py` (pure code, no LLM, emits the 9-section
markdown). The rendered `{project}.narrative.md` is read directly by the
session-start and subagent-start hooks.

**Breaking**

- Six MCP tools removed: `memory_store`, `memory_recent`, `memory_get`,
  `memory_connect`, `memory_explore`, `memory_delete`. They only queried the
  dropped tables. Net −400 lines.
- The `memories` and `connections` tables are gone. Storage moved to
  per-project `{project}.json`, per-item files under
  `items/{project}/{kind}/{id}.json`, and an FTS5 index rebuilt from those
  files.
- Item IDs migrated from integer suffixes to UUIDs (`migrate_item_ids.py`,
  one-shot).
- `narrative-updater` retired in favour of the delta pipeline.
- The `session_log` SQL table is replaced by a registry reading frontmatter
  from `conversations/*.md`.

**Added**

- `resume(project, lines)` — last real session's journal plus a conversation
  tail, on demand. Skips synthetic audit and agent sessions.
- `project_lookup(project, query, kind?, status?, limit?)` — fuzzy keyword
  search over one project's ledger items.
- `delta_cache.py` — skips the LLM call when a cached delta was produced by
  the current extractor prompt. On a prompt change it re-extracts with
  probability `exp(-age_days / 14d)`, deterministic per session so the verdict
  is reproducible across runs.
- `resolve_conflicts.py` — reconciles Syncthing sync-conflict files under
  `items/`. Archived wins over active; otherwise later `last_touched_at` wins
  and the loser is snapshotted into `history[]`, so nothing is lost.
- `state-auditor` agent for one-off cross-session sweeps.
- JSONL dialogue extractor: raw transcripts are stripped to user/assistant
  text with the project stamped in frontmatter.
- Projects auto-bootstrap; the installer removes files retired from the repo.

**Changed**

- Narrative size dropped ~80% (55k → 11k tokens for llm_memory) via mandatory
  closure sweeps over decisions, learnings and done — not just goals and
  suggestions — plus `weight × exp(-age_days / 30)` decay scoring and moving
  the resume excerpt out of the narrative body into the `resume` tool.
- The extractor's grading rule became the three-part filter: design-shaping
  AND non-obvious AND current.
- Deltas write to `~/.claude/memory/deltas/`, not `/tmp`.
- SessionEnd runs synchronously so resumed sessions get their `.md` written.
- The llm_memory version shows in the SessionStart banner.

**Migration**

Run `install.sh`. It removes retired files and applies the ID migration.
Restart Claude Code afterwards — the MCP server is a stdio subprocess and
holds the old tool schema until it respawns.

---

## 1.2.0 — 2026-03-20

- Operations section added to the narrative format, plus deterministic
  validation.
- Skills and agents ship in the repo and install pipeline.
- Server tests skip gracefully when the `mcp` package isn't installed.

---

## 1.1.0 — 2026-03-12

`narrative_coverage` introduced; narrative-updater rewritten for
one-transcript-per-invocation. Full detail in `CHANGELOG.md`.

---

## 1.0.0 — 2026-03-10

First public release: MCP server, hooks, dashboard, install and auto-update
from GitHub, file-based multi-device sync. Full detail in `CHANGELOG.md`.

Development from 2026-03-08 to this point covered chunk summaries, transcript
archiving, project-aware hooks, the v2 narrative format, and a run of venv and
install-permission fixes on 2026-03-10 that the tags still record.
