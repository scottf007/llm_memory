# Grok transcript ingestion — plan-gate design note

**Date:** 2026-09-03 · **Author:** llm-pm-3 (PM seat) · **Rigor:** c · **Status:** plan gate, awaiting coordinator ack before seats launch

**Owner priority (3 Sep):** the owner did significant `load_balancer` work directly in Grok and the next Claude agent on that project cannot see any of it. Build Grok ingestion at parity with the codex adapter.

Everything below that reads as a fact about the Grok store was measured on this machine on 3 Sep 2026 by a census over every session directory, not taken from a format survey. The codex adapter's central lesson (`docs/adapters.md`, "What the codex adapter cost") is that a survey of one session is not evidence of what a client emits.

## 1. The Grok store, as measured

Root: `~/.grok/sessions/<urlencoded-cwd>/<session_id>/`. One non-directory entry at the root (`session_search.sqlite`) and one `prompt_history.jsonl` per project dir; both are ignored by the adapter.

| Fact | Value |
|---|---|
| Project dirs / session dirs | 81 / 981 (growing; three appeared during the census) |
| Session dirs without `chat_history.jsonl` | 0 |
| Total `chat_history.jsonl` bytes | 174 MB (min 19 KB, median 78 KB, max 2.3 MB) |
| Full-corpus JSON parse, naive Python | 0.9 s / 57,633 records |
| Record types | `system` 981, `user` 9,566, `assistant` 13,518, `reasoning` 13,570, `tool_result` 19,941, `backend_tool_call` 47 |
| Per-record timestamps in `chat_history.jsonl` | **none** (no `timestamp`/`ts`/`created_at` on any record) |
| Session ids | UUIDv7 (`01a05f66-…`), same shape as codex thread ids |

**One dialect.** Unlike codex there is a single record shape. `user.content` is always a list of `{type: "text", text}` blocks (two records also carry `images`); `assistant.content` is always a string, with an optional `tool_calls` list.

**Three kinds of `user` record, distinguished by keys, not content:**

| Keys present | Count | What it is | Adapter rule |
|---|---|---|---|
| `prompt_index` | 4,337 | A real prompt. Interactive prompts are wrapped `<user_query>…</user_query>`; harness-driven prompts (scheduled loops, background-task completions) are bare `<system-reminder>` text but still carry `prompt_index`. | **keep** — this is the only kept user shape |
| `synthetic_reason` | 6,114 | Harness injections: `system_reminder` 4,243, `project_instructions` 772, `subagent_completed` 431, `notification_drain` 304, `scheduler_fired` 302, `task_completed` 56, `compaction_meta` 6 | drop |
| neither | ~981 | The first record of every session: `<user_info>`, `<git_status>`, `<rules>` preamble | drop |

**Correction (3 Sep, round-2 census by grok-impl-codex): 1,131 user records carry both `prompt_index` and `synthetic_reason`; the adapter keeps them (prompt_index wins), and the round-2 judge rules on whether that admits harness noise.** `prompt_index` count equals the session's `prompt_history.jsonl` entry count for interactive sessions and exceeds it for harness-driven ones, which is why the adapter keys on the record and not on `prompt_history.jsonl`.

**Session kinds (`summary.json`):**

| `session_kind` | Count | `agent_name` | Has `parent_session_id` |
|---|---|---|---|
| none (interactive primary) | 207 | `grok-build-plan` | no |
| `headless` | 1 | `grok-build-plan` | no |
| `subagent` | 301 | `general-purpose` | no |
| `subagent_resume` | 471 | `general-purpose` | yes (`fork_context_source: resumed`, `forked_at`) |

**Forks duplicate history verbatim.** A `subagent_resume` session's `chat_history.jsonl` begins with a byte-identical copy of its parent's entire file and continues from there (verified on chain `01a060ac → 01a060b1 → 01a060b3 → 01a060b5`: 31 parent records, 42 child records, identical 31-record prefix). Chains are how the board harness drives a Grok seat: a scheduled loop prompt every two minutes, each iteration a fork of the last. Ingesting every session would extract the same prose up to N times. On this machine every parent lives in the same project dir as its child and no parent id dangles.

