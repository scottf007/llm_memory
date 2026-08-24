# ROADMAP — llm_memory

Rewritten 2026-08-24 by `llm-memory-pm`, reconciled line-by-line against all 28
ledger rows. Supersedes the earlier draft, which covered one project, cited four
ledger rows, and had no acceptance test.

**The bar, in Scott's words:** *"a correct narrative that lets someone who has
never seen anything make a much better decision"*, at *"the most efficient token
length"* — a ratio, not a ceiling. And it must work **for anyone, on any
client**, not just for Scott on this machine.

---

## THE MEASURE — today is 100. Is a change positive or negative?

Built as `tools/narrative_score.py`, computed from artifacts the pipeline
already writes. **Deliberately not one number.**

**TRUST is a gate, not a tradeable axis. You may not buy token efficiency with
falsehood.**

| | what it counts | why it maps to the goal |
|---|---|---|
| **FALSE** | active items whose parent is archived as superseded | a stranger decides **worse** — told a removed mechanism ships |
| **DIRTY** | % of top-10 search results that are archived | retrieval hands an agent a dead record at live rank |
| **LOST** | `load_bearing` items evicted at the budget boundary | a stranger **misses** something they needed |
| **UNSEEN** | transcripts with real content merged nowhere | knowledge never captured at all |
| **TOKENS** | rendered narrative size | the denominator |
| **reach/1k** | active items ÷ 1k tokens | signal density |

**BASELINE, measured today after the drain and first-ever audit:**

```
TRUST   FALSE = 2        DIRTY = 33.3%
REACH   LOST  = 0        ACTIVE = 253      (LOST was 7 this morning)
COST    TOKENS = 10,730  reach/1k = 23.6
```

**The decision rule.** Any change that raises FALSE or DIRTY is **negative**,
whatever it saves. Among changes that don't, higher reach-per-1k is better.
A change that cuts tokens by evicting load_bearing items is negative even though
the token count improves — that is precisely the trade April's target invited.

*Known weakness, stated so nobody over-trusts it:* FALSE is conservative — it
only catches active items that textually cite a superseded id. The cascade
cross-check running now will give the true figure, and FALSE should be
recalibrated against it rather than believed as-is.

---

## TIER 1 — The memory tells strangers things that are false

One defect in three costumes: **archival is a flag where it should be a state.**
It doesn't reach the item's children, its index entry, or its storage.

**1. Cascade rule.** A sweep isn't a fix — orphans regenerate on the next
supersession. Must cascade on supersession/reversal/contradiction (35 of 73
archived decisions) and **never** on a re-grade (38 of 73, where the work still
stands). Backwards here destroys a true record. *No design candidate proposed a
rule.* Cross-check in flight. **Moves FALSE.**

**2. Retrieval returns dead content at live rank.** 67% of the index is
archived, no default filter; **33.3%** of a fixed 8-query panel's top-10 is
archived, and a superseded decision has been measured outranking its own
replacement. Worst exactly where mechanisms changed — where being wrong costs
most. *Open: filter by default, rank below active, or return separated?*
**Moves DIRTY.** (ledger: new)

**3. Split archives into their own file.** 354 KB of `llm_memory.json`'s 762 KB
is archived (51%). Halves the extractor's input structurally — the cheapest
answer to the cohort's condensation deadlock, which is currently an argument
about which *fields* to drop from a file that is half dead. Removes a
redundancy: archived items already live in `items/` and the FTS index. **Real
work:** `merger.py` does one atomic write; spanning two files needs
write-then-rename or a crash orphans an item. (F-09 rides along: archive-
provenance drift, `[L:N]` refs shift and drop.)

---

## TIER 2 — It can fail silently. This project's defining failure mode.

**4. Nothing makes extraction run.** Ingestion is hooked and automatic;
extraction is a skill a human invokes. Dead 13 days here while ingestion ran
perfectly. **Correction to the earlier draft: file age is NOT the measure.** A
project untouched for a month has a correct old narrative. The measure is
**unprocessed transcripts with real content**. Acceptance: an unprocessed
backlog must be *visible*, with a trigger and a non-trigger control.

