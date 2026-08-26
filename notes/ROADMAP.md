# ROADMAP — llm_memory (FINAL)

2026-08-24, `llm-memory-pm`. Reconciled against all 28 ledger rows, the design
cohort, the critique cohort, and the cascade cross-check. Every status measured.

**Updated 2026-08-25 02:20 by `llm-memory-pm2`** — overnight run. Changed:
Tier 1 status (BUILT, gate running), the perf note under THE MEASURE (47s → 0.55s),
Tier 3 #8 (done) + new #8a, Tier 4 #14/#15/#17. Everything else is untouched and
still `llm-memory-pm`'s. Nothing pushed; `origin/main` unchanged.

**Three decisions still waiting on Scott** — unchanged, and D-C is the one that
blocks the next item of work: **D-C** retrieval default (filter / rank below /
separate) blocks Tier 1 #2 · **D-A** F-17 native-memory displacement · **D-B**
F-22 Q1 confirm. Plus the standing one: publish, or stay local.

**The bar (Scott):** *"a correct narrative that lets someone who has never seen
anything make a much better decision"* at *"the most efficient token length"* —
a ratio, not a ceiling. Working **for anyone, on any client**.

---

## THE MEASURE — `tools/narrative_score.py`. Today = baseline.

**TRUST is a gate, not a tradeable axis. You may not buy tokens with falsehood.**

```
TRUST   FALSE=3   DIRTY=33.3% of top-10 search results archived
REACH   LOST=0    (was 7 this morning)      ACTIVE=252
COST    TOKENS=10,759   reach/1k=23.4
```

Any change raising FALSE or DIRTY is **negative**, whatever it saves.
Otherwise higher reach/1k wins.

**Perf, resolved 25 Aug (was going to block the wiring).** Certification cost
**47.25s** per `/narrative` render on the live ledger — a new parent×child
cross-product (36 × 187 = 6,732 fuzzy comparisons), each an O(n·m) pure-Python
DP. Owner called it: bad algorithm, not natural cost. Replaced with a memoized
suffix automaton, **47.25s → 0.55s (86×)**; the test suite came down 208s → 12s
with it. Proven exact against the original DP before landing — 4,000 randomized
adversarial pairs, all edge cases, and all 2,970 live-corpus pairs, 0
mismatches. Lookup paths were never affected and were measured: `memory_search`
2.9–26.4 ms, `project_lookup` 4.6 ms, `resume` 2.7 ms, session-start injection
0.8 ms. **Bar for wiring certification into `/narrative`: under 1 second. Met.**

**Known weakness, do not over-trust:** FALSE only catches active items that
textually cite a superseded id — archiving *more* can raise it (it went 2→3 when
the cascade fix landed). All three critique arms independently said the same
thing about `contested.json`: **it sees only the budget boundary, never the
interior of the kept set.** Both instruments are necessary and neither is
sufficient. **#1a below is the fix.**

---

## PROVEN TODAY: the product was rendering a self-contradiction

The narrative contained, simultaneously:
- *"The unconditional 'load_bearing always renders' exemption is gone"*
- *"load_bearing items always render in full"*

Both live, to any stranger. Found independently by all three critique arms and
the cascade seat. Now fixed. **This is the failure class the whole Tier 1 exists
to eliminate, and it was invisible to every metric we had.**

---

## TIER 1 — The memory tells strangers things that are false

One defect, three costumes: **archival is a flag where it should be a state.**

**Status (25 Aug 02:15, `llm-memory-pm2`): BUILT. Merge gate running.**
The full chain ran overnight: design → spec (`SPEC-rev2-certification-cascade.md`)
→ lock-audit judge PASS → 81 frozen tests with RED proof → code → gate.

Suite on the integrated base: **1 failed, 361 passed, 1 skipped (12.3s)**. The
one failure is `test_suggestions_do_decay`, a pre-existing frozen-NOW fixture
excluded by name — not ours. At the code-stage fork it was 25 failures.

Five coder seats, disjoint files, author ≠ coder ≠ judge held throughout:
`code-root` (archive_class + claim_match) · `cert` (certify + renderer wire) ·
`merger` (M2/M3 schema, atomic write, inbox_merge, §8.2) · `score`
(narrative_score) · `cascade` (cascade.py, §8.1 wire, resolver tools).
**No seat edited a frozen test.** All 8 hashes + both fixtures verified
unchanged at every landing — which was the point of the drill.