Counting only chain tails (sessions no other session names as parent): **510** — 208 primaries plus 302 tails of subagent chains. Per project: finance_nexus 273 (2 primaries), agent-messaging 116, utilityswitch 38, unattributed 27 (cwd under `~/worktrees/`), llm_memory 24, universalai 22, nonprofitadmin 5, load_balancer 1, plus singletons.

**The acceptance case.** `%2Fhome%2Fscott%2Fprojects%2Fload_balancer` holds exactly one session, `01a05f66-e2bc-7332-8728-546c1a71e8cf`: interactive primary, 21 real prompts, 2026-09-01T23:56Z to 2026-09-02T22:09Z, title "Syd1 cloud-init rebuild proven, production untouched", 941 KB chat history. Today `narrative_coverage(load_balancer)` reports 11 transcripts on disk, all Claude.

**Timestamps live next door.** `events.jsonl` in the same dir carries `turn_started {ts, turn_number}` / `turn_ended` pairs, one per real prompt (21/21 for the acceptance session), plus `summary.json` has `created_at`, `updated_at`, `last_active_at`. Grok's own `prompt_history.jsonl` timestamps exist but do not cover forked or harness-injected prompts.

## 2. Decisions

**D1 — Module and base.** `adapters/grok.py`, four protocol functions plus `ref_for_path(dir)`. `SessionRef.path` is the session directory (the protocol docstring in `adapters/base.py` already anticipates this). `ID_PREFIX = "grok-"`, registered in `_REGISTRY` and `_PREFIXES` in `adapters/__init__.py`. **Built on main `e80a73f`, not on the F-08 branch**: F-08 (shared cache/dispatch facade) is unmerged with a review chain that has failed twice on documentation; the grok adapter copies codex's ~20-line cache scaffold and rebases trivially if F-08 lands.

**D2 — discover().** Walk `SESSIONS_DIR/*/*/chat_history.jsonl`; a session is a dir containing that file. Skip non-directories. Order oldest first by `summary.json.created_at`, falling back to dir mtime. Same id in two dirs: keep the first, as codex does. Unreadable entries are skipped and counted, never fatal.

**D3 — session_meta().** `cwd` from `summary.json.info.cwd`, falling back to the URL-decoded dir name; `project = project_from_cwd(cwd)` (already skips the dotted `.agent-messages/…/worktrees/<seat>` segment, so seat sessions attribute to their parent project; `~/worktrees/…` cwds come out unattributed, which is the codex behaviour too). `started = created_at`, `ended = last_active_at or updated_at`. `parent_session_id = grok-<parent>` when present. `extra`: `model` (`current_model_id`), `title` (`generated_title`), `session_kind`, `agent_name`. `raw = transcripts/grok-<sid>.jsonl`, `raw_source = <chat_history.jsonl path>`.

**D4 — Superseded sessions are the subagent rule.** `is_subagent = True` iff another session in the same project dir names this one as `parent_session_id`. `process_foreign_session` already skips `is_subagent` sessions, so a superseded prefix writes nothing and the chain tail carries the whole conversation once. The tail's frontmatter records `fork_of: grok-<parent>` so provenance survives. Rejected alternative: stubbing every `subagent`/`subagent_resume` session — that would drop all Grok seat work (finance_nexus would keep 2 sessions of 275) and break parity, since codex's single-prompt seat runs are ingested today. The same-dir assumption is pinned by a live-store census test (0 cross-dir parents, 0 dangling parents) that skips where the store is absent.