**5. `done[]` has no pruning path.** 92 active, dropping 9/render, tightest
budget. `state-auditor` covers decisions and learnings only. **All three design
arms independently excluded `done[]` from the sweep** — so the binding
constraint is managed by neither mechanism.

**6. Rule 14 oscillates.** `dec-eb6ada76` went 0.60 → 0.45 → load_bearing/0.68
in one run. Each extractor sees what was *cut*, never what the previous one just
decided — last writer wins, grade decided by processing order. Counterweight: in
aggregate the loop is self-correcting and moved the over-grade 49% → 36%
unaided.

**7. Coverage numbers cause false alarms, repeatedly.** Raw gaps say 5,344
sessions unmerged; the real figures are 3 (utilityswitch) and 14 (universalai) —
the rest are one-shot agent calls under the turn threshold. **This has now
produced a false panic three times in one day.** `narrative_coverage` should
lead with the filtered number and make the raw count secondary, and no tool
should report a raw gap without its exclusion breakdown. (ledger: new)
Related: **F-10** — the stale mid-session `.md` class, ~10 permanent oracle
failures; and today's live-session exclusion is the same class.

---

## TIER 3 — It only works for Scott, on this machine

The theme the earlier draft missed entirely.

**8. `settings.yaml` ships one person's home directory.** Two permission rules
hardcode `/home/scott/.claude/memory/**`, and `apply_settings.py` does **no
expansion** — so every other user gets a rule naming a directory that doesn't
exist, matching nothing, silently. The comment above it says that without these
rules `/narrative` prompts ~4× and Claude invents `/tmp` workarounds. **So the
headline feature is quietly degraded for everyone who isn't Scott.** It is also
personal information in a repo heading for release. Not a scrub — needs
expansion plus a test. Same file grants **7 retired MCP tools**. (T-06)

**9. `LLM_MEMORY_HOME` — recommended, never built.** **0** references in code;
**42** files hardcode `~/.claude/memory`. `~/.llm-memory` is a symlink: the
cosmetic half only. Must land **before** adapter consolidation (F-08) — two of
those sites are `ARCHIVE_DIR` in both adapters, the exact lines consolidation
would move byte-identical into `base.py`. (F-18)

