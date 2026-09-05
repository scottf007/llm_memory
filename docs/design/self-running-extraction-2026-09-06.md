# Self-running narrative extraction — plan-gate design note

**Date:** 2026-09-06 · **Author:** selfrun-design-codex · **Rigor:** C · **Status:** candidate for PM ratification; no production code changed

**Goal:** after a substantive session ends, its project narrative refreshes without a person invoking `/narrative`; if refresh cannot complete, the condition remains visible until it is fixed.

Evidence is from the delivered pack `bd9735d2…`, live read-only measurements in this seat's `scratch/`, and the delivered UAI snapshot (input digest `d057400d…`; 2,309 entities / 8,136 relationships). The live `projects/llm_memory.json` was never passed to merger or renderer.

## 1. Measurements that decide the shape

### Hooks and unattended execution

| Measurement | Result |
|---|---|
| Installed Claude budgets | SessionStart 15 s; synchronous SessionEnd 30 s (`~/.claude/settings.json`; repo installer agrees) |
| Hook no-work cost, 20 runs | SessionStart compact/no-project median 31.9 ms, max 33.9 ms; missing-transcript SessionEnd median 67.7 ms, max 74.3 ms |
| Real ingestion-only SessionEnd | 742,533-byte stored transcript copied + stripped into scratch: median 110 ms, max 149 ms (5 runs) |
| User systemd | Manager is live. A transient detached unit dispatched and wrote a scratch marker in 0.05 s. No llm_memory unit/timer is installed. Only `am-watchdog.timer` (2 min) and an unrelated cache timer exist. `am` is installed at `~/.local/bin/am`, but is job infrastructure, not a product scheduler. |

The hooks can durably enqueue and dispatch; neither can contain extraction. SessionStart already starts a background installer, but raw shell backgrounding is not a lifecycle guarantee. A user-systemd service is.

### Same-session backend pair

Input was stored footballmanager session `14f31ddc…`: 21,603-byte conversation, 44 prior active items, current delta-extractor spec, identical condensed state and prompt transport. Outputs went to scratch; neither result was merged.

| Backend | Wall time | Tokens / cost | Result |
|---|---:|---:|---|
| Local `llm start 27b` → `qwen3.8-27b-mtp` | 69.38 s cold load + 115.40 s inference | 16,245 prompt + 5,137 completion; $0 | Valid JSON and dry in-memory merge; covered 7 of the 10 distinct claims/resolution emitted by today's Claude result (70%); emitted one terminal contradiction on which the Claude/reference delta disagreed |
| `claude -p --model sonnet` | 80.02 s process wall; 62.48 s reported model duration | $0.164579 total; Sonnet 22,276 effective input/cache + 6,263 output, plus a $0.012849 Haiku CLI auxiliary call | Valid JSON and dry in-memory merge; 1 decision, 2 learnings, 6 done rows, 1 closure; no disputed terminal action |

Claim recall was manually matched by meaning, not string overlap; the paired run is directional evidence, not a release benchmark. Today's 70% local/Claude ratio confirms the 22 Aug bake-off's roughly 70% local recall. Today's sample had 0% `load_bearing` from both, so it does not erase the earlier 44% local versus 12% reference over-grading case.

Both backends exceed both hook budgets. Local is currently slower as well as lower-recall; its advantages are zero marginal cloud cost and privacy.

### Queue discovery

`conversations.iter_sessions()` read 10,062 main-session frontmatters across 37 projects in a 0.71 s warm median (0.88 s in the recorded coverage run). Calling the real `compute_narrative_coverage()` once for all 37 projects took 71.90 s (84.99 s process wall including imports/setup), so an all-project sweep cannot sit on SessionStart.

Current filtered backlog is **98 unprocessed + 12 stale = 110 sessions across 11 projects**:

| Project | Unprocessed | Stale |
|---|---:|---:|
| agent-messaging | 33 | 2 |
| database | 1 | 1 |
| finance_nexus | 16 | 2 |
| llm_memory | 3 | 1 |
| load_balancer | 0 | 1 |
| mailsort | 2 | 1 |
| nonprofitadmin | 13 | 1 |
| ocr-app | 12 | 1 |
| sysadmin | 1 | 0 |
| universalai | 6 | 1 |
| utilityswitch | 11 | 1 |

Five projects already trip the seven-day content-wait liveness signal. Coverage, including its client/noise filters and `stale` detection, remains the source of truth; an enqueue record is only a wake hint.

### Existing concurrency and visibility