Three items were raised rather than decided unilaterally and are with the gate
judge: cert's `_ID_TOKEN_RE` widening (the spec's hex-only regex cannot match
its own frozen fixtures), §8.4's directory-fsync errno guard being a measured
no-op (constants read off `os`, but they live in `errno`), and cascade's N5
ruling (adopted the guard: `item_fingerprint` structurally cannot see
`decision_links`, and cascade is monotone — a wrong archive has no recovery).

Still open, not built: **certification is NOT yet wired into the live
`/narrative` path.** See the perf note under THE MEASURE. See
`notes/PM-STATE.md` §10 for the full chain.

**1. Cascade rule.** Cross-check done: 92 active `done` items vs 73 archived
decisions → **2 orphans found and archived**; 6 flagged ambiguous and correctly
left active; 1 duplicate pair named not archived. All 38 re-graded parents
correctly excluded. **All three critique arms independently agree: cascade only
on supersession/reversal/contradiction, never on a re-grade.** A sweep is not a
fix — the *rule* is still unbuilt, and no design candidate proposed one.

**1a. A kept-set currency check.** The gap all three arms found: `contested.json`
only sees what was *cut*. `dec-e60b5979` stayed active, load_bearing, describing
a reversed rule, through every metric we had — caught only by the first-ever
sweep. **Zero rendered misinformation is the R1 acceptance test.** Needs a
per-render check at zero LLM cost (crit-codex's "mechanical certification":
active contradictions, archive cascades, omission reasons, overdue jobs).

**2. Retrieval returns dead content at live rank.** 67% of the index archived,
no default filter; **33.3%** of an 8-query panel's top-10; a superseded decision
measured outranking its own replacement. *Decision needed: filter by default,
rank below, or return separated?* **(D-C)**

**3. Split archives into their own file.** 354 KB of 762 KB (51%) is archived —
half of what the extractor reads every session. Cheapest answer to the cohort's
condensation deadlock. Removes a redundancy (`items/` + FTS already hold them).
**Real work:** `merger.py` does one atomic write; two files need
write-then-rename. (F-09 rides along.)

---

## TIER 2 — It can fail silently. The defining failure mode.

**4. Nothing makes extraction run.** Dead 13 days here while ingestion ran
perfectly. **File age is NOT the measure** — a dormant project's old narrative is
correct. The measure is **unprocessed transcripts with real content**.