**D5 — turns().** One streaming pass over `chat_history.jsonl`, `raw_line` = line number.
- `user` with `prompt_index` → user turn. Text = joined `text` blocks; strip exactly one outer `<user_query>` wrapper if present; whitespace-normalise like codex (`\n{3,}` → `\n\n`, strip). No other tag stripping — the angle brackets that survive are prose or harness prompts that *are* the seat's task.
- `user` with `synthetic_reason` and no `prompt_index`, or with neither key → dropped. **Amended after round 2 (verdict 01788393451001602134): a record carrying both keys is kept — `prompt_index` wins, as fixture 09 pins. Residual telemetry-only turns (`subagent_completed`, `task_completed`) are an open follow-up, not this arc's scope.**
- `assistant` → assistant turn; `had_tool_use = bool(tool_calls)`; empty text with tool calls is a tool-only marker, as in codex.
- `backend_tool_call` → tool-only marker (model-side web search; mirrors codex `web_search_call`).
- `system`, `reasoning` (encrypted, summaries only), `tool_result` → dropped.
- Timestamps: read `events.jsonl` once; the k-th kept user turn takes the k-th `turn_started.ts` in file order, assistant turns inherit the current user turn's ts. If `events.jsonl` is missing or short, remaining turns take `created_at` and `meta.notes` says so. Never a crash, never an empty timestamp when `summary.json` exists.

**D6 — Envelope and render.** Unchanged shared code: `write_envelope` → `transcripts/grok-<sid>.jsonl`, `render_conversation` → `conversations/grok-<sid>.md` with `client: grok` and project stamped. `verify_envelope` must pass for every non-superseded session on this machine (live census test, skipped where the store is absent).

**D7 — narrative_coverage picks Grok up.** `_MIN_USER_TURNS_BY_CLIENT["grok"] = 1`, same as codex: 302 of 981 sessions have exactly one real prompt and the harness loop drivers are single-prompt by construction; the existing 50-char assistant-content filter already removes the empty ones. The literal map inside `compute_narrative_coverage` (server.py ~589) hardcodes `claude` and `codex` and must gain `grok`; a new test asserts every registered adapter has an explicit threshold so the next client cannot silently fall to the Claude default of 5. No Grok analogue of `_is_codex_auto_participant` is needed: no polling participant exists on the Grok side.

**D8 — Sweep hook.** `hooks/session_start.sh` runs `process_transcripts.py --quiet`, which iterates `adapters.names()`, so registration alone wires the hook and the first run is the backfill. One change in `process_foreign_session`: skip a session whose envelope already exists with mtime ≥ the client's source file mtime. **Amended by PM ruling 01788392769744608957 after judge finding B1: D4 outranks D8. The supersession check runs before the skip; a superseded session has its stale envelope and conversation.md removed and yields None, and adapters without an `is_superseded` hook get the same cleanup after parse when `is_subagent` is reported. Pinned by amendment 2 (13a7cdc).** Today codex re-parses and rewrites all 127 sessions on every session start; Grok would add 981. Budget: incremental sweep ≤ 1.5 s at session start, measured and posted by the implementer.

**D9 — Fixtures.** `tools/make_grok_fixtures.py` mirroring `make_codex_fixtures.py`: structure kept exactly (record types, keys, ordering, `summary.json`, the `events.jsonl` turn records), every string replaced by a length placeholder — including the xAI system prompt. Each fixture is a directory with pinned `.expected.md` and `.expected.envelope.jsonl`. Selection by feature, at least eight: interactive primary with `<user_query>` prompts; single-prompt primary; chain tail whose file carries a forked prefix; a superseded parent (must yield `is_subagent`); `backend_tool_call`; an `images` block; unattributed cwd; missing or short `events.jsonl` (timestamp fallback); a session with `subagent_completed` synthetics.

**D10 — Docs and non-goals.** `docs/adapters.md` gains "What the grok adapter cost": no per-record timestamps, fork duplication, keep-iff-`prompt_index`. Out of scope, explicitly: Grok-side hooks or context injection (F-19, F-21 stay as documented); the delta-extractor prompt spec; the renderer's 8-character id display (`grok-01…` collides exactly as `codex-01…` does — existing follow-up, not widened here); the 356 legacy-dialect codex transcripts (separate ticket).

## 3. Mechanical impact map (UAI, `uai callers` on the llm_memory graph)