- The narrative skill requires within-project order: extractor B must read state after A merged; renderer runs after every merge because `contested.json` is the next extractor's input.
- There is no production lock around `merger.main`: it reads full state, applies, then replaces archived and active files. Atomic replacement prevents torn single files, not two concurrent read/modify/write cycles; two triggers can lose one writer's state. Even an already-merged session rewrites state, fans out items, and rebuilds the index.
- Archived-first write ordering makes a crash duplicate rather than lose a newly archived row, but it does not make two writers serializable.
- `lrn-6cc16ac2` records real within-drain grade oscillation: `contested.json` conveys the cut, not prior reasoning, so last-session revaluation wins. More concurrency would worsen it.
- The host test lock is `~/.am-host/test-llm.lock`; implementation/review tests use it. The product worker gets separate locks under `$LLM_MEMORY_HOME/runtime/` and never takes the test lock.
- SessionStart already prints `LLM_MEMORY_WARN` for unavailable counts and an age-based `AUTOMATIC TASK`. The rendered footer covers integrity/cascade backlog only; extraction failure itself has no persistent narrative line today. `narrative_coverage.stale` detects work but is pull-only.

## 2. Decisions

### D1 — SessionEnd enqueue + detached user-systemd worker

After the existing transcript copy/strip succeeds, SessionEnd atomically writes a unique request under `$LLM_MEMORY_HOME/runtime/extraction-requests/` and runs `systemctl --user start --no-block llm-memory-extract.service`. The service owns the long work. A `llm-memory-extract.timer` runs every five minutes with `Persistent=true` only to recover a missed hook, crash, or request arriving as a service exits.

The worker takes a nonblocking host singleton flock, then a per-project flock for the entire coverage → extract → validate → merge → render transaction. A losing trigger exits successfully because its unique request remains and the winner rechecks requests plus authoritative coverage before releasing the lock. Projects are alphabetical; sessions use `unprocessed_sorted`, then stale reruns. First slice is serial globally; cross-project parallelism waits.

Rejected: **timer-only**, because every success inherits polling latency and a disabled timer silently recreates the original gap; **next SessionStart**, because the 72 s sweep and 80–185 s model calls violate the no-slow-start constraint and there may be no next session; **`nohup ... &`**, because child survival is exactly the kind of shell-dependent failure already learned elsewhere, while user systemd was measured working.

### D2 — Qualification-gated local backend, then Claude, then explicit waiting

The worker builds the existing condensed active-items-only prompt and runs mechanical JSON/schema/known-ID validation plus an in-memory `apply_delta` check before any real merge. It stamps provenance after generation; the extractor prompt spec is unchanged.

Local becomes first choice only after a frozen, reviewed 10-session set passes all of: at least 90% claim recall relative to reviewed Sonnet deltas; 100% valid/mergeable JSON; zero unsupported terminal closures/archives/rejections/contradictions; at least 90% closure-status agreement; and `load_bearing` share no more than 10 percentage points above reference (and never above 20% unless reference itself is above 20%). Today's 70% pair fails, so first slice skips local rather than paying 115 s before falling back.

Fallback is one safe, tool-less, no-session-persistence `claude -p --model sonnet` call with a $0.50 per-session ceiling and a $3/day worker ceiling. Exceeding a cap or losing auth/network is not success: the request remains pending and status becomes `waiting`. No third silent backend exists.

Every applied session stores an `extraction` record: backend, concrete model, prompt/input hashes, attempt/completion timestamps, duration, token counts, cost (nullable), validator version, and request id. Reruns retain prior provenance in their rerun record.

### D3 — Persistent, repeated failure visibility

The worker atomically maintains `$LLM_MEMORY_HOME/projects/{project}.extraction-status.json`: state (`idle|running|waiting|failed`), authoritative unprocessed/stale counts, oldest wait, last attempt/success, backend, request ids, retry time, and a bounded error summary. Top-level exception handling writes it; the systemd unit also has an `OnFailure` helper so a pre-status crash cannot disappear.

Contract: dispatch immediately at SessionEnd; timer recovery within five minutes; after two failed attempts or ten minutes pending, `failed/waiting` is sticky. The worker re-renders the unchanged ledger with this status so the narrative footer says `⚠ Narrative pipeline: N session(s) waiting …`. Every later SessionStart performs only a cheap sidecar/request read and emits `LLM_MEMORY_WARN: extraction ...` on stdout until coverage is clear. `journalctl --user -u llm-memory-extract` is diagnostic evidence, not the user-facing signal.

