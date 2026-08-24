# ROADMAP — llm_memory (FINAL)

2026-08-24, `llm-memory-pm`. Reconciled against all 28 ledger rows, the design
cohort, the critique cohort, and the cascade cross-check. Every status measured.

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

**8. `settings.yaml` ships one person's home directory.** Hardcodes
`/home/scott/.claude/memory/**`; `apply_settings.py` does no expansion — so every
other user gets a rule matching nothing, silently, degrading `/narrative`.
Personal information in a repo heading for release. Also grants 7 retired MCP
tools. (T-06)

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

**14.** 14+ commits unpushed; `origin/main` at `e3ae2a7`.
**15.** B-1 identifying strings — **DONE** (`b45b077`).
**16.** F-26 mcp 2.x port (deferred, trigger stated).
**17.** Fresh install **never** verified on another machine. (F-20 rides along.)
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
