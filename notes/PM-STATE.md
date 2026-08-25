# PM-STATE — llm_memory

Seat: `llm-memory-pm` (standing product PM). Owner: Scott.
Written: 2026-08-23. Supersedes nothing; this is the first PM-STATE for this product.

Written the way `utilityswitch-pm` learned to after its crash (`01786956546187997684`):
obligations live in a file on disk, incurred as they arise, not in a PM's context. A
replacement PM picks this up without a re-brief from Scott.

**Everything below was verified against the repo, the store and the boards directly.
Where I trusted a report rather than checking, the line says so.**

---

## 1. THE PRODUCT

A persistent memory system for Claude Code, and now for other CLI agents, so context
survives across sessions and projects without manual rehydration. Fully local, no
cloud, no API keys, heading toward open-source release.

Pipeline, in order: session transcript → stripped `conversations/<sid>.md` → delta
extraction → `merger.py` → `{project}.json` (source of truth) → `renderer.py` →
`{project}.narrative.md` → injected at SessionStart by the hook. Plus an MCP server
(`memory_search`, `project_lookup`, `narrative_coverage`, `resume`) and an FTS5 index.

The current program is **multi-client**: make the thing client-agnostic so Codex, Grok
and local models feed and read the same store. Slices S1 (adapter protocol) → S2 (codex
adapter) → S3 (serving + injection) → S4 (adapter consolidation).

---

## 2. WHERE THE PRODUCT ACTUALLY STANDS

**main = `7b96b22`** as of this sitting. I merged S3 today; see §4.

Live on main, confirmed by `git log` and by file inspection, not by the ledger:

| slice | what landed | status |
|---|---|---|
| S1 | adapter protocol — `adapters/{base,claude,codex,envelope,render}.py`, byte-for-byte re-extraction oracle | merged |
| S2 | codex adapter | merged |
| post-ingest | codex fixture scrub, per-client `min_user_turns` + codex-auto structural filter, renderer short-id collision fix | merged `a996002` |
| — | `mcp>=1.0,<2` pin (escalation `09ea13cb`) | merged `7077c0c` |
| S3 | `tools/memory_wrap` generic injection wrapper, MCP wiring recipes, grok probe runbook, installer ships the wrapper | **merged today `7b96b22`** |

Suite at main from a fresh clone + fresh venv built from the declared `requirements.txt`:
**271 passed, 1 skipped**, `mcp` resolving to 1.29.0, `import server` OK. Run by me
today, after the merge, not quoted from the judges.

**Nothing is published.** `origin/main` is still at `e3ae2a7` — local main is now **6
commits ahead of origin**. Every slice above exists only on this machine. See §5.1.

---

## 3. THE THING THAT MATTERS MOST — the product is not eating its own dog food

`narrative_coverage(llm_memory)`, run today:

```
narrative_updated : 2026-08-10T05:15:36   (13 days ago)
on_disk_count     : 87        processed: 36        unprocessed: 6
```

Ingestion is healthy — `conversations/` and `transcripts/` have files written **today**,
7569 and 8676 respectively. The stage that is dead is everything after it: extract →
merge → render. `~/.claude/memory/projects/llm_memory.json` and `.narrative.md` are both
dated **10 August**.

The consequence, concretely: the narrative auto-injected into *this* PM session at
startup — my primary designed input, the artifact the brief told me to read first —
contains **no trace of the entire multi-client program**. No S1, no S2, no S3, no adapter
protocol, no mcp pin, no fleet run of 17 August. I had to reconstruct the product's state
from the job event log and the git history instead, which is exactly the manual
rehydration this product exists to abolish.

This is not a stale-file annoyance. It is the product's core promise failing on its own
repository, and it is measurable: 6 unprocessed sessions, 13 days, one whole delivered
program invisible. Other projects show the same shape (`agent-messaging.json` 13 Aug,
`universalai.json` 13 Aug; only `utilityswitch` is as recent as 17 Aug).