Success is also explicit: final authoritative coverage must show the processed session absent from both `unprocessed` and `stale` before its request is retired and status clears.

### D4 — First slice is this machine + `llm_memory`, safety before breadth

Ratified slice processes only `llm_memory` (currently 3 unprocessed + 1 stale), serially, using Claude because local is not qualified. Installation creates/enables the user service and recovery timer. Rollout to the other ten backlogged projects occurs only after this slice drains and survives 48 hours.

Automatic runs do **not** apply `ledger_delta.revaluations` while `lrn-6cc16ac2` is unresolved: preserve raw output, remove those observations from the applied delta, count them in status, and leave `contested.json` visible for manual review. They also do not run the irreversible cascade resolver. Out of scope: changing the delta-extractor prompt, solving commutative revaluation, changing cascade semantics, cross-project parallelism, multi-machine ownership/sync, or tuning the local model.

## 3. UAI impact map (delivered snapshot; not rebuilt)

| Surface | Graph evidence and implementation boundary |
|---|---|
| Discovery | Reuse `server.compute_narrative_coverage` (direct production caller: `_handle_narrative_coverage`; shell SessionStart is outside the Python AST graph) and `conversations.iter_sessions` (production callers: `list_projects`, `list_sessions`, `server._client_by_session`). Do not create a second filter. |
| Mutation | Keep `merger.main`/`apply_delta` as the only ledger mutation path; add external locking and provenance plumbing. Snapshot has one production path into `apply_delta` (`merger.main`) and one into `tools.project_state.write_full` (`merger.main`), plus their tests. |
| Rendering | Extend `renderer.main`/`render_with_report` with extraction-status footer input; preserve existing contested/certificate writes and exit-2 contract. Snapshot shows only CLI entry plus renderer tests as direct `main` callers. |
| New orchestration | New worker/request/status modules, user-systemd units, installer wiring, SessionEnd enqueue, and SessionStart warning read. Shell/systemd edges are not represented by UAI, so acceptance must exercise installed files rather than infer reachability from the graph. |

## 4. Seats after PM ratification

| Seat | Produces |
|---|---|
| tests (Claude/Sonnet) | Frozen fake-home tests for enqueue/control, systemd install, duplicate triggers, re-trigger-during-drain, ordered two-session state handoff, stale rerun, backend qualification/fallback, provenance, warning/footer persistence, and revaluation/cascade quarantine. Trigger and non-trigger controls for every warning/guard. |
| implementation (Codex) | Worker, request/status format, locks, unit/timer and installer/hook wiring, provenance and footer plumbing; no prompt changes. RED-on-base/GREEN-on-candidate evidence. |
| judge (Claude/Opus) | Different-vendor review by execution of the exact commit: mutations for lock removal, dropped re-trigger, false-success coverage, backend failure, and warning suppression; one real scratch `claude -p` canary only if PM separately authorizes spend. |

## 5. Acceptance protocol

1. Under fake HOME/`LLM_MEMORY_HOME`, SessionEnd archives a transcript, creates exactly one valid request, dispatches in under 1 s, and never runs a model in the hook; no-request control stays silent.
2. Two simultaneous triggers plus a request arriving during drain produce one serialized project history: every session once, extractor N+1 sees merge N, final coverage zero, active/archive split consistent, no concurrent index rebuild.
3. Kill/restart at request, extraction, archived-first write, render, and status-update boundaries: request is recoverable, next timer self-heals, and no success is recorded before final coverage proves it.
4. Invalid local/Claude output and auth/download/budget failures never merge. Local is bypassed while unqualified. Successful state and reruns contain complete provenance; secrets and full prompts never enter status/logs.
5. With injected failure, within ten minutes status + narrative footer show the exact waiting count and every SessionStart prints `LLM_MEMORY_WARN`; success clears all three only after coverage is zero. Dormant/no-backlog controls emit nothing.
6. Existing narrative, merger/project-state, hook, installer, and default suites pass under `flock -w 1800 ~/.am-host/test-llm.lock` with fake stores. SessionStart p95 regression is under 100 ms and contains no coverage/model subprocess; SessionEnd representative p95 remains under 1 s and under its 30 s budget.
7. First live canary is `llm_memory` only, after cross-vendor PASS: record ledger hash before, drain its 3+1 backlog in order, verify coverage zero and provenance/cost, observe 48 hours/two timer cycles with no duplicate merge, then PM decides wider rollout. No push or all-project enablement is implicit.
