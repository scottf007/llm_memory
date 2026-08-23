# FEEDBACK — llm_memory's living defect and observation ledger

Compiled 2026-08-17 by seat `s3-judge`, job `llm-memory-multiclient`
(assignment `fba61413`, admission heuristic `a4272dca`). Sources: all 53 events
in the job record, the S1/S2/S3 judge verdicts, the multi-client design note
(`5a1c58b2`), and the transition-update incident (`512d16a6`).

Shape mirrors universalai's `notes/FEEDBACK.md` at `53c095a`.

---

## THE STANDING RULE

**Every judge verdict's non-blocking observations, every incident post, and
every deferred item gets appended here by the seat that produced it, in the
same sitting.** Not "when there is time" — findings that live only in a board
post are findings that get re-discovered by the next seat at full cost.

**This ledger is a required input to every design brief and every slice cut on
this product.** A brief that has not been checked against it is a brief that
will propose work already known to be blocked, or re-litigate a rejection that
already has evidence behind it.

---

## THE ADMISSION HEURISTIC

Per Scott, via `a4272dca`: *"obvious holes yes, not every suggestion or
problem."* This **replaces** a blanket intake rule. A row earns admission only
if **all four** hold:

1. **Evidenced** — it demonstrably bit (reproduced, measured, incident) **or**
   carries a named, checkable trigger condition under which it will bite.
2. **Actionable** — a specific change someone could make, not a vibe.
3. **Consequential** — it blocks work, corrupts output, loses data, spends
   money, or silently degrades.
4. **Not covered** — no existing test guards it, and no in-flight slice already
   owns it (in-flight work gets a status row; deferred work needs a stated
   reopen condition).

Explicitly excluded: style and taste suggestions with no failure mode;
hypotheticals with no concrete trigger; one-off environment quirks with no
recurrence path; anything a merged test already pins.