**I am treating this as the product's top finding, ahead of every queued slice.**
Proposed as the next slice in §6.

---

## 4. JOB: llm-memory-multiclient

Mode `managed`. Owner Scott. 96 events. The board ran hard on 17 August and then went
silent for six days — the last event before mine was `glm-arm`'s verdict at 13:34.

### 4.1 What I did this sitting

**Merged S3.** The gate condition had been met for six days and nobody was left to act
on it. Two independent PASS verdicts at the same SHA:

- `s3-judge` (claude lineage), `01786971962654367040` — PASS, judge-executed
  fast-forward, PM merges at own authority.
- `glm-arm` (z-ai / opencode-GLM lineage), `01786973651112959419` — PASS, all seven
  items reproduced independently, no disagreement with the Claude judge on any item.
  This is the different-vendor-lineage review HOW-WE-WORK requires, and it is the half
  the prior PM opened `glm-arm` specifically to obtain.

Re-verified by me before merging, because six days had passed:
`main` still an ancestor of `7b96b22` (a real fast-forward, not a rebase);
`git diff 7077c0c 7b96b22` sha256 `05b67106798ed8d29ccaf40e1cba770f0c4da4529f257cbfcc7574157f15a616`
— byte-identical to what both judges cited; 9 files, +735/−25; `tools/memory_wrap` is
100755 in the tree. Merged `--ff-only`. Post-merge fresh-environment suite: 271/1.

Not escalated, deliberately: a judge-executed fast-forward with two independent PASS
verdicts at a SHA I re-verified is inside the job's declared mode, and HOW-WE-WORK says
not to make the owner approve logistics already inside it.

### 4.2 Branches, and what is still off main

| branch | SHA | state |
|---|---|---|
| `claude/s3-serving-rebased` | `7b96b22` | **merged to main today** |
| `claude/s3-serving` | `76391c7` | the seat's original, untouched by design — left intact so the seat finds its own state if it wakes |
| `claude/feedback-ledger` | `8a5c3b7` | **not merged**, and cannot fast-forward — see §5.2 |
| `claude/mc-design` | `5879bf6` | design note `notes/design-multiclient.md`, 682 lines, never merged |
| `claude/s1-adapter`, `claude/s2-codex`, `claude/post-ingest` | — | content already on main; prunable |
| `narrative-budget` | `e607d00` | orphan, predates this program; unassessed |

### 4.3 Seats

All dead. `claude-fable-COO` (coordinator), `s1-adapter`, `mc-design`, `s1-judge`,
`s3-serving`, `s3-judge`, `post-ingest`, `glm-arm`. Nothing is in flight; nothing is
waiting on a running process. The job is idle, not stalled mid-work.

Two seat-level facts worth carrying forward, both established on the record:

- **`s3-serving` never accounted for the spawn incident** (`F-23`). It committed its
  fixes and went quiet across three activation pings; the COO's primary evidence
  (`01786945149023375027`) stands, the seat's dispute stands, and no reconciliation
  exists. The code is fine — that was independently established twice. What is missing
  is the account of the manual commands. F-23 stays open.
- **`s3-judge` died twice mid-assignment.** One judge seat for a whole job, dying under
  load, is why the prior PM refused to run S3 and S4 gates concurrently. Keep that
  constraint: one gate at a time, or provision a second judge seat.

### 4.4 The feedback ledger

`docs/FEEDBACK.md` on `claude/feedback-ledger` at `8a5c3b7`, 26 rows, index reconciles.
It is a genuinely good artifact and it is the required input to every brief on this
product. Two problems with it right now:

1. It is **not merged**, so it is invisible to anyone who does not know the branch name.
2. It is **stale on its own subject**: F-01, F-02 and F-03 are still marked `in-flight`
   when all three merged in `a996002` on 17 August, and F-04/F-05/F-06 are marked
   `closed` on a branch that had not merged when they were closed — they only became
   true today. A ledger whose statuses lag reality is a ledger that will mislead the
   next brief.