**10. Only 2 of 6 clients can feed the store.** Present: `claude`, `codex`.
Missing: **grok, opencode, gemini, qwen**. Live corpus: 1,947 claude / 188 codex
/ 5,530 with no `client:` field. Until an adapter exists, a client's work reaches
no narrative. (F-08 consolidation first, per #9.)

**11. The installer wires no non-Claude client**, and **F-19: the rules-line has
never been observed to work on any client.** A documented integration nobody has
seen function is a README claim we cannot support at release. (F-21: the Grok
probe that would settle it is unrun and its runbook was only made executable at
`7b96b22`.)

**11b. The store has a write-permission story for exactly one client.**
`docs/mcp-wiring-recipes.md` wires Codex, Gemini, Grok and qwen — all of it
**read** access to the MCP server. Nothing anywhere covers **write** access to
the store. The only write mechanism in the repo is `settings.yaml` ->
`apply_settings.py` -> `~/.claude/settings.json`, which is Claude-only *and*
hardcodes one person's home (#8).

Surfaced live: a codex seat asked to write one delta to
`~/.claude/memory/deltas/` hung on an approval prompt until Scott fixed it by
hand. It had finished the analysis; it could not deliver it.

**This contradicts Scott's own ruling.** F-22 Q2, verbatim: *"It should be able
to be done easily by any agent."* Today only Claude can run any part of the
pipeline that writes — extraction, audit, cascade. Every other client can query
memory and cannot contribute to it.

Note this is the OURS half of a two-part defect. The other half — `am window`
and `am phase` not granting declared writable paths per vendor at launch — is
agent-messaging's and sits in Tier 5, filed at `6f000df0`. Fixing theirs makes
seats work; fixing ours makes the product work for anyone who installs it.
Interacts with #9: `LLM_MEMORY_HOME` changes where "outside the working
directory" even is, so sequence them together.

**12. F-28 — worktree sessions land in a phantom project.** 49 sessions, 39 MB,
merged nowhere. Fix generically: a project name beginning with `.` is never a
project. Backfill needs no re-extraction, but `agent-messaging` gains 49
unprocessed sessions the moment it lands — fix and drain are one slice.

**13. First-run defects, all ledger rows, all invisible to an existing install:**
F-11 (`memory_wrap` reports a missing project to the model but not the user —
looks like it worked), F-12 (`resume` turns an empty conversation into a
directory-read error), F-13 (dev `.venv` holds mcp 2.0.0 — a contributor's first
`pytest` fails), F-14 (the oracle exits 0 with no corpus — a green run that
checked nothing), F-25 (the `codex-auto` marker is tested on every session, not
only codex ones).

---

## TIER 4 — Release. Gated on Scott's publish call (currently NOT YET).

**14.** `origin/main` at `e3ae2a7`; local **12+ commits** ahead. Nothing published.
**15.** B-1 owner-identifying strings — **DONE** (`b45b077`), verified both ways.
Real names now sourced from a gitignored file with a synthetic always-on control.
**16.** F-26 mcp 2.x port — deferred with a stated trigger, folds in here.
**17.** A fresh install has **never** been verified on another machine. Needs a
clean container and a recorded transcript. (F-20 rides along: transition-update
ordering, the old installer always runs the upgrade.)
**18.** F-16 — the compacted-record render path has no rendered pin.

---

## TIER 5 — Blocked on agent-messaging, not on us. All reported, all worked around.

`am post --kind decision` bricks `am phase` on that job (`1ce054f7`) · launcher
grants no writable paths to non-Claude seats, so a brief can ask for a write the
launch cannot authorise and the seat hangs silently (`6f000df0`) · no
`digest-review` verb (T-F20/B23) · `stage`-then-`launch` deadlocks · launcher
strands non-Claude seats on trust prompts while status reads ACTIVE
(`829b1db9`).

---

## LEDGER RECONCILIATION

**Now scheduled that weren't:** F-08→#10, F-09→#3, F-10→#7, F-11/12/13/14/25→#13,
F-16→#18, F-18→#9, F-19→#11, F-20→#17, F-21→#11, F-26→#16, F-28→#12.

**Proposed CLOSE, pending verification — not closed unilaterally:**
- **F-07** (graph one-liners under-reported) — the S3 delivery it attached to
  merged at `7b96b22`. Verify the datum landed, then close.
- **F-24** (fixture scrub residuals) — today's scrub went wider than the row and
  the residuals it names are gone. Verify against the row's exact wording.

**Still open, deliberately, no action proposed:**
- **F-23** — the `memory_wrap` spawn incident. The seat never accounted for its
  manual commands across three pings and is now dead. The dispute is recorded
  from both sides. It cannot be reconciled without the seat, and inventing a
  conclusion would be worse than leaving it open. Recommend it stays open with
  that stated, rather than being quietly closed.

---

## DECISIONS I NEED FROM SCOTT

**D-A. F-17 — codex and qwen both ship native memory.** Codex has
`~/.codex/memories/`, qwen has `~/.qwen/memories/MEMORY.md`. Wiring llm_memory
in doesn't touch either; a codex user ends up with two memory systems that don't
know about each other and no documented answer for which to write to. The design
note recommends **displace for project knowledge, keep native for client-local
preferences** — never ratified, nothing implements it. This shapes every adapter
in #10, so it wants answering before that slice, not during.

**D-B. F-22 Q1 — is it optics or capability?** Does "a generic memory folder"
mean the path must stop saying `.claude`, or just that non-Claude agents must be
able to use it? Your 17 Aug ruling ("only the variable is in the application,
not the vendor name") reads as **capability + env var**, which is what #9
assumes. Confirm and F-22's Q1 closes; the held Q3 was never asked and can close
with it.

**D-C. Retrieval default (#2).** Filter archived unless asked, rank below
active, or return clearly separated? Affects every agent consuming
`project_lookup`, including me.

---

## IN FLIGHT

`cascade-codex` (feeds #1, recalibrates FALSE) · `crit-claude` / `crit-codex` /
`crit-grok` (feed #3, #5, #6).

## ORDER

Tier 1 → #4 → #7 → Tier 3 → Tier 4 → publish.
Tier 1 first: it is the only tier where the product currently tells strangers
things that are false. Everything else is incompleteness, which is a smaller sin
than misinformation.