| Entity touched | Callers |
|---|---|
| `adapters.get` | `process_transcripts.find_transcripts`, `process_foreign_session`, `main`; 4 tests |
| `adapters.names` | `extract_conversation.main`, `process_transcripts.main`; 4 tests |
| `adapters.envelope.write_envelope` | `process_foreign_session`; 4 codex tests |
| `adapters.base.project_from_cwd` | `claude._parse`, `codex._parse`, `reattribute_dotted_conversations`, `make_codex_fixtures`; 4 tests |
| `server.compute_narrative_coverage` | direct 2, transitive 16 (`_handle_narrative_coverage`, `call_tool`, `_call_tool_request`; 12 tests in `test_narrative_coverage.py`) |

Files changed: `adapters/grok.py` (new), `adapters/__init__.py`, `server.py` (two sites: the threshold map at ~263 and the literal at ~589), `process_transcripts.py` (`process_foreign_session`), `docs/adapters.md`, `tools/make_grok_fixtures.py` (new), `tests/test_grok_adapter.py` (new), `tests/fixtures/grok/` (new), `tests/test_narrative_coverage.py` (additions). Untouched: `adapters/base.py`, `claude.py`, `codex.py`, `envelope.py`, `render.py`, `merger.py`, `renderer.py`, `agents/delta-extractor.md`, `hooks/*`.

## 4. Seats (rigor c; author ≠ implementer ≠ judge; no grok seats)

1. **grok-tests-sonnet** (claude sonnet, `--role test`): frozen tests, fixture generator, fixtures, coverage-test additions. Must be red on main except structural tests. Inputs: this note, `docs/adapters.md`, `adapters/codex.py`, `tests/test_codex_adapter.py`, `tools/make_codex_fixtures.py`.
2. **grok-impl-codex** (codex, `--role code`): everything in §3 against the pinned pack. Reports sweep timing (D8) and the live census (D4, D6) in its status.
3. **grok-judge-opus** (claude opus, `--role judge`, cross-vendor to codex): verify by execution — full suite, fixture sanitisation walk, live census read-only, live store hash unchanged before/after its run.
4. **PM**: merge the judged blob to main, then run acceptance (§5) and the backfill. Max two judge rounds; out-of-plan findings become tickets.

## 5. Acceptance protocol (PM-run, mechanical)

1. Record sha256 + size of `projects/load_balancer.json`, `.archived.json`, `.narrative.md`, and the count of `transcripts/*.jsonl` and `conversations/*.md`.
2. From the merged checkout: `python3 process_transcripts.py --client grok`. Expect 510 new `transcripts/grok-*.jsonl` and 510 `conversations/grok-*.md`; zero `WARN` envelope lines; `conversations/grok-01a05f66-….md` carries `project: load_balancer`.
3. `narrative_coverage(load_balancer)` lists `transcripts/grok-01a05f66-….jsonl` as unprocessed with timestamp `2026-09-01T23:56Z` and `min_user_turns_by_client.grok == 1`.
4. Run `/narrative` for load_balancer (delta-extractor → merger → renderer). Expect `sessions[]` in `load_balancer.json` to contain `grok-01a05f66-…` and the rendered narrative to carry the Sydney provisioning work.
5. Diff the hashes: only load_balancer state files and the new `grok-*` files changed.
6. Post the evidence (counts, hashes, the narrative excerpt) on the project board and to chairman.

## 6. Owner decision to escalate (not pre-empted)

`install.sh --update` installs only from the GitHub tarball, and this seat never pushes. The installed lib at `~/.claude/memory/lib` (VERSION `e80a73f`) therefore cannot receive the merged adapter through the auto-update path. The acceptance run above executes from the repo checkout against the same live store, so the owner's load_balancer sessions become visible regardless — but the *session-start sweep* (deliverable 4) only runs the installed lib. Recommendation: owner pushes main after acceptance, auto-update does the rest. Alternative: a one-off local copy into the lib, which the next tarball update would overwrite anyway.