Open rows worth a PM's attention (full list in the file): **F-08** adapter boilerplate
duplication (this is the S4 slice), **F-09** archive-provenance drift, **F-10** the
permanent ~10 stale-`.md` oracle failures, **F-13** the dev `.venv` holds mcp 2.0.0 and
cannot collect the suite, **F-15** nothing downstream reads `client:` yet, **F-17** codex
and qwen both ship native memory and displacement is undecided, **F-18** `LLM_MEMORY_HOME`
recommended and never built, **F-19** the rules line has never been observed to work on
any client, **F-23** the spawn incident, **F-26** the mcp<2 pin bought time and set no
trigger date.

**F-15 deserves naming as a product-level gap, not a row.** The whole S1/S2 program adds
`client:` to the conversation frontmatter, and nothing reads it. Multi-client ingestion
is real; multi-client *serving* is one wrapper and a set of recipes. That is the honest
state of "multi-client" today.

---

## 5. OPEN DECISIONS FOR SCOTT

Four. Ordered by what blocks the most. None is urgent tonight; all four are cheap.

### 5.1 Publish, or stay local? *(the biggest one, and it is not a coordination question)*

`origin/main` is at `e3ae2a7`. Local main is 6 commits ahead. The entire multi-client
program — S1, S2, post-ingest, the mcp pin, S3 — has never been pushed. Meanwhile the
stated direction is open-source release, and the `mcp<2` escalation was argued *on the
grounds that* a new user's first run is the whole audience.

There is a real tension there and it is yours to resolve: we fixed a defect that only
bites new installs, for a repository no new installer can reach. **Do we push?** If yes,
I will run a release-readiness pass first (§6.2) rather than push a tree with 26 open
ledger rows and a 13-day-dead narrative pipeline into public view.

### 5.2 Ledger merge — one line of clearance

`claude/feedback-ledger` at `8a5c3b7` is based on `c2557ee` off `e3ae2a7`. Main has moved
twice since; it is **not** a fast-forward, so merging it means a docs-only rebase onto a
SHA no judge has seen. My brief escalates non-FF merges. The prior PM held it for exactly
this reason and asked for the same clearance, which never came.

It is one file, `docs/FEEDBACK.md`, and about as harmless as a rebase gets. **Say the
word and I rebase and merge it** — with the status corrections from §4.4 applied in the
same commit, so the merged ledger is true on the day it lands.

### 5.3 F-26 — the mcp 2.x port has no trigger date

The `mcp>=1.0,<2` pin restored fresh installs. It did not make `server.py` compatible
with mcp 2.x; `Server.list_tools` is gone in 2.0 and we are simply refusing to see it.
A ceiling with no reopen condition is a deferred item that will be rediscovered at full
cost, which is the exact failure mode the ledger exists to prevent.

**Pick one:** (a) port `server.py` to the mcp 2.x API as a small slice now, while the
context is warm; (b) keep the pin and set an explicit trigger — a date, or "when a
dependency we want requires mcp 2.x", written into F-26. My recommendation is (b) with
a named trigger, and (a) folded into the release-readiness pass if you choose to publish.

### 5.4 The qwen narrative backend — already answered, off the record

The COO queued a narrative-on-qwen pilot on 17 August (`01786950192264618966`) as rung 3
of the standing qwen ladder. **That pilot effectively ran on 22 August, outside the job
record**, and produced numbers: Qwen3.8-27B via llama-swap is structurally clean (3/3
valid, schema-correct, mergeable, caught one contradiction Sonnet missed) but recovers
only ~50–70% of Sonnet's items, finds zero archives where Sonnet found 3, over-grades
`load_bearing` at 44% against a 10–15% target, and mislabels closure. Haiku 4.5 on the
same three sessions is no better and costs money. Harness and outputs:
`~/.claude/memory/bakeoffs-qwen38-extractor-2026-08-22/`.