**5. `done[]` has no pruning path. All three candidates failed this and all
three critiques said so.** 92 active, tightest budget, excluded from the sweep
by every candidate. Needs the archive cascade (#1) *plus* a population
reconciler. crit-grok: *"renderer dissolve is not honesty — the lie sits in
Foundations, above the cut."*

**6. Rule 14 oscillates — and the fix is now known.** Last-writer-wins must die:
**store observations and merge them commutatively** (crit-codex), which
crit-grok independently calls "the accidental oscillation fix". Order-dependence
disappears rather than being damped.

**6a. CORRECTION — my objection to the cohort was wrong.** I argued the drain
proved the per-session loop works, undercutting the move to a periodic sweep.
crit-claude checked attribution directly: Rule 14 (revaluation) and Pass B
(archival) are **decoupled mechanisms on different timescales** — one session did
17 revaluations and 0 archives; the two sweeps did 36 and 15 archives against
ordinary sessions' 1–5. **The drain evidence supports the seam, not against it.**
The seam survives all three critiques; the *justification* and the `done[]`
exclusion do not.

**7. Coverage numbers cause false alarms.** Raw gaps said 5,344 unmerged; real
figures are 3 and 14. **Three false panics in one day.** `narrative_coverage`
must lead with the filtered number; no tool should report a raw gap without its
exclusion breakdown. (F-10 rides along.)

---

## TIER 3 — It only works for Scott, on this machine

**8. `settings.yaml` ships one person's home directory. — DONE 25 Aug.** Now
`Write(~/.claude/memory/**)`, with `apply_settings.py` expanding `~` and failing
LOUDLY (exit 1) instead of silently producing broken permissions. MCP list cut
from 10 entries to the 4 real tools — verified against `server.py`'s
`list_tools()`, not against any document. `install.sh` no longer swallows that
call's stderr or ignores its exit code. (T-06)

**8a. STILL OPEN, same class, found 25 Aug:** `examples/claude-rules-{full,
minimal}.md` document all **7 retired tools**, including *"include the project
name in every `memory_store` call"* — a new user pastes that into their
CLAUDE.md and Claude then calls tools that do not exist. Deliberately not fixed:
the fix is rewriting a memory protocol around four read-only tools, which is
authorship, not a correction. **Needs its own slice.** Mitigating, and checked
rather than assumed: `claude-rules-example.md`, the file `install.sh` actually
deploys, is already correct. Only the unshipped examples rot. Also: the
`mcp__llm_memory__memory_store` PostToolUse matcher is dead in four files, so
`/tmp/llm_memory_last_save` is never written and `session_monitor.sh`'s
staleness check runs against a file that never updates.

**9. `LLM_MEMORY_HOME` — 0 references, 42 hardcoded files.** `~/.llm-memory` is
a symlink: the cosmetic half. Must land **before** F-08 consolidation. (F-18)

**10. Only 2 of 6 clients can feed the store** — claude, codex. Missing grok,
opencode, gemini, qwen. (F-08 first, per #9.)

**11. Installer wires no non-Claude client**; **F-19: the rules-line has never
been observed to work on any client.** (F-21 probe unrun.)

**11b. The store's write-permission story is Claude-only.** Recipes wire
Codex/Gemini/Grok/qwen for **read**. Nothing covers **write**. Proven live: a
codex seat finished its analysis and could not deliver it. **Contradicts F-22
Q2:** *"It should be able to be done easily by any agent."* Sequence with #9.

**12. F-28 — worktree sessions land in a phantom project.** 49 sessions, 39 MB,
merged nowhere. Fix generically: a project name starting with `.` is never a
project. Fix + drain are one slice.

**13. First-run defects:** F-11, F-12, F-13, F-14, F-25 — all invisible to an
existing install, all hit a new user first.

---

## TIER 4 — Release. Gated on Scott (currently NOT YET).

**14.** 15 commits unpushed; `origin/main` still at `e3ae2a7`. Nothing pushed,
nothing tagged.
**15.** B-1 identifying strings — **DONE** (`b45b077`); `docs/FEEDBACK.md`
scrubbed 25 Aug. `.agent-messages/` was untracked-but-NOT-gitignored — hundreds
of files of host paths and internal deliberation, one `git add -A` from
publication. Now ignored. **Remaining and NOT text-scrubbable:** every commit in
this repo's history is authored `scott <scott@fletchcorp.com>`. Fixing it means a
history rewrite. Owner's call — for a personal project under your own name it may
be exactly right.
**16.** F-26 mcp 2.x port (deferred, trigger stated).
**17. Fresh install — VERIFIED FOR THE FIRST TIME, 25 Aug. It did not work.**
Three defects, each of which killed the installer *silently* under `set -e` with
stderr discarded: (a) `install.sh` called `from server import init_db` — a
function that does not exist anywhere in the repo; (b) `_gh_token()` returned
non-zero when no token was configured, which is the DEFAULT state of a fresh
machine, killing the install at step 2 after printing only its banner; (c) the
`lib/` package was never deployed at all, so the deployed tree could not import.
All three fixed. **(d) The one that matters most: `hooks/install_hooks.sh` opened
its embedded Python with `python3 -c "` — a double-quoted bash string — and the
Python source contains ``` `claude --resume` ``` inside a COMMENT. Bash
command-substituted it. Our installer has been launching Claude Code mid-install
on every machine with the CLI on PATH.** It never failed the install, which is
why it survived. Fixed with a quoted heredoc, which retires the class rather than
the instance. A fresh install now runs to completion and the deployed tree
imports. (F-20 closed.)
**18.** F-16 compacted-record render path has no rendered pin.

## TIER 5 — agent-messaging, reported and worked around

`--kind decision` bricks `am phase` (`1ce054f7`) · launcher grants no writable
paths to non-Claude seats (`6f000df0`) · no `digest-review` verb · stage/launch
deadlock · trust-prompt stranding (`829b1db9`).

---

## LEDGER

**Scheduled:** F-08→10, F-09→3, F-10→7, F-11/12/13/14/25→13, F-16→18, F-18→9,
F-19/21→11, F-20→17, F-26→16, F-28→12.
**Propose CLOSE (verify first, not closed unilaterally):** F-07, F-24.
**Stays open deliberately:** F-23 — the spawn incident cannot be reconciled
without a dead seat; inventing a conclusion is worse than leaving it open.

## DECISIONS I NEED

**D-A. F-17** — codex and qwen ship native memory. Displace for project
knowledge, keep native for client-local prefs? Never ratified. Shapes every
adapter in #10.
**D-B. F-22 Q1** — optics or capability? Your 17 Aug ruling reads as capability
+ env var, which #9 assumes. Confirm and Q1/Q3 close.
**D-C. #2** — retrieval default: filter, rank below, or separate?

## ORDER

**1a → 1 → 2 → 3 → 6 → 5 → 4 → 7 → Tier 3 → Tier 4 → publish.**
1a first: it is the acceptance test for everything above it, and today proved we
cannot see the failure without it.