The custodian applies the filter on intake and **may reject a submission with
one line saying which criterion failed**. A rejected row costs nothing; an
admitted junk row costs every future reader. Rejections from this compilation
are recorded in [Compilation notes](#compilation-notes) rather than discarded
silently.

---

## HOW TO READ THIS

| status | meaning |
|---|---|
| **open** | Confirmed, nobody is working on it. |
| **in-flight** | A slice is actively carrying it. Branch or assignment named in the row. |
| **deferred** | Deliberately not doing it. The **condition** that would reopen it is stated. A deferred row without a condition is a bug in this ledger. |
| **closed** | Fixed and merged. Kept only where re-opening is a live risk. |

`id` is stable — cite `F-05`, not "the exec bit thing". New rows take the next
free number and are **never renumbered**.

---

## INDEX

| id | finding | status |
|---|---|---|
| F-01 | `min_user_turns` — codex ingest works, codex work does not reach the narrative | closed |
| F-02 | renderer short-id collapse — every codex session renders as `codex-01` | closed |
| F-03 | fixture project names in a public repo — an owner ruling was reversed | closed |
| F-04 | the Gemini wiring recipe does the opposite of what it claims | closed |
| F-05 | exec bit invisible to git under `core.fileMode=false` — 8 tests fail from a clean checkout | closed |
| F-06 | `install.sh` ships `tools/*.py` only, so the wrapper never reaches the installed lib | closed |
| F-07 | graph one-liners under-reported; `render` collides with two existing symbols | in-flight |
| F-08 | adapter boilerplate duplication claude/codex | open |
| F-09 | archive-provenance drift (`08d89c12`) — `[L:N]` refs shift and drop | open |
| F-10 | the stale mid-session `.md` class — a permanent ~10 oracle failures | open |
| F-11 | `memory_wrap` reports a missing project to the model but not to the user | open |
| F-12 | `resume` turns an empty `conversation_md` into a directory-read error | open |
| F-13 | the dev `.venv` holds a foreign package named `mcp` | open |
| F-14 | the standalone oracle script exits 0 with no corpus | open |
| F-15 | nothing downstream reads `client:` yet | open |
| F-16 | the compacted-record render path has no rendered pin | open |
| F-17 | D12 — codex and qwen both ship native memory; displacement undecided | open |
| F-18 | D2 — `LLM_MEMORY_HOME` recommended, never built; zero env overrides exist | open |
| F-19 | the rules line has never been observed to work on any client | open |
| F-20 | transition-update ordering — the old installer always runs the upgrade | deferred |
| F-21 | Grok `UserPromptSubmit` probe unrun, and its runbook is not executable | deferred |
| F-22 | Q1 and Q3 to Scott, unanswered (Q2 closed at `78f45a48`) | deferred |
| F-23 | the `memory_wrap` real-client spawn incident | open |
| F-24 | the fixture scrub's two residuals — a pseudonym, and a shape guard that cannot see a bare name | open |
| F-25 | the `codex-auto` marker is tested on every session, not only codex ones | open |
| F-26 | the `mcp<2` pin bought time on escalation `09ea13cb`, it did not fix `server.py`'s mcp-2.x incompatibility | deferred |
| F-27 | the narrative-on-qwen pilot ran off-record and failed its own pass condition | closed |
| F-28 | worktree sessions attribute to a phantom project — 49 sessions ingested, merged nowhere | open |

---

## A. MERGED — the S3 revision and the post-ingest slice

**Status corrected 2026-08-23 by `llm-memory-pm`.** Every row in this section
except F-07 is now closed on `main`, and this section is kept under its original
number because ids are never renumbered.

- **F-01, F-02, F-03** merged in `a996002` on 17 August (seat `post-ingest`,
  assignment `6a853f48`), gated PASS by `s3-judge` at `97c8a3cb`.
- **F-04, F-05, F-06** merged in `7b96b22` on 23 August (seat `s3-serving`,
  revision assignment `1f25dbd1`), gated PASS twice — `s3-judge` at
  `01786971962654367040` and cross-vendor `glm-arm` (z-ai/GLM lineage) at
  `01786973651112959419`, which reproduced all seven items independently.
- **F-07** remains in-flight; it is the only row in this section still open.

**Why this correction was needed, recorded because the failure is instructive.**
This ledger marked F-01..F-03 `in-flight` for six days after they merged, and
marked F-04..F-06 `closed` on a branch that had not merged yet — so it was
simultaneously behind reality and ahead of it. The rule at the top of this
section ("a row leaves this section when the fix **merges**") was correct and
was not applied, because the custodian who wrote the statuses was gating the
branch rather than merging it, and nobody held the file afterwards. A ledger
that is a required input to every brief will send the next brief at work already
done. The structural fix is not a better rule: it is that whoever merges updates
the row in the same sitting, which is what happened here.

### F-01 — `min_user_turns`: "codex ingest works" and "codex work reaches the narrative" are different claims
*source: `c237eb21`, judge `07cf16e8`, contract amended at `6a853f48` · status: closed at `a996002` · blocks: whether S2 delivers anything Scott can see*

Only **8 of 124** codex envelopes clear the default threshold of 5 substantive
user turns — measured twice independently, by the adapter's own count and by
loading the real `server.py` from merged main. Codex `exec` runs are
single-prompt by nature. Codex sessions are ingested, readable, and attached to
no narrative.

The first contract (a flat `codex: 1`) was **amended before implementation**,
after an owner-commissioned analysis of the actual 124 conversations:

- 68% are the `codex-auto` board-polling harness — 57 pure `NO_REPLY`, and 27
  whose replies are posted to the board *verbatim, by construction*, so
  extracting them duplicates the board.
- ~40 standalone sessions carry real value, **some recorded nowhere else**
  (design-council rounds whose arguments exist in no other memory).

So a blanket `1` ingests 84 noise sessions and a flat `5` discards real signal.
The amended contract is three parts: a structural pre-filter on the `codex-auto`
prompt pattern (archive, never extract), per-client `min_user_turns` with
`codex: 1` for the remainder, and a ~50-char minimum assistant-content gate to
kill `PONG`/`exit`/bare-id sessions.

**The durable half, which outlives the fix:** the answer is not a number. The
next client will face the same question, and the method is "characterise the
corpus first, then pick per-client."

**Delivered at `774e859`, gated at `97c8a3cb`, and the corpus characterisation
above was independently re-measured rather than taken on trust.** Judge ran the
real `_handle_narrative_coverage` at both SHAs against the real
`~/.claude/memory`, five real projects: **0 sessions lost, +24 gained**
(agent-messaging 6 → 29, fletchcorp 0 → 1, the other three unchanged). Codex
admission goes **8 → 35 of 124**. Every one of the 7 content-gate casualties was
inspected individually and is `exit`, `PONG`, a bare event-id ping, or a session
with zero assistant records.

The load-bearing claim — that the `codex-auto` harness's replies are "posted to
the board verbatim, by construction", so archiving without extracting loses
nothing — **was tested against the board rather than argued.** Of the 84
excluded sessions, 28 carry ≥200 characters of assistant prose; **27 of those
28 were found verbatim** in agent-messaging's 1075 board event files. The single
miss is a 203-character refusal to act on an assignment addressed to another
seat. The "27 verbatim" figure in the amended contract is therefore confirmed
by a second, independent method.

**Stale doc, noted here rather than given its own row:** `docs/adapters.md:121-131`
still reads "`min_user_turns` is the real gate", "the threshold is a parameter",
and "**8** clear the default `min_user_turns=5`". That section is the analysis
that motivated this fix and now describes pre-fix behaviour. The 8 is still
exactly right as history — it reproduces — but a reader arrives at a document
arguing for work already done. `adapters.md` was untouched by the fix branch.

### F-02 — renderer short-id collapse: every codex session renders as `codex-01`
*source: `0bdcb0c2` · status: closed at `a996002` · blocks: human-readable provenance in narratives*

`renderer.py:618` and `:641` print `session_id[:8]`. `codex-` is six of those
eight characters and every codex thread id begins `019`, so **every** codex
session renders as `codex-01` in both places a human reads session ids. Claude
ids still show 8 hex characters and stay distinct.

Reported, not fixed, by the seat that caused it — `renderer.py` was out of its
scope and the fix is a rendering decision. The assigned fix strips a known
client prefix and shows it separately, which keeps provenance visible; the
alternative (take the *last* eight characters) loses it.

**Delivered at `a996002`, gated at `97c8a3cb`.** `_display_session_id()` reuses
the pre-existing `adapters.prefixes()` rather than duplicating the prefix
table — a genuine GRAPH-HIT, and the second data point in F-07's tally. The
trigger control was verified the only way that means anything: the five new
renderer tests were run against **pre-fix** `renderer.py`, where four fail and
the one that passes is `test_claude_short_ids_in_source_transcripts_are_unchanged`,
the non-trigger control. `test_two_codex_sessions_render_with_distinct_short_ids`
does fail against the old `codex-01` collision.

### F-03 — fixture project names in a public repo: an owner ruling was reversed
*source: judge `07cf16e8` raised it; Scott ruled KEEP at `3e91071d`; COO reversed at `bee6b19e` · status: closed at `a996002` · blocks: publishing the repo*

Three real project names survive the fixture sanitiser **by design**, because
the fixtures pin project derivation from `cwd`:
`/home/user/projects/universalai` (189 records), `agent-messaging` (36),
`fletchcorp` (13). None appears anywhere in the repo at merged main, so S2
newly introduces them, and `fletchcorp` matches Scott's own email domain — it
is identifying in a way `universalai` is not.

Everything else in the sanitiser holds under direct attack: 1.8 MB across 30
files, zero occurrences of `scott`/`Scott`/`/home/scott`, zero credentials, and
a prose hunt over every string **and dictionary key** longer than 25 characters
returned exactly two hits, both codex's own schema key names.

**The disagreement is recorded rather than smoothed over,** per this job's
policy. Scott ruled KEEP at 03:52 ("directory names of largely public repos;
sanitizer exceptions signed off"). The COO reversed it at 03:59 — placeholders
like every other string, plus a depth-walk extension so a real project name can
never ship again. The reversal is the operative instruction and is being
implemented. **This row does not close on the fix merging.** It closes when
Scott confirms he accepts the reversal of his own ruling.

**The fix is delivered and has passed its gate. The row still does not close.**
`aa59930` on `claude/post-ingest` maps the `cwd` project component through a
deterministic digest placeholder; judged at `97c8a3cb` from a fresh checkout of
`a996002` — all three names gone, both guards carrying real trigger and
non-trigger controls, fixtures byte-reproducible. **Five** fixtures leaked and
were regenerated, not four (01 and 03 agent-messaging, 04 and 09 universalai,
10 fletchcorp); the delivery's "4 affected fixtures" counts the four
`.expected.md` files that changed, and `01-compacted`'s `.expected.md` — which
does exist — is the `skipped: true` subagent stub with no `project:` line, so
regeneration reproduced it byte for byte.

**The confirmation is still not in the record, and that is the whole point of
this row.** The delivery at `9dec4d6c` reports that Scott confirmed in-window,
this sitting, that he personally reversed his own KEEP ruling. Nobody doubts
the seat. But an in-window confirmation to a single session is exactly what
rule `a4565f94` was written to stop counting, and F-P5 says a relayed
instruction is unverified until the record shows it — a standard this ledger
applied to its own compiler and cannot now waive for a convenient answer.
**Needs one line on the board from Scott, or from the COO relaying with
attribution.** Until it exists this row stays open with its fix merged, which
is an ordinary and untroubling state for it to be in. See also F-24 for what
the fix does and does not achieve.

### F-04 — the Gemini wiring recipe does the opposite of what it claims
*source: judge `52e59524`, proven by sandbox execution · status: closed at `7b96b22` · blocks: anyone wiring Gemini*

`docs/mcp-wiring-recipes.md` printed:

```bash
gemini mcp add llm_memory <py> -- <server.py> -s user
```

Run verbatim in a sandbox `HOME`, this reports *"added to **project**
settings"*, writes `./.gemini/settings.json`, and bakes
`args: ["…/server.py", "-s", "user"]` into the server's launch command. `-s
user` lands after `--`, so yargs takes it as a positional. Both halves of the
claim fail — and the doc explicitly says "pass `-s user` (as above) to make it
available everywhere."

Corrected form, verified in the same sandbox — **options before positionals**:

```bash
gemini mcp add -s user llm_memory <py> -- <server.py>
```

The Codex line in the same file *was* correct, and both were "verified against
`--help` output". See F-P2 for the process lesson.

**CLOSED at `7b96b22` (S3 delta-verify, judge `s3-judge`).** The corrected form
was re-run verbatim, not re-read: `gemini mcp add -s user llm_memory
~/.claude/memory/lib/.venv/bin/python3 -- ~/.claude/memory/lib/server.py` in a
sandboxed `HOME` (`live ~/.gemini` never touched — the sandbox `HOME` env var
makes that structurally true, not merely claimed). Result: `MCP server
"llm_memory" added to user settings. (stdio)`; only
`$SANDBOX_HOME/.gemini/settings.json` was written, nothing under the scratch
cwd; the file's `args` array is exactly `["/home/scott/.claude/memory/lib/server.py"]`
— no stray `-s`/`user` baked into argv. Both halves of the original defect are
fixed and both are independently confirmed by execution, not inference.

### F-05 — the exec bit git could not see
*source: judge `52e59524` · status: closed at `7b96b22` · blocks: reproducibility of every test claim on this repo*

`tools/memory_wrap` was committed `100644` while every other shell entrypoint
in the repo (`install.sh`, `dashboard.sh`, all seven `hooks/*.sh`) is `100755`.
From a fresh checkout the suite is **247 passed, 8 failed, 1 skipped** — all
eight wrapper tests dying on `Permission denied` — against a reported 255/1.

Root cause: this repo's shared `.git/config` sets **`core.fileMode = false`**.
The authoring seat's local `chmod +x` was real (its worktree file is genuinely
`0755`) and completely invisible to git. Its "255 passed" was a true statement
about its own worktree and a false one about the commit.

Fix is `git update-index --chmod=+x tools/memory_wrap`; the installer test
extension in F-06 is its regression guard. See F-P1 for the process rule.

**CLOSED at `7b96b22` (S3 delta-verify, judge `s3-judge`).** Reproduced from a
genuinely fresh `git worktree add` of `7b96b22` — not the author's worktree,
not a prior verify worktree — so no local `chmod` could contaminate the
result. `git ls-files -s tools/memory_wrap` reads `100755` in the index, the
checked-out file is `-rwxr-xr-x`, `core.fileMode` is still `false`, and the 8
wrapper tests in `tests/test_memory_wrap.py` pass with no local chmod: `8
passed in 2.04s`. The exec bit survived the rebase onto `7077c0c` exactly as
claimed.

### F-06 — `install.sh` ships `tools/*.py` only
*source: judge `52e59524` · status: closed at `7b96b22` · blocks: the wrapper working outside a repo checkout*

`install.sh:209` is `cp "$EXTRACTED/tools/"*.py "$LIB_DIR/tools/"`. Both new
non-`.py` artifacts — `tools/memory_wrap` (extensionless bash) and
`tools/memory_wrap_clients.json` — are therefore **never installed**. The
installed lib gets `memory_wrap_resume.py`, the helper, with no wrapper to call
it and no client config to read. Every other path the recipes document is a
`~/.claude/memory/lib/...` path; the wrapper alone is repo-checkout-only.

The assigned fix extends the copy and asserts both files land **and are
executable**.

**CLOSED at `7b96b22` (S3 delta-verify, judge `s3-judge`).** From a fresh
checkout + fresh venv built off declared `requirements.txt`:
`test_wrapper_and_config_are_installed` and `test_wrapper_is_executable`
(`tests/test_install.py`) both pass, and both extract and execute the **real**
lines from `install.sh` (`TestToolsInstall._copy_block()` slices the file
between the `mkdir -p "$LIB_DIR/tools"` and `chmod +x
"$LIB_DIR/tools/memory_wrap"` lines) rather than a paraphrase. Confirmed the
guard is not vacuous: run the identical block-extraction logic against
`install.sh` as it stood at `6ab990e` (pre-fix) and it fails to even find the
`chmod +x` boundary — `StopIteration`, because the pre-fix installer has no
such line at all. A test that can only pass because the artifact under test
changed underneath it, and that provably cannot pass against the pre-fix
source, is a live guard.

### F-07 — graph one-liners under-reported, and `render` collides
*source: standing practice `062046a8`, audited at `52e59524` · status: in-flight · blocks: the evidence base for keeping the practice*

The practice: run `bootstrap/uai build` + `find` before creating a new symbol,
post one line per symbol. **The one-liners are the measurement** — they decide
whether the practice earns its keep.

First data point, S3: **1 of 3** new module-level symbols reported.

- `memory_wrap` — reported MISS, and the MISS **is corroborated**: `uai find
  inject` and `uai find prepend` both return zero on a 995-entity /
  4,035-relationship graph. No pre-existing injection helper existed.
- `tools.memory_wrap_resume.render` — **not reported, and it collides by name
  with two existing symbols**: `renderer.render(state: dict)` and
  `adapters.render(ref, client)`. Substantively still MISS-proceed — three
  different jobs, and `renderer.py:589` deliberately emits a *pointer* rather
  than full resume content, so there was nothing to reuse. But this collision
  is precisely what the practice exists to surface.
- `tools.memory_wrap_resume.main` — not reported; trivially new, 12 existing
  modules define `main` by convention.

**Cost datum for the tally:** graph build plus three `find` calls, ~40 seconds,
would have caught the collision.

Second data point, post-ingest reuse: **`adapters.__init__.prefixes` is a real
GRAPH-HIT.** It genuinely pre-exists at `adapters/__init__.py:75-77`, and
`renderer._display_session_id` reuses it instead of duplicating the prefix
table, so "single source of truth, not duplicated" is a checkable claim that
checks out. This is a **better** data point than S3's, because it is the
practice producing the outcome it was created for rather than the practice
catching a near-miss.

Third data point, post-ingest symbols: **0 of 6.** The branch adds six new
production symbols — `renderer._display_session_id`,
`server._first_user_message_text`, `server._is_codex_auto_participant`,
`server._has_substantive_assistant_content`, `server._client_by_session`,
`make_codex_fixtures._placeholder_project`. **None got a posted one-liner.**
The delivery's only graph statement is the `prefixes()` reuse above, which is a
HIT on a pre-existing symbol, not a MISS one-liner for anything new. Judge
checked all six against the convener graph pinned at `a996002` (988 entities /
4,053 relationships): all six are unique, **zero collisions**, so nothing was
missed on substance.

**Fourth data point, the S3 revision itself (`7b96b22`), and the sharpest one
yet.** The `render` collision named in the first data point above is closed in
code and was never reported as closed. `tools.memory_wrap_resume.render` no
longer exists; the revision renamed it to `render_resume_block`, exactly the
name `1f25dbd1` suggested while leaving the final call to the seat. Checked
against the convener graph pinned at `7b96b22` (1038 entities / 4276
relationships): `tools.memory_wrap_resume.render_resume_block` is a clean
GRAPH-MISS with **no** collision, and the only two `render` entities left
anywhere in the graph are the two pre-existing ones — `renderer.render`
(`renderer.py:703-704`) and `adapters.__init__.render`
(`adapters/__init__.py:109-112`). Separately, `tools.memory_wrap_resume.main`
is confirmed trivially new: 14 modules in the graph now define a `.main`
function, 13 of them pre-existing. **Neither one-liner was posted for this
revision either** — the seat fixed the substance the practice exists to catch
and still did not run the practice that would have let it say so. That is the
practice working in the code and failing in the reporting, in the same
commit, which is a sharper data point for whoever decides F-07's future than
either half alone: "under-applied" cannot be read as "not worth keeping" when
the thing it would have surfaced here is a clean bill of health, not a new
defect.

**Running tally: 1 of 3, then 0 of 6, then 0 of 2.** Three consecutive slices
under-applied a practice whose stated purpose is to be measured, and every
time the measurement had to be done by the judge afterwards. That is the
datum the tally exists to produce, and it now points somewhere: the practice
keeps finding real things (the `render` collision, the `prefixes` reuse, and
now the collision's own closure) and keeps not being run by the seat that
owes it. Whoever decides the practice's future should weigh
"under-applied" separately from "not worth keeping" — this record supports the
first and not the second.

One near-neighbour the practice would have surfaced this time:
`_has_substantive_assistant_content` sits beside the pre-existing
`_count_substantive_user_turns` in the same filter loop, and with
`_first_user_message_text` that is three separate streaming passes over the same
file per session. Correct and short-circuited, so not a defect — but exactly the
shape of thing a `find substantive` was meant to put in front of the author.

---

## B. OPEN — duplication and consolidation

### F-08 — adapter boilerplate duplication claude/codex
*source: `runtime/bakeoffs/` in this job — all eleven cross-vendor review texts, committed at `eafd0b50`; originally named by the COO at `fba61413` · status: open · trigger: a third adapter*

`adapters/claude.py` and `adapters/codex.py` share structure that a third and
fourth adapter will duplicate again. The natural moment to consolidate is
**before S4**, with two examples to generalise from and not yet four to
migrate.

**The provenance gap this row used to carry is closed.** It was admitted on the
trigger criterion while stating plainly that the cross-vendor round's texts
lived in the COO's session scratchpad and that `ab6e87f8` carried only six
extracted items, not including this one. At `eafd0b50` the COO put all **eleven**
texts on the record at `runtime/bakeoffs/` — the exact texts, not summaries
(S2 round 1: nemotron / glm / luna / deepseek plus worksheet; round 2: those
plus gemini plus worksheet). The citation above now points at the immutable job
record, and no re-derivation is needed before the consolidation slice is cut.

Recorded because the mechanism matters more than the row: the gap was closed by
flagging it in a ledger row rather than by quietly trusting the source, and the
remedy cost the coordinator one commit.

---

## C. OPEN — product limitations, policy, and one incident

### F-09 — archive-provenance drift (`08d89c12`)
*source: raised as content loss at `670d410d`, re-characterised by judge `29268c5e` · status: open*

The original report called this content loss ("the archived jsonl holds less
than whatever produced the stored .md"). Exact analysis says otherwise, and the
correction matters:

- 8,554 lines on both sides; **66 lines differ**; every one is an
  `=== assistant <ts> [L:N] ===` header.
- **Not one line of conversation text differs. Not one timestamp differs.**
- 53 headers: the `[L:N]` ref merely shifts (`+1`×3, `+2`×48, `+3`×1, `+6`×1).
- 13 headers: stored had an `[L:N]`, regenerated has none at all.
- The −115 bytes is entirely those 13 dropped refs, net of digit-width growth.

Logged as **archive-provenance drift, not data loss**. On the
narrative-fidelity standard it costs nothing. It stays open as an
archive-integrity question: the transcript archive and the rendered
conversation disagree about structure, and nobody knows why.

### F-10 — the stale mid-session `.md` class
*source: `670d410d`, `29268c5e` · status: open · blocks: trusting the corpus oracle's failure count*

Ten corpus sessions do not reproduce byte-for-byte. All ten fail **identically
on main**, and main's output and the adapter's output are byte-identical to
each other on all ten (sha256 verified per pair) — pre-existing drift, not
S1's. They split three ways:

- **Eight**: genuinely stale `.md` files written mid-session. Regeneration
  produces *more* narrative, not different narrative — `810372f1` 15 → 116
  turns (+202,766 bytes); `066f9f7a` 1 → 56; `d0ae24a8` 46 → 70. On the
  narrative-fidelity standard these are a **win**.
- **One** (`08d89c12`): F-09, a different thing entirely.
- **One** (`7a8a83ff`): same turns, same bytes (74,083 both sides), differs
  only in `ended` by **118 ms**. Not staleness at all.

**Why this is consequential rather than cosmetic:** the corpus oracle will
report ~10 non-reproducing forever unless someone regenerates these or pins
them as known-drift. A permanent non-zero failure count is exactly how a real
regression gets waved through.

### F-11 — `memory_wrap` reports a missing project to the model but not to the user
*source: judge `52e59524` · status: open · not owned by the S3 revision*

The test is named `test_missing_project_reports_visibly_not_silently`. Executed
for real against a nonexistent project, the wrapper **does** put
`(memory_wrap: Error: no project state at …)` into the prompt the model
receives — and emits **0 bytes on stderr**, exit **0**.

Non-silent to the *model*, entirely silent to the *user*. Typo a project name
and you get a normal-looking session with no memory and nothing on your
terminal to say so. That is criterion 3's "silently degrades".

Fail-open is the right default — a missing narrative should not block a
session. The claim is what overreaches. One-line fix: echo the same notice to
stderr.

For the record, everything else the wrapper was attacked with fails **closed**
and correctly: unknown client, malformed JSON, wrong-type JSON, missing config,
`_comment` as a client key, ghost binary, unknown `prompt_mode`, and `jq`
absent from PATH. No attack produced a silent success or launched a client with
a broken prompt.

### F-12 — `resume` turns an empty `conversation_md` into a directory-read error
*source: judge `52e59524` · status: open · pre-existing, introduced by no slice on this job*

`server.py:566`: when a session record has no `conversation_md`, `Path("")`
evaluates to `.`, `.exists()` is true because the cwd exists, and `read_text()`
raises `IsADirectoryError`. `resume` returns
`(failed to read conversation.md: [Errno 21] Is a directory: '.')`.

Harmless when a person reads it. It is no longer only read by a person —
`memory_wrap` prepends that string straight into a model's context.

**Severity is genuinely low and stated as such:** 0 of 29 real projects have an
empty `conversation_md` on their last session. Only synthetic state reaches it,
including the wrapper's own test fixture, which is why nobody noticed. Guard
the empty case rather than relying on the corpus staying clean.

### F-13 — the dev `.venv` holds a foreign package named `mcp`
*source: `de2577f2`, independently verified at `52e59524` · status: open · trigger: any hand-run of the wrapper from the repo venv*

`/home/scott/projects/llm_memory/.venv` contains an `mcp` package that is
**not** the MCP SDK `server.py` imports. It imports fine at top level, so it
looks healthy — but `import server` under it dies with
`AttributeError: 'Server' object has no attribute 'list_tools'`.

This is why `tests/test_memory_wrap.py` points `MEMORY_WRAP_PYTHON` at
`~/.claude/memory/lib/.venv/bin/python3`. The workaround is correct and
correctly documented as pre-existing.

Two consequences worth keeping:

- The wrapper's documented fallback — "falls back to plain `python3` for
  dev/test checkouts" (`tools/memory_wrap:22-24`) — **is non-functional here**:
  system `python3` has no `mcp` at all. It fails closed with a traceback and
  exit 1, which is right, but the comment overstates what the fallback buys.
- Anyone hand-running the wrapper from the repo venv gets a confusing
  `AttributeError` rather than "wrong environment".

Admitted as an environment quirk **with a recurrence path**, which is the
distinction the heuristic draws.

### F-14 — the standalone oracle script exits 0 with no corpus
*source: judge `29268c5e` finding 2, re-verified unchanged at `bae6414a` · status: open (standing constraint) · trigger: anyone wiring the script into CI*

`tools/adapter_oracle.py` returns **0** when the conversation corpus is absent
("nothing to check") and when the sample is empty. Verified under a redirected
`HOME`.

**The constraint:** the standalone script is a vacuous pass on any machine
without the corpus and **must never be wired into CI as the gate on its own**.
`tests/test_adapter_oracle.py` is the correct gate and is wired correctly —
with no corpus it runs 17 synthetic fixtures (not 20, as originally reported)
and they genuinely bite: dropping a turn fails 3, reordering blocks fails 1,
truncating one body character fails 4, dropping an `[L:N]` ref fails 2.

Recorded so nobody "simplifies" CI by calling the script directly.

### F-15 — nothing downstream reads `client:` yet
*source: `5bcd5c88`, carried forward at `ff1cba31` · status: open · trigger: attribution becoming load-bearing, i.e. now*

The S1 pre-merge verification ran every downstream consumer against real
`client:`-bearing conversation files — `conversations.py`, `server.py`'s
`narrative_coverage`/`resume`, the delta-extractor input path, `merger.py`,
`backfill_conversations.py`, `renderer.py`, and the SessionStart hook. Nothing
chokes; the frontmatter parser is a generic `key: value` regex and every lookup
is by name.

The finding is the other half: **`client:` is provenance nobody consults.**
Correct when there was one client. It becomes load-bearing with codex ingested
and a third adapter coming, and no consumer is ready.

(Also surfaced by that pass: `renderer.py:301 _tail_lines()` has zero call
sites — verified by instrumenting `open`/`read_text` during a real render, 1
file opened, 0 under `conversations/`. Dead code with no failure mode, so no
row of its own; noted here so it is not re-discovered.)

### F-16 — the compacted-record render path has no rendered pin
*source: judge `07cf16e8` · status: open · trigger: any change to compacted-record rendering*

Fixture `01-compacted-…` is the only fixture carrying a `compacted` record, and
its `session_meta` payload genuinely has `parent_thread_id`/`forked_from_id`,
so it is correctly classified as a forked thread and correctly **stubbed**
rather than enveloped. The skip is legitimate and self-documenting.

Side effect: the compacted-record **render** path is pinned only by its
expected-envelope fixture, never by a rendered conversation. It can break
silently. Wants a fixture that carries a compacted record without being a
subagent.

### F-17 — D12: codex and qwen both ship native memory
*source: design note `5a1c58b2`, D12 · status: open, undecided · blocks: S4/qwen slice framing*

Codex ships `~/.codex/memories/` + `memories_1.sqlite`. qwen ships
`~/.qwen/memories/MEMORY.md` — with the same `MEMORY.md` + feedback + user
taxonomy shape this project uses. Wiring `llm_memory` into either client does
**not** touch its native store; the two are simply separate.

Design-note recommendation: **displace for project knowledge, keep native for
client-local preferences.** Not ratified, nothing implements it. Until it is
decided, a codex user has two memory systems that do not know about each other
and no documented answer for which to write to.

### F-18 — D2: `LLM_MEMORY_HOME` recommended, never built
*source: design note `5a1c58b2`, D1/D2/D3 · status: open · blocked on F-22 Q1*

Verified during the design pass: **zero env overrides exist anywhere in the
codebase.** `~/.claude/memory` is pinned by `install.sh:6`, by 14 Python modules
each recomputing `Path.home()/'.claude'/'memory'` independently, by
`hooks/session_start.sh` (19 occurrences), by the MCP registration in
`~/.claude.json` (absolute paths), and by Syncthing's `config.xml` per device.

Recommendation: add the indirection now, default unchanged; if the name matters
for optics, **symlink rather than move**. The code change is cheap (~14 sites;
`merger.py:38 _memory_root()` already shows the pattern). The *data* move is
2.4 GB, an all-or-nothing flag day across devices, a full Syncthing rescan
each, and MCP re-registration in every client — and buys **no capability**,
because nothing in the ingest path consults the directory name.

The one scenario where a rename stops being cosmetic: running `llm_memory` on a
machine with no Claude Code installed at all. For an open-source release that
is real.

### F-19 — the rules line has never been observed to work on any client
*source: design note `5a1c58b2` §6 (its own falsifier), S3 scope at `de2577f2` · status: open · blocks: the whole non-Claude serving story*

The design note states the falsifier that would invalidate its own
recommendation:

> If S3's acceptance test shows models that have the tools and still do not
> call them, every rules-line recommendation is documentation theatre and the
> wrapper becomes the only answer.

No slice has tested this. D8's rules line is shipped as documentation in
`docs/mcp-wiring-recipes.md` and has never been observed to work on any client.
That is not a defect in S3 — it was scoped to recipes, not adoption — but it
means the injection story rests on one mechanism that is verified
(`tools/memory_wrap`, once F-05 and F-06 close) and one that is not (the rules
line, on four clients).

Automatic injection exists **only** where a client splices hook stdout into
context. Claude does (`session_start.sh:196-214`). Grok, Codex, Gemini and qwen
all depend on the model choosing to obey, and **the failure is silent in every
case** — the model answers with no memory and cannot tell it was missing any.

### F-23 — the `memory_wrap` real-client spawn incident
*source: `866c89fe` (primary evidence, COO, timestamped); `s3-serving` disputes the incident · status: open · blocks: nothing today; governs how test configs for client-launching tools are written*

**Reserved empty in this ledger's first compilation and opened now that the
evidence exists.** The compiler was asked for this row, found nothing in the
53-event log describing it, and refused to open a placeholder on the grounds
that a row invented by the compiler asserts something it cannot show. That
refusal is what put the evidence on the record; the row is opened on
`866c89fe`, not on the request.

**Observed, ~14:33.** (1) `ListAgents` showed ~20 new sessions named
`llm-memory-*`, **all attached to one tmux pane** — `s3-serving:@304` — every
one started 11-14 seconds earlier. Fleet seats each own their own window, so
twenty sessions bound to a single pane within seconds is a process tree
spawning inside that pane, not a baseline. (2) `ps` counted **54 `claude`
processes under 120 seconds old, and climbing** between consecutive checks,
against a fleet of ~8 seats all minutes-to-hours old. (3) Two Escape keys sent
to that one pane — nothing else — collapsed the under-60s count from dozens to
2 within seconds; interrupting one seat's turn cannot stop unrelated launches.
(4) The phantom sessions vanished after targeted kills and `ListAgents`
returned to the normal roster.

**Likely vector**, consistent with all four observations: the manual testing
visible in the pane at that moment ("back to testing `memory_wrap` with the
corrected python resolution") ran `memory_wrap` **outside the fixture config**,
where a real `clients` entry launches the real `claude` binary once per
invocation.

**The committed test suite is EXONERATED, explicitly and by the same post.**
The fixture-fake suite never invokes `claude`; the guard tests in revision
`1f25dbd1` stand (fixture-only clients in tests, no real binaries reachable
from the test config). **No fault attaches to the committed suite**, and this
row should never be cited as if it did.

**Disagreement, kept attributed** per this job's policy: `s3-serving` disputes
that the incident occurred, having inspected post-cleanup state. The COO's
position is that the post-cleanup state cannot show what the live observations
showed. Both are recorded; neither is resolved here. **Room is deliberately
left in this row for `s3-serving`'s own account of the manual commands it ran**,
which is the one piece of evidence that would settle it and which only that
seat holds.

**What it is actually for:** a tool whose job is to launch a client is a tool
whose test config can launch a client. The durable lesson is that such a tool
needs its real-binary path unreachable from anything a developer might run by
hand in a repo checkout, not merely unreachable from the test suite.

### F-24 — the fixture scrub's two residuals: a pseudonym, and a shape guard that cannot see a bare name
*source: judge `97c8a3cb`, measured against `a996002` · status: open · blocks: nothing; bounds what F-03's fix can be said to have achieved · trigger: a fourth real project name entering the fixture corpus*

The F-03 fix is sound and does what Scott's reversed ruling asked: the three
names are gone, and `git grep` across `tests/fixtures/codex` returns zero. Two
things it does **not** do, recorded together because they are one cause — a
shape-based sanitiser cannot tell a real project name from a placeholder, which
is the fix's own stated premise.

**1. `project-<sha1:8>` is a pseudonym, not an anonymisation.** The digest is
unsalted SHA-1 of the plaintext project name truncated to 8 hex characters, so
the mapping is guess-and-confirm reversible in one line by anyone who tries the
name: `sha1("fletchcorp")[:8]` = `aad8ac1e`, which is fixture 10. In a public
repo, for precisely the name flagged as identifying because it matches Scott's
own email domain. `make_codex_fixtures._placeholder_project`'s docstring calls
it a "content-free stand-in"; it is content-free to a reader and to `grep`, and
it is not content-free to a guesser. A committed salt would not help — it ships
too. **Recommendation: accept this deliberately and correct the docstring**,
rather than re-engineer it. The ruling was that the names must not ship, and
they do not ship; determinism was the right trade and F-24 exists so nobody
later believes a stronger property was bought than was.

**2. The tightened depth walk still cannot catch a bare name.** The new
predicate pins the exact placeholder cwd shape and genuinely catches all three
names in `cwd` position — verified by running both predicates side by side, old
says sanitised / new says not, for all three, with the placeholder still
passing. But a **bare** project name outside a path is token-shaped and passes
`_is_sanitised` under both the old and the new predicate.
`test_no_real_project_name_ships_in_a_fixture` covers exactly that hole for the
three literals Scott ruled on — so the pair is complete **for these three
names**, and a **fourth** real project name landing in a token-shaped non-`cwd`
field would be caught by neither guard.

### F-25 — the `codex-auto` marker is tested on every session, not only codex ones
*source: judge `97c8a3cb` · status: open · trigger: a claude session whose first user turn quotes the harness preamble*

`_is_codex_auto_participant()` runs in `_handle_narrative_coverage`'s filter
loop **before** the client is looked up, so its marker string is matched
against the first user turn of every session regardless of client. A **claude**
session that quotes the `codex-auto` preamble as its first user turn — pasting
it to debug the harness, which is plausible in this project family
specifically — is dropped silently, with no error and no log line, exactly the
F-01 failure mode in the losing direction.

**Zero of 6,701 non-codex archived sessions match today**, so this is
prophylactic rather than a live loss, and it is admitted on the trigger
criterion. The fix is one line: move the `client_by_sid` lookup two lines up
and gate the marker test on `client == "codex"`.

**While in that function, and not worth its own row:** `_client_by_session()`
walks all 5,718 conversation `.md` files, and `_find_project_transcripts()`
already walked the same directory via `list_sessions()`. `narrative_coverage`
went **0.33s → 0.57s** measured on this machine. Fine for an interactive tool;
reusing one pass gives it back. Cosmetic, same neighbourhood: `import
conversations` is now unguarded at module top while the same import inside
`_find_project_transcripts` is still wrapped in `try/ImportError`, so that guard
is now decorative.

### F-26 — the `mcp<2` pin bought time, it did not fix anything
*source: escalation `09ea13cb`, closed by `7077c0c`; row opened at S3
delta-verify (`s3-judge`) · status: open · trigger: anything that forces `mcp`
2.x back into this project's resolved dependency set*

**Say this precisely, because the escalation's own closure invites the wrong
reading.** `09ea13cb` is closed — `requirements.txt` now pins `mcp>=1.0,<2`,
a fresh venv resolves `mcp==1.29.0`, and the suite passes. **That is a ceiling
on the symptom, not a fix to the cause.** `server.py:54` still calls
`@app.list_tools()`, an API `mcp` 2.0.0 removed
(`AttributeError: 'Server' object has no attribute 'list_tools'`, reproduced
directly against this machine's `mcp` 2.0.0 during this delta-verify).
Nothing in `7077c0c` touches `server.py`'s use of that API — the moment
something forces `mcp>=2` back into the resolved set (a transitive dependency
bump, a future package that needs a 2.x-only feature, someone editing the pin
without checking why it exists), the exact `AttributeError` this escalation
was opened for comes back, on a codebase that by then may no longer carry
anyone's memory of why the ceiling was there.

**No trigger date, and that is the honest state of it, not a gap in this
row.** There is no scheduled moment this becomes due — the pin holds for as
long as nobody needs `mcp` 2.x for an unrelated reason. The checkable trigger
condition is behavioural, not calendar: *if `pip show mcp` in this project's
venv ever reports `2.x`, `server.py` breaks at import, immediately, the same
way it did before `7077c0c`.* Anyone auditing dependency bumps should treat a
`mcp` major-version change as a required trigger to re-open this row and
either migrate `server.py` off `list_tools()` or re-pin with a stated reason.

**OWNER DECISION, 2026-08-23 (Scott, relayed by the COO at `effe7f2e`), and
this row moves `open` → `deferred` because of it.** The choice put to him was
(a) port `server.py` to the mcp 2.x API now, while the context is warm, or
(b) keep the pin and write an explicit reopen trigger. **He took (b).** Under
this ledger's own rules that is what makes the row `deferred` rather than
`open`: deferred means deliberately not doing it, *with the condition that
reopens it stated* — and a deferred row without a condition is a bug in this
ledger. The condition is the behavioural one above, now decided rather than
merely observed:

> **Reopen F-26 when anything forces `mcp>=2` into this project's resolved
> dependency set** — a transitive bump, a wanted package that needs a 2.x-only
> feature, or an edit to the pin itself. Concretely: `pip show mcp` reporting
> `2.x` in a fresh venv built from `requirements.txt`.

**And the port is not abandoned, it is placed.** It folds into the
release-readiness pass, which is prepared but not run — publishing is **NOT
YET** per the same owner decision (5.1), so `origin/main` stays at `e3ae2a7`.
That ordering is deliberate: the pin's whole justification was that a new
user's first run is the audience, so the port becomes due at exactly the moment
there are new users, and not before.

### F-27 — the narrative-on-qwen pilot ran off-record, and it failed its own pass condition
*source: COO directive `01786950192264618966` (rung 3 of the standing qwen
ladder); results produced 2026-08-22 outside any job record; admitted here by
owner decision `effe7f2e` · status: closed · reopen: a materially better local
model, or a redesign that removes Pass B from the local rung's job*

**The pilot the COO queued was, in substance, run — and nobody knew.** On
17 August the COO queued a narrative-on-qwen pilot with an explicit condition:
*"Pass ⇒ qwen becomes the default narrative backend with Claude as fallback;
fail ⇒ numbers on record and the rung stays Claude."* On 22 August that
comparison was executed against the real delta-extractor spec on three sessions
Sonnet had already processed. It produced numbers. Those numbers lived in a
memory file and a bake-off directory, and reached no board until 23 August.

**Harness and raw outputs:** `~/.claude/memory/bakeoffs-qwen38-extractor-2026-08-22/`.
Rerunnable. Sessions: `footballmanager` (16k prompt tokens), `llm_memory` (48k),
`sysadmin` (46k). Backends: Sonnet (the pinned `model: sonnet` in
`agents/delta-extractor.md`), Qwen3.8-27B local via llama-swap, Haiku 4.5.

**What passed.** Qwen3.8 is *structurally* sound, which the 9B and 3.5-27B
generations were not — that is a real change and the reason the rung was worth
re-testing. 3/3 valid JSON, schema-correct, never invented or referenced a
non-existent id, every delta applied cleanly through `merger.apply_delta`.
It set `value` on every item where Sonnet sometimes omitted it, matched Sonnet's
closed items every time, and **caught a genuine decision contradiction Sonnet
missed** (`dec-ecad9fb2`, "no JS on other pages", against a session that added
client-side filtering JS).

**What failed, and it is concentrated rather than diffuse.**

| | Qwen3.8 | Haiku 4.5 | Sonnet |
|---|---|---|---|
| items introduced (3 sessions) | 7 / 9 / 18 | 4 / 13 / 16 | 10 / 16 / 21 |
| **archives found** (Rule 4 Pass B) | **0** | **0** | **3** |
| closure match | matched every time | missed one | — |
| contradictions found | 1 and 2 | 0 | 0 and 1 |
| `load_bearing` %, target 10–15 | 0 / **44** / 7 | 0 / 18 / **25** | 0 / 12 / 11 |
| `closure_status` | wrong on the dense session | wrong on the dense session | correct |
| cost / time per session | free / 162–335s | $0.13–0.19 / 104–170s | — |

**The verdict against the COO's stated condition: FAIL. The rung stays Claude.**
Not because the model is unreliable — it is not, any more — but because it
returns roughly 50–70% of Sonnet's items, over-grades `load_bearing` to 44% on a
dense session against a 10–15% target, mislabels an open-offer ending as
`complete` where the spec says `interrupted`, and writes journals about half the
length.

**Two findings worth more than the verdict.**

1. **The gap is Sonnet vs *everything else*, not Sonnet vs local.** Haiku costs
   money to be *worse* than free Qwen at the jobs that make this ledger
   trustworthy: it missed a closure both Qwen and Sonnet found, produced zero
   archives and zero contradictions across all three sessions, and over-graded on
   two of three. **Haiku has no niche here.** Wall-clock is not a constraint on a
   background pipeline, so "free and slow" strictly dominates "cheap and fast".
2. **Both cheap models fail in the *same place*: Rule 4 Pass B.** Zero archives,
   twice, three sessions each. That is not a recall gradient, it is a specific
   job neither can do — and it is the same job that forces the extractor's
   largest input. This is the evidence base for the design cohort on job
   `llm-memory-pipeline` (proposal `01787487821089749375`, corrected at
   `01787487925269299055`), which asks whether Pass B belongs in the per-session
   extractor at all. **Do not read that cohort's premise as settled by this row:**
   the April redesign put Pass B there deliberately, and the cohort may well
   conclude April was right.

**Why this row exists at all, and it is not about qwen.** A decision-shaped
result sat outside the record for a day and would have been re-litigated at full
cost by the next person to ask "can we use a local model". The rule already on
this board covers it — *a seat holding a question posts it before going idle*
(`a4565f94`) — and the same logic applies to a seat holding an *answer*.
Generated views and memory are evidence, not replacements for the job record.


### F-28 — `project_from_cwd` invents a phantom project for every worktree session
*source: owner product ticket T-F25, relayed by the COO at `b6df4478`; scale
and root cause measured by `llm-memory-pm` 2026-08-24 · status: open ·
blocks: every project that uses `am` worktree seats*

**The ticket says the sessions are never ingested. They are — that is the good
news, and it makes the fix much smaller than the ticket assumes.** Ingestion,
stripping and archival all work. What fails is **attribution**, in one function.

`adapters/base.py:126-138`:

```python
for i, part in enumerate(parts):
    if part == "projects" and i + 1 < len(parts):
        return parts[i + 1]
```

An `am` worktree seat runs in
`/home/scott/projects/.agent-messaging-worktrees/<PROJECT>/<JOB>/<SEAT>`
(read from a real transcript's `cwd`, not inferred from the slug). The component
after `projects` is therefore `.agent-messaging-worktrees`, so every worktree
session is stamped `project: .agent-messaging-worktrees` — a project that does
not exist and never will.

**MEASURED 2026-08-24, not estimated:**

| | |
|---|---|
| worktree project slugs under `~/.claude/projects/` | **106** |
| worktree transcripts | **49** (39.1 MB) |
| conversations stamped with the phantom project | **51** |
| `~/.claude/memory/projects/.agent-messaging-worktrees.json` | **does not exist** |
| distinct real parent projects hidden inside them | **1** — all 49 are `agent-messaging` |

So 49 sessions are stripped, archived, searchable by `grep`, and **merged into
no narrative at all**, because the project they claim to belong to has no
ledger. They are not lost from disk. They are lost from memory — which for this
product is the same thing.

**The parse rule is unambiguous, contrary to the ticket's "prefix/parse rule"
concern.** The worry was that `<PROJECT>-<JOB>-<SEAT>` cannot be split on
hyphens when project names contain hyphens — true of the *slug*, but the slug is
a lossy encoding of a path, and the path has real separators. Reading `cwd`
instead of the directory name makes it a path-component lookup: the parent
project is the component immediately following the worktrees marker. No
guessing, no ambiguity, no heuristic.

**Generalise the fix rather than special-casing one marker.** The root defect is
that `project_from_cwd` will return *any* component after `projects/`, including
a dotted infrastructure directory. `.agent-messaging-worktrees` is today's
instance; any future `projects/.something/` produces the same phantom. Proposed
invariant, testable with a trigger and a non-trigger control: **a project name
beginning with `.` is never a project** — descend past it and take the next
component, or return `""` if there is none.

**Backfill is cheap and needs no re-extraction.** The stripped conversations
already exist; only the `project:` frontmatter is wrong. Re-stamping recovers
all 49 into `agent-messaging`'s narrative without touching a raw transcript.
Note the ordering consequence: `agent-messaging` gains 49 unprocessed sessions
the moment this lands, so the fix and the drain are one slice, not two.

**Why this is a product row and not an `agent-messaging` row.** The worktree
layout is `am`'s; the wrong answer is ours. Any client that runs an agent in a
subdirectory of `projects/` hits it — the convention `project_from_cwd`
encodes is a guess about directory layout that happens to be wrong for a layout
already in daily use across this machine.


### F-20 — transition-update ordering: the old installer always runs the upgrade
*source: incident `512d16a6`, declined with reasons at `c237eb21` · status: deferred*

On 2026-08-17 the background updater ran the **old** `install.sh` to install the
new tree. The old script had no `adapters/` copy block, so it installed the new
shim `extract_conversation.py` **without** `adapters/` — live for ~15 minutes —
and stamped `VERSION`, so re-running `--update` skipped the copy and would not
self-heal. Exactly the silent breakage S1 predicted. Repaired by hand.

The general defect: **the old installer always runs the upgrade to the new
one.** Every future update inherits this.

What shipped was the *detector*, not the fix — a session-start self-check
(`fd474bf`, widened at `14f7177`) that imports `extract_conversation` and
`adapters.base`/`adapters.render` from the lib dir and shouts on stdout **and**
stderr when they are broken. stdout because it is the only channel that reaches
the model, and going unnoticed was the entire failure mode.

The root-cause fix — `install.sh` re-execing its freshly extracted self before
the copy phase — was declined **on purpose**:

> "It changes the one code path that can break every future update, and I
> cannot test a real transition from here — I would be shipping an untested
> change to the upgrade mechanism to fix an upgrade bug." — `s1-adapter`

**Reopen condition:** a seat or operator who can drive a **real remote
transition** end to end. Do not close this from a single machine, and do not
let the existence of the detector be mistaken for the fix.

### F-21 — Grok `UserPromptSubmit` probe unrun, and its runbook is not executable
*source: design D9 (`5a1c58b2`), runbook at `de2577f2`, defects found at `52e59524` · status: deferred*

The question is one bit and falsifiable. Grok's `SessionStart` stdout is
confirmed ignored (`~/.grok/docs/user-guide/10-hooks.md:415`), but the same doc
lists `UserPromptSubmit` as a per-turn event (`:86`) and its "stdout is
ignored" sentence is scoped to "events **like** `SessionStart` or
`PostToolUse`" — conspicuously excluding `UserPromptSubmit`. If Grok's
`UserPromptSubmit` stdout reaches context, Grok gets genuine automatic
injection and stops being obedience-dependent (see F-19).

**Why it is unrun, and why that was right.** The only hook surface Grok trusts
is the single shared global `~/.claude/settings.json`. Running the probe means
writing to a file other seats' live Claude Code sessions depend on mid-session;
two seats had live sessions in that window. `s3-serving` and `s3-judge`
declined independently, for the same reason.

**Two defects to fix before anyone runs it:**

1. **Step 3 does not work as written.** `grok "prompt"` is the *interactive
   TUI* form — `grok --help` documents `[PROMPT]` as "Initial prompt for the
   interactive session". Presented as a copy-paste bash block feeding step 4's
   one-word read, it drops the operator into a TUI. Grok has
   `-p, --single <PROMPT>` with `--output-format plain` for exactly this.
2. **No positive control.** Step 4 reads a `NONE` reply as "D9 negative, stdout
   ignored". But `NONE` is equally consistent with "the hook never fired" — bad
   JSON, wrong file, Grok not reading it. As written the procedure **cannot
   distinguish a true negative from a broken install**, and this job's policy
   requires a trigger and a non-trigger control. Give the sentinel hook a side
   effect (`echo SENTINEL; date >> /tmp/grok-probe-fired.log`); an empty log on
   a `NONE` result means inconclusive, not negative.

**Reopen condition:** a window with no concurrent Claude Code or Grok session,
**or** a project-scoped hook surface for Grok (which would deserve its own
row). Fix both runbook defects first — running it as written can produce a
false negative that closes D9 wrongly.

**Runbook defects: CLOSED at `7b96b22` (S3 delta-verify, judge `s3-judge`).
The probe itself stays unrun — the reopen condition above has not changed and
was not attempted.** Checked statically, per the deferral, not by running the
probe: `docs/grok-userpromptsubmit-probe.md` step 3 now reads `grok -p
"..." --output-format plain`, and this machine's `grok --help` confirms both
flags exist exactly as used — `-p, --single <PROMPT>` ("Single-turn prompt.
Prints the response to stdout and exits") and `--output-format
<OUTPUT_FORMAT>` with `plain` a valid (and default) value; the multi-line
quoted string syntax checks clean under `bash -n`. The positive control is
now present in the procedure: the sentinel hook has a side effect
(`date >> /tmp/grok-probe-fired.log`) independent of whether stdout reaches
the model, and step 4 explicitly reads an empty log as inconclusive rather
than as a negative. Both defects named above are fixed in the doc as written;
whether `UserPromptSubmit` stdout actually reaches Grok's context is still
unknown and stays that way until someone runs it under the stated reopen
condition.

### F-22 — Q1 and Q3 to Scott, unanswered; Q2 is closed
*source: `93e6d723`; a third question was held and never spent; Q2 closed against `78f45a48` per `eafd0b50` · status: deferred (owner) on Q1 and Q3*

Neither open question blocks anything — defaults were picked and the design note
is complete without answers — but both change recommendations if answered the
other way.

**Q1. Does "a generic memory folder for multiple agents" mean the path must
stop saying `.claude` (optics / open-source), or just that non-Claude agents
must be able to use it (capability)?** Default taken: capability, hence D1
(keep the root) + D2 + D3. If Scott meant optics, **D1 flips** — but D3
(symlink `~/.llm-memory` + `LLM_MEMORY_HOME`) may buy the whole optics win for
a hundredth of the cost. Cost breakdown in F-18.

**Q2 — CLOSED. Is qwen-local a client whose memory Scott wants kept, or a test
rig?** Answered by the owner on the board at `78f45a48`, found and cited by the
COO at `eafd0b50`. Scott's ruling, verbatim in the relevant part: qwen is "not
necessarily" a kept client, **but** "what if I used a cloud qwen at full
power... It should be able to be done easily by any agent". Read as: **client-
agnostic extensibility governs**, so the answer does not turn on qwen's status
at all. Consequence, which was the whole reason the question mattered:
**`tools/memory_wrap` is load-bearing, not speculative, and D10 is confirmed.**

Worth keeping rather than deleting, because the question was mis-classified
before it was answered. It was escalated as an unanswered owner question when
it was in fact already answered on this board — a findable answer, not a
missing one. The distinction is the coordinator's job and this row is the
evidence that it is a real distinction.

**Q3.** Held by `mc-design`, never asked. Recorded so the budget is not assumed
spent.

**Reopen condition (Q1 and Q3 only):** Scott answers, or Scott says the
defaults stand.

---

## E. RECENTLY CLOSED — do not re-open these

Kept only where re-opening is a live risk. All verified closed by an
adversarial delta pass, not by the author's report.

- **Oracle `client:` excuse too wide** (`29268c5e` finding 1 → closed at
  `4f08246`, verified `bae6414a`). The excuse is now exactly one line with
  exactly the expected value. The fix closed **two holes the judge had not
  reported**: a single `client:` line with a *wrong* value, and the line
  **omitted entirely** — the latter meaning the old oracle would pass an
  adapter that had stopped emitting provenance at all.
- **Self-check gated on the file whose absence is the failure** (`14f7177`).
  The old guard was `if [ -f "$LIB_DIR/extract_conversation.py" ]`, so deleting
  that file silently disabled the entire check. Verified real by running the
  old hook against the same broken lib: it fires 0. Now 8 triggers fire, 2
  controls stay silent, and it survives the cwd-shadowing trap that made the
  first version useless.
- **Case-insensitive prefix routing** (`8b1e39a`). `CODEX-`/`Codex-`/`cOdEx-`
  now route to codex; the near-miss `codexlike-` still routes to claude —
  widened without swallowing the lookalike.
- **Sanitiser depth** (`8b1e39a`). Prose is now caught in values, as dict
  **keys**, and through nested **lists**. 0 unsanitised strings across 1.8 MB.
- **Mixed-dialect handling** (`8b1e39a`). Both dialects kept, a note recorded
  on `SessionMeta.notes`, a stderr WARNING; single-dialect control silent. 0 of
  127 real files flagged — disjointness now asserted rather than assumed.
- **Repeat-install determinism** (`e3ae2a7`). Three consecutive installs leave
  an identical tree; a retired fixture is now removed instead of lingering and
  being collected by pytest.
  **One nuance worth not losing:** the `fixtures/fixtures` *nesting* half is
  **not reproducible on this platform** — GNU coreutils 9.4 merges rather than
  nests, so the pre-fix commit also produces zero nesting here. That half is
  pre-emptive (correct: the lib self-updates every session start, so repeat
  install is the normal case and divergence would show on someone else's
  machine). The **retired-fixture half is a real, reproducible repair.** Do not
  let a changelog imply an observed break that nobody observed.

---

## F. PROCESS — kept separate on purpose

These are not defects in the product. They are how work on it goes wrong.

- **F-P1 — A test count is a claim about the commit, not about your worktree —
  and about the environment running it, not only the tree checked out.**
  Verify it from a **fresh clone or worktree of the pushed SHA run in a fresh
  environment built from the repo's own declared dependencies**, and say both
  of those — which tree, which environment — in the same sentence as the
  number. A fresh checkout is necessary but not sufficient; it is not the same
  claim as a fresh environment, and conflating them is exactly the mistake
  this rule exists to block. This repo sets `core.fileMode=false`, so file
  modes are one specific thing a worktree will lie to you about (F-05: 255/1
  in the author's worktree, 247/8/1 from the commit) — but the rule is
  general. `git update-index --chmod=+x <file>` is how you fix a mode when
  `core.fileMode` is off. **Widened at the S3 delta-verify (`s3-judge`,
  `7b96b22`): today the identical commit gave three different results on one
  machine depending only on which venv ran it.** A fresh venv built from
  `requirements.txt` resolves `mcp==1.29.0` and the suite passes clean
  (271 passed, 1 skipped). This repo's own long-lived `.venv` (F-13) resolves
  `mcp==2.0.0` and does not merely fail tests — it fails to even **collect**
  them: `AttributeError: 'Server' object has no attribute 'list_tools'` at
  `server.py:54`, reproduced directly during this verify. A third venv could
  easily land somewhere else again depending on when it was built against a
  moving `mcp` release line. None of the three is "the wrong tree" — the tree
  was identical in every case. State the venv's provenance (what it was built
  from, and when) with every count, not just the git ref.
- **F-P2 — Reading a `--help` and running the command are different acts of
  verification.** F-04's Gemini line and the Codex line beside it were both
  "verified against `--help` output"; one was right and one did the opposite of
  its stated purpose. A recipe whose entire value is being copy-pasteable
  should be **executed once into a sandbox `HOME`** — which costs nothing and
  touches no live config.
- **F-P3 — State the method with the number.** Two figures in the S2 record
  cannot be reproduced from what was written down. "1069 assistant turns"
  reconciles with no definition the judge could construct (independent counts:
  1054 / 1376 / 1394 depending on what you include). And "468 user turns per
  `server.py`" is only reproducible **uncapped** —
  `_count_substantive_user_turns(path, cap)` short-circuits at its cap, and at
  the production cap of 5 the same function returns ≥5 for exactly 8 of 124.
  Neither is a correctness problem; both are numbers a future seat will either
  re-derive or trust wrongly.
- **F-P4 — Graph-check before naming a new symbol,** and report
  GRAPH-HIT/GRAPH-MISS with the delivery (`062046a8`). A MISS means *no name
  matched*, not *nothing like it exists* — F-07's `render` was a legitimate
  MISS on substance while colliding by name with two existing symbols.
- **F-P5 — A relayed instruction is unverified until the record shows it.**
  Established at `51e8d0e5` when a side-channel instruction was correctly
  refused and the COO confirmed the refusal was right and is the org standard.
  Applied twice more in this compilation: both cross-session relays about this
  assignment were checked against the board before being acted on.
- **A guard needs a trigger and a non-trigger control.** Job policy, and the
  reason F-21 is not runnable as written — a probe with no positive control
  cannot tell a true negative from a broken install.

---

## COMPILATION NOTES

**Method.** Read all 53 events in
`.agent-messages/jobs/llm-memory-multiclient/events/` directly rather than from
session context, then verified the claims I could reach against the tree at
`e3ae2a7` and against live state (`server.py` tool definitions, `install.sh:209`,
`renderer.py` render functions, the 29 real project state files) before writing
them down.

**Rejections.** The admission heuristic (`a4272dca`) was applied, and it
excluded four candidates that a blanket intake rule would have admitted:

- **D13 adapter order** (codex → qwen → grok → gemini, moving qwen ahead of
  grok on cost) — a planning recommendation, not a finding. Fails
  *consequential*: nothing breaks if it is ignored. Belongs in the slice plan.
  Same for the note that **Gemini now writes transcripts**
  (`~/.gemini/tmp/<project>/chats/session-*.jsonl`), which downgrades S5 from
  "manufacture a transcript" to "write an adapter for a `$set`-op journal" —
  useful for the S5 brief, not a defect.
- **`renderer.py:301 _tail_lines()` is dead code** — fails *consequential*: no
  failure mode. Noted inside F-15 so it is not re-discovered, without a row.
- **The two unreconciled S2 numbers** — as *findings* they fail
  *consequential*; as a *habit* they are worth fixing, so they are F-P3 in the
  process section rather than a defect row.
- **`memory_wrap`'s non-functional `python3` fallback** — folded into F-13
  rather than given its own row; it is one consequence of one environment
  fault, and two rows for one cause is how a ledger becomes noise.

**One item I could not admit, and it was explicitly requested.** The
compilation brief named "the `memory_wrap` real-client spawn incident once
`s3-serving` posts it". **As of the first compilation `s3-serving` had not
posted it, and nothing in the 53-event log described it.** It failed criterion 1
outright — no evidence, not even a second-hand summary. No placeholder row was
opened, because a row invented by the compiler is worse than no row: it would
carry an id, appear in the index, and assert that something happened that the
compiler cannot show. **It has since taken F-23**, opened on the COO's
timestamped primary evidence at `866c89fe` and not on the request. The refusal
is what forced the evidence onto the record, which is the outcome the heuristic
is for.

**Related, and the same judgement:** F-08 (adapter boilerplate) *was* admitted,
because it has a checkable trigger and a named source even though its evidence
was off-record. The difference is that F-08 states a specific, verifiable claim
about two files in the tree; the spawn incident stated nothing yet. F-08's gap
is now closed too, at `eafd0b50`.

---

**SECOND PASS — the post-ingest gate (`97c8a3cb`), same sitting.** Four rows
updated on coordinator instruction and three admitted on the judge's own
findings.

Updated: **F-08** citation repointed to `runtime/bakeoffs/` and its provenance
caveat dropped. **F-22** Q2 closed against `78f45a48`; Q1 and Q3 stay open and
the row stays deferred. **F-23** opened (above). **F-03** deliberately *not*
closed — its fix is delivered and has passed its gate, and the row is built to
close on the owner's confirmation, which is not on the board. F-01, F-02 and
F-07 carry the gate's measurements.

Admitted, three: **F-23** (criterion 1 now satisfied), **F-24** (evidenced by
demonstration — the digest was inverted, and both predicates were run side by
side), **F-25** (admitted on a *named trigger*, not on an occurrence: zero of
6,701 non-codex sessions match today).

Rejected, three, each naming the criterion it failed rather than being dropped
silently:

- **`docs/adapters.md:121-131` describes pre-fix behaviour** now that F-01's
  fix has shipped. Fails *consequential* — the numbers in it are still correct
  as history and nothing breaks. Noted inside F-01 so it is not re-discovered,
  without a row.
- **`narrative_coverage` scans the conversations directory twice per call**
  (0.33s → 0.57s over 5,718 files). Fails *consequential* — it does not block,
  corrupt, lose or spend. Folded into F-25 as a while-you-are-there note.
- **The delivery's "4 affected fixtures" is 5.** Fails *consequential* as a
  finding — the tree is correct and all five were regenerated. It is a
  reporting error, which is already F-P3, so it is recorded inside F-03 with
  the reconciliation rather than given a row.

One admitted row was **merged rather than added**: the pseudonym residual and
the bare-name residual arrived as two findings and are one row, F-24, because
they are one cause. Two rows for one cause is how a ledger becomes noise.

**Counts current as of `a996002` (`claude/post-ingest`), verified from fresh
clones of the pushed SHAs per F-P1, not from a worktree:** suite **261 passed /
1 skipped** at `a996002` against **247 / 1** at main `e3ae2a7`; **+14 test ids,
0 removed, 0 weakened**. Those 14 run against **pre-fix** source as 16 failures,
and the only two that pass there are the two non-trigger controls. Convener
graph pinned at `a996002`: **988 entities / 4,053 relationships**. Fixture tree
byte-reproducible — two regenerations and the committed tree all hash
`e23870f2abbd157f…` across 30 files. Codex admission **8 → 35 of 124**; **0
sessions lost** across five real projects. Earlier counts for `6ab990e`
(`claude/s3-serving`) stand as written above and were not re-measured here.

---

**THIRD PASS — the S3 delta-verify (`s3-judge`) on `claude/s3-serving-rebased`
at `7b96b22`, same sitting as the PASS verdict.** Three rows closed, one
deferred row's sub-defects closed with the row itself staying deferred, one
row's in-flight status advanced, one row opened, one process rule widened.

Closed: **F-04**, **F-05**, **F-06** — all three re-verified by execution, not
by re-reading the prior verdict as settled. F-21's two runbook defects closed;
the row itself stays **deferred**, its reopen condition unchanged, because the
probe was — correctly, per this job's standing instruction — still not run.
Advanced: **F-07** took a fourth data point, the sharpest so far — the S3
revision closed the exact collision the first data point flagged and still
did not post either graph one-liner for the revision itself. Opened: **F-26**,
the `mcp<2` pin, worded so the closed escalation `09ea13cb` cannot be
mistaken for a solved incompatibility. Widened: **F-P1**, from "verify against
a fresh checkout" to "verify against a fresh checkout **run in a fresh
environment**" — the two are different claims, and today's identical commit
produced three different results on one machine purely by venv.

**Rebase integrity, checked rather than trusted.** `claude/s3-serving`'s
original two commits (`6ab990e`, `76391c7`) sit on `e3ae2a7`;
`claude/s3-serving-rebased`'s two commits (`16be5e0`, `7b96b22`) sit on
`7077c0c`. `git diff e3ae2a7 76391c7` and `git diff 7077c0c 7b96b22` are
**byte-identical** (same sha256, 883 lines each). The file set the S3 branch
touches and the file set main gained between `e3ae2a7` and `7077c0c` **do not
overlap** — checked by set intersection, not by inspection. Nothing was
dropped or silently absorbed in the rebase.

**Counts current as of `7b96b22` (`claude/s3-serving-rebased`), verified from
a fresh `git worktree add` of the pushed SHA and a fresh venv built from
`requirements.txt` (`mcp==1.29.0` resolved) per F-P1 as widened above, not
from any prior worktree and not from this repo's own `.venv`:** suite **271
passed / 1 skipped** against main `7077c0c` at **261 passed / 1 skipped** —
**+10 test ids**, matching the claimed 8 wrapper (`tests/test_memory_wrap.py`)
plus 2 installer (`tests/test_install.py`) tests exactly, 0 removed, 0
weakened. `tools/memory_wrap` is `100755` in the index and on disk from a
genuinely fresh checkout, `core.fileMode=false` confirmed. Convener graph
pinned at `7b96b22`: **1038 entities / 4,276 relationships**, matching the
metadata this row's citations are checked against. `F-23` was not touched by
this pass — it is out of the ledger custodian scope handed to this sitting —
and the S3 verdict posted to the board says explicitly that it closes with
`F-23` resting on one side's evidence plus an unelaborated dispute.