Read against the COO's own pass condition — "pass ⇒ qwen becomes the default narrative
backend" — this is a **fail**, and the rung stays Claude. I am not treating that as my
call to make silently: **confirm and I will close the queued pilot with these numbers**,
write them into the ledger, and take the rung off the queue. The mitigation path (local
backend in the `/narrative` launcher plus a post-check rejecting >20% load_bearing) stays
available if the cost of Sonnet extraction ever becomes the binding constraint.

*(Recorded here because it is a decision-shaped fact that exists only in a memory file
and in one bake-off directory. Generated views and memory are evidence, not replacements
for the job record — so it goes on the board too.)*

---

## 6. PROPOSED NEXT SLICE

### 6.1 Recommended: **restart the narrative pipeline, then make it not stop again**

Not `LLM_MEMORY_HOME`, and not S4. Both are real and both are queued behind this.

The reasoning is §3. This product's single claim is that context survives without manual
rehydration. On its own repository that claim has been false for thirteen days, and the
first person it failed was the PM the owner appointed to run it. A slice that consolidates
adapter boilerplate while the narrative is thirteen days dead is a slice that improves the
plumbing of a thing nobody is being served by.

Shape, in two halves:

**(a) Drain the backlog.** Run the `/narrative` pipeline over the 6 unprocessed
`llm_memory` sessions — which is where the entire 17 August fleet run lives — and then
across the other projects showing the same gap. This is mechanical and it is the
acceptance test for (b): the injected narrative must contain S1/S2/S3 afterwards.

**(b) Find out why it stopped, and guard it.** The interesting question is not "run the
pipeline", it is **why nobody noticed for thirteen days while ingestion kept working
perfectly**. Ingestion is hooked and automatic; extraction is a skill someone has to
invoke. That asymmetry is the defect. Acceptance: a check that makes an N-day-stale
narrative *visible* — the SessionStart hook already knows how to say "AUTOMATIC TASK: no
narrative exists"; it evidently does not say it loudly enough, or does not fire on
staleness at all. Establish which, then fix that specific thing with a trigger test and
a non-trigger control.

Cost: small. Routing: one sonnet implementer, one opus judge, test-author ≠ implementer.
Half (a) needs no gate.

### 6.2 Then, in this order

1. **Ledger merge with status corrections** (§5.2) — trivial, unblocks every later brief.
2. **`LLM_MEMORY_HOME` / D2 / F-18.** Scott already ruled the scope on 17 August: a
   generic folder, the variable in the application, not the vendor name. 17 hardcoded
   `Path.home()/'.claude'/'memory'` sites across 15 files, no shared config module;
   `merger.py:38 _memory_root()` is the pattern to generalise. **This goes before S4**,
   for a reason the prior PM established and I have re-checked: two of those 17 sites are
   `ARCHIVE_DIR` in `adapters/claude.py` and `adapters/codex.py` — the exact literal S4
   would move byte-identical into `adapters/base.py`. S4 first bakes the hardcoded path
   into `base.py` and forces a second pass over the same lines.
3. **S4 adapter consolidation** (F-08). Now consolidates an already-generic reference.
4. **Release-readiness pass** — only if §5.1 says publish.
5. **F-15**: make something downstream actually read `client:`. This is what turns
   "multi-client ingestion" into "multi-client", and right now nothing owns it.

---

## 7. JOB: memory-handoff-pilot

Mode `manual`. Goal: prove cross-vendor project handoff without a human re-brief.
19 events, all 13 August. **Concluded; nothing in flight; no seat alive.**

Outcome, per `codex-pilot`'s adjudication (`01786627849021242593`) and `claude-handoff`'s
reconciliation (`01786627904557736042`):

- **The handoff itself succeeded.** Six seats, three vendors, one unbroken record, zero
  owner intervention — `grok-cold` (died on HTTP 402 mid-run) → `codex-pilot` (preserved
  it, and correctly refused to synthesize a verdict under Grok's name) → `grok-tui` →
  `claude-handoff` → independent `codex-review` + `claude-review` approvals → adjudication.
  Every seat reconstructed goal, blocker and finish line from the event log alone, across
  a mid-flight vendor death. That is the thing the job set out to prove, and it held.
- **P0 FAILED as preregistered** — not on llm_memory, on Agent Messaging's cold install:
  `pip install -e` hit PEP 668 and produced no `am` on PATH. P5 FAILED: the dying Grok
  runner never announced its own blocker.
- Preregistered remediation, and it belongs to **agent-messaging, not to this product**:
  a PEP-668-safe installer that puts `am` on PATH, and a managed runner that persists
  request/stdout/stderr and publishes terminal failure without relying on the dying model.

**One llm_memory item survives from it, unclosed:** `claude-review` found that
`merger.py`'s `revalued_at` stamp has no assertion anywhere — the suite would pass
identically with the line deleted. Non-blocking, tracked value only, one assertion in
`test_revaluation_is_recorded_on_the_session` closes it. It is not in the ledger. I am
opening it as a ticket (§8) rather than letting a 13-day-old finding evaporate.

I hold this job open and joined, with a monitor. I do not propose new work on it; the
remediation is another project's.

---

## 8. TICKETS OPENED THIS SITTING

`am` has no `ticket` verb yet, so these are posted as `kind=note` prefixed `TICKET`.

| id | subject | source |
|---|---|---|
| T-01 | narrative pipeline dead 13 days on llm_memory while ingestion runs — 6 unprocessed sessions, whole multi-client program invisible | §3, measured today |
| T-02 | ledger statuses lag reality — F-01/02/03 `in-flight` but merged; F-04/05/06 `closed` before their branch merged | §4.4 |
| T-03 | `merger.py` `revalued_at` has no test assertion | `claude-review`, pilot board, 13 Aug |
| T-04 | qwen narrative-backend pilot ran off-record 22 Aug and failed its stated pass condition; queued rung needs closing with numbers | §5.4 |
| T-05 | `origin/main` 6 commits behind local; nothing published, release intent unresolved | §5.1 |

---

## 9. STANDING NOTES FOR WHOEVER HOLDS THIS SEAT

- **Verify against the repo, not the board.** Every PM on this product who reported
  from board claims was wrong about something within the hour. Both prior PM sittings
  opened with a correction to the state they inherited.
- **`am status` heartbeats lie about liveness.** Heartbeats advance only when a seat
  calls `am`. Stale ≠ dead, and a live OS process ≠ working. Check `ps`, and check the
  pane.
- **A watcher is not a monitor.** A delivery watcher marks delivery and cannot interrupt
  a running session — a seat can look perfectly subscribed and be deaf for hours. Only an
  in-session monitor over the events directory actually wakes a working agent. I run one
  per board (multiclient, pilot, chairman), 20s poll.
- **Escalations go to chairman**, not to the working board. Milestone density there, not
  working noise. The working room is `llm-memory-multiclient`.
- **The convener builds the graph once**, at a pinned SHA, into the job's runtime dir,
  and cites it in the assignment. Arms may rebuild to verify; comparative numbers cite
  the shared build.
- **Fresh checkout is not a fresh environment.** This repo's own `.venv` holds mcp 2.0.0
  and cannot collect the suite. Any test count stated without naming the environment is
  not a claim about the commit. Build the venv from the declared `requirements.txt`.
- **Never spend metered API without asking Scott on chairman.**

---

## 10. SECOND INCIDENT (~17:58, 24 Aug) and this sitting's recovery

`llm-memory-pm2` relaunched again after the *entire WSL host froze and was
restarted*, killing every tmux-hosted seat/monitor/supervisor across every
project (coordinator's recovery note on `chairman`, `01787559054011511306`).
Distinct from the earlier 12:39 tmux-server-only incident this seat's brief
already referenced. This seat's own presence survived (seat state is
file-based, not process-based), so no rejoin was needed — only a heartbeat.

**What was found dead:** `spec-judge-rev1-claude` on `llm-memory-build` had
died mid-judge (repeated Anthropic API 529s, then the host freeze) with no
verdict posted. No `am supervise` daemon and no board monitor were alive for
`llm-memory-build`. `llm-memory-pipeline` was confirmed already fully
reconciled (`6b47bc5`) — nothing to recover there.

**Lesson learned the hard way:** a raw `tmux new-session` replay of a dead
seat's `launch.sh` is *not* a valid relaunch — it starts a real process, but
`am supervise`'s liveness check reads the pid recorded in
`runtime/launches/<seat>.json`, never sees the new out-of-band pid, and
reaps the seat (killing the tmux session) within one poll interval. The
correct relaunch primitive is `am window --role <role> --mode legacy
--input <pinned-pack> --brief <text>` (add `--launcher-seat llm-memory-pm2`
for anything that will need `am post --done` later) against a clean
worktree — remove the stale one first with `git worktree remove --force` if
the seat's original worktree still exists un-modified. Never hand-roll what
`am window` does.

**Second recurring gap, now named:** a relaunched/new judge seat can be
launched successfully yet still be refused by `am phase gate` with "not an
authorized judge for stage 'judge'" — `plan.judges[stage]` in
`runtime/anon-critique/plan.json` is a *separate* registration from the
launch record, and new re-judge seats are routinely missing from it. This is
distinct from the well-known C1C-R15 `launcher_seat` gap (which blocks `am
post --done` and needs a patch to `runtime/launches/<seat>.json` instead).
They surface at the same moment and look alike; diagnose them separately.
The PM adds the seat to `plan.judges[stage]` — a judge seat must never
self-authorize its own gate. Saved to memory as
`am_judge_seat_gate_authorization`.

**Recovery actions taken, in order:** relaunched `spec-judge-rev1-claude`
correctly via `am window` (after the raw-tmux false start above), restarted
`am supervise --job llm-memory-build`, armed an in-session board monitor.
Patched both `launcher_seat` and `plan.judges.judge` for the seat so it
could complete-stamp and record its gate.

**Result — the re-judge cleared, then failed narrowly, then passed:**
`spec-judge-rev1-claude` FAILed the revision with one blocking defect (C5-a
— the C5 fix's `_write_decision_link` confirm path was a no-op: a
`U1_PARTIAL` review could never promote to a whole edge, so the child never
archived and every confirm minted a fresh unbounded-growth open review,
which also blinded the C6 staleness alarm) plus three non-blocking fold-ins
(N2 stale section cross-ref, N3 test-calibration note, N4 wording
inconsistency). PM built a tail-capped evidence pack (`am pack build`)
scoped to exactly those four items, launched `spec-converge-rev2-claude`
(sonnet), which delivered `SPEC-rev2-certification-cascade.md`. PM built a
second tail-capped pack and launched `spec-judge-rev2-claude` (opus),
pre-registered in `plan.json` this time. **Verdict: PASS** —
`VERDICT-rev2-spec-certification-cascade.md`. All four items verified
applied and correct by simulation against the code (not the prose); a
227-line/10-hunk diff confirmed nothing else moved. One new non-blocking
corner found (N5 — a U2/U3/U4 confirmation can also promote a partial edge
because the promotion guard doesn't check `proposed_test`; one-clause fix
given, handed to test/code as an explicit open decision, not silently
closed).

**Design → spec → judge is DONE for Tier-1** (1a certification + cascade
rule). `SPEC-rev2-certification-cascade.md` is the frozen input to `test`.
Next stage: test-author (frozen tests, author ≠ implementer), scoped to the
judge's 16-item load-bearing list (rev1 §10's ten items carried forward +
six amended/added this round) and the still-open items (§16.1 six ids,
§16.2 shingle + token-nesting calibration, §16.3 `narrative_score.py`
inflation — none may be silently invented or closed). Not yet started as of
this note.

---

## 11. THE TIER-1 BUILD (overnight 24→25 Aug) — `llm-memory-pm2`, plain-process incarnation

**Result: Tier 1 (#1 cascade rule + #1a mechanical certification) is BUILT and
PASSED its merge gate.** Suite on the integrated base: **1 failed, 361 passed,
1 skipped in ~12s**; the one failure is `test_suggestions_do_decay`, a
pre-existing frozen-NOW fixture excluded by name. At the code-stage fork it was
25 failures. Nothing committed, nothing pushed — `origin/main` still `e3ae2a7`,
15 commits behind, and the whole night's work is uncommitted in the working
tree. **That is the first thing the next PM should resolve with Scott.**

### 11.1 What ran

Six seats, all via `am window`, author ≠ coder ≠ judge held end to end:

| seat | scope | outcome |
|---|---|---|
| `code-root-claude` | §15 1-2: `archive_class`, `claim_match` | 20 rows; left 2 red as out-of-scope |
| `cert-claude` | §15 3: `certify.py` + renderer wire | 21/21; raised 2 objections + a new time bomb |
| `merger-claude` | §15 4/5/7 + §8.2 | 9 rows + proved non-regression by A/B |
| `score-claude` | §15 9: `narrative_score` | 4/4 |
| `cascade-claude` | §15 6/8: `cascade.py`, §8.1 wire, resolver tools | 20/20 + the §8.1 row |
| `merge-gate-judge-claude` | the gate | **PASS**, + found F-1 |

**No seat edited a frozen test.** All 8 hashes + both fixtures verified at every
landing and again by the judge. Every coder left at least one row RED rather
than reach outside its scope, and raised objections instead of quietly editing
another seat's file. That behaviour — not the green suite — is the result of
this drill.

### 11.2 The perf work, and why it was resequenced

Certification cost **47.25s** per `/narrative` render on the live ledger: a new
parent×child cross-product (6,732 fuzzy comparisons), each an O(n·m)
pure-Python DP. I initially reported this to Scott as constrained by a frozen
test (`test_u3_live_corpus_substring_facts` asserts `max(scores) == 59`) and
sequenced the fix after the gate. **That framing was wrong and Scott called it:**
the test pins the function's *exactness*, never its *speed*. Replaced the DP
with a memoized suffix automaton — **47.25s → 0.55s (86×)**, suite 208s → 12s.
Proven exact against the original DP over 4,000 randomized adversarial pairs,
all edge cases, and all 2,970 live-corpus pairs; the gate judge then
independently re-derived it with its own reference DP over 6,009 pairs. 0
mismatches both times.

Lookup paths were never affected and were measured, because Scott asked
directly: `memory_search` 2.9-26.4 ms, `project_lookup` 4.6 ms, `resume` 2.7 ms,
`narrative_coverage` 642 ms, session-start injection 0.8 ms. The SessionStart
hook READS the rendered `.narrative.md`; it does not render.

### 11.3 Open follow-ups — routed, not lost

- **F-1 (the gate's find, and the one nobody else saw).** C5-a's in-place
  `scope` promotion does not survive replication: `inbox_merge` keys
  decision_links identity on `(decision_id, relation, scope)`, and a promotion
  *changes* `scope`, so a promoted edge syncing to another machine APPENDS
  instead of matching. Record becomes permanently self-contradictory for that
  pair; second-order, `_write_decision_link` then promotes the stale partial
  too, yielding two `whole` entries. **No existing test can see it** — the code
  is green honestly, not by evasion. Only reachable via `inbox_merge`, i.e. the
  multi-machine sync path, which is this project's premise. `link-identity-claude`
  is on it, writing the tests too (author≠coder replaced with a
  RED-before-GREEN evidence requirement).
- **F-2 (low).** `_write_decision_link` matches on `decision_id` alone while
  every other reader also matches `relation`. Unreachable today; same seat.
- **§8.4 errno amendment.** The guard reads EINVAL/ENOTSUP/EOPNOTSUPP off `os`;
  they live in `errno`, so the tuple is `(None, None, None)` and any dirfd-fsync
  OSError re-raises — a false merge failure on non-ext4. One token. merger-claude
  implemented the frozen buggy form verbatim and raised it, which the judge
  upheld as correct conduct.
- **Certification is NOT yet wired into the live `/narrative` path.** Perf bar
  (under 1s) is met, so nothing blocks it now except a decision to do it.

### 11.4 Release track — ran in parallel, and it found the real blocker

`release-prep-claude` then `installer-fix-claude` ran the **first fresh-install
verification in this project's history**. It did not work. Four defects, each of
which killed the installer *silently* under `set -e` with stderr discarded:

1. `install.sh` called `from server import init_db` — a function that exists
   nowhere in the repo. Verified before deletion that `indexer.py` genuinely
   covers DB init (it is the only schema definition in the shipped tree).
2. `_gh_token()` returned non-zero with no token configured — the default state
   of a fresh machine — killing the install at step 2 after only its banner.
3. The `lib/` package was never deployed, so the deployed tree could not import.
   Found independently by `merger-claude` from the coder track.
4. **`hooks/install_hooks.sh` opened its embedded Python with `python3 -c "` —
   a double-quoted bash string — and the Python source contains ``` `claude
   --resume` ``` inside a COMMENT. Bash command-substituted it. Our installer
   has been launching Claude Code mid-install on every machine with the CLI on
   PATH.** It never failed the install, which is why it survived. Fixed with a
   quoted heredoc, retiring the class rather than the instance. The COO swept
   the org: ours was the only live instance.

A fresh install now runs to completion and the deployed tree imports. Also
landed: `settings.yaml` portable + `apply_settings.py` expanding `~` and failing
loudly, MCP list cut to the 4 real tools (verified against `server.py`'s
`list_tools()`), `docs/FEEDBACK.md` scrubbed, `.agent-messages/` gitignored.

**Still blocking publication:** `examples/claude-rules-{full,minimal}.md`
document all 7 retired tools (needs its own slice — it is authorship, not a list
swap); the dead `memory_store` PostToolUse matcher in four files; and Scott's
go. Note the one thing no scrub reaches: every commit is authored
`scott <scott@fletchcorp.com>`.

### 11.5 Process findings worth carrying

- **A merge-gate judge cannot be handed a worktree.** `am window --role judge`
  creates a worktree at HEAD, which for a merge gate contains none of the
  delivered work — judging it would certify an empty tree. This judge worked it
  out itself and judged the shared base. Point the next one at the base
  explicitly.
- **`am sync --monitor` is unsafe for a PM.** It is cursor-based, and a PM also
  runs plain `am sync` by hand; both advance the same per-seat cursor, so the
  monitor and the PM's own reads race and wake events get consumed silently. It
  cost me two hours — three seats posted completions addressed to me and I woke
  for none of them. Replaced with a directory watcher
  (`/home/scott/.am-host/board-watch.sh`), verified by injecting a probe event
  and confirming it reached the session. Filed org-wide as T-F37 (P0).
  **Verification standard, now in the PM onboarding packet: do not accept "a
  monitor is armed" — name an event addressed to the seat and confirm it woke.**
- **`am window` leaves `produces`/`vendor` null on EVERY role**, not just
  judge/test. Patch the launch record immediately after launch; a finished seat
  is idle and cannot consume the fix. Coders correctly refuse to stamp their own
  completion metadata.
- **Do not withdraw work from a running seat without checking whether it has
  started.** I did fix-up A myself while already in `claim_match.py` and posted
  an amendment; it arrived after `cascade-claude` had already done it. Merged
  by hand (its comments were better than mine in all three files).
- **All test runs now serialize through `flock /home/scott/.am-host/test.lock`**
  (host rule, after concurrent suites collided with systemd resource limits).
  `TMUX_TMPDIR` moves inside the flock; it does not go away.
