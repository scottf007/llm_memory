# RELEASE-READINESS PASS — llm_memory

Prepared 2026-08-23 by `llm-memory-pm`, per owner decision `effe7f2e` (5.1):
**publish is NOT YET; prepare the pass so it is ready when Scott says go.**

This document is the pass. It is **not run**. `origin/main` stays at `e3ae2a7`;
local `main` is **10 commits ahead** and nothing has been pushed.

Every item below is either measured (a command and its output) or cited to a
ledger row. Items marked **BLOCKER** must clear before a push. Items marked
*should* are judgement calls for Scott.

---

## 0. THE ONE-PARAGRAPH SITUATION

The `mcp<2` escalation was argued on the grounds that *a new user's first run is
the whole audience*. We then fixed it on a repository no new user can reach.
That tension is the reason this pass exists: the moment we publish, every defect
whose only victim is a first-time installer becomes real. This pass is ordered by
"what does a stranger with a clean machine hit first".

---

## 1. BLOCKERS

### B-1 — Real project names are still in the repo, in three test files
**This is F-03's class, and F-03 was ruled SCRUB by the owner. F-24 records "two
residuals"; the real count is larger and one of them is a default value.**

Measured `2026-08-23` on `main`:

| file | occurrences | shape |
|---|---|---|
| `tests/test_merger.py` | 17 × `finance_nexus` | **the default project name** in `_write_state(path, project="finance_nexus")` |
| `tests/test_codex_adapter.py` | 3 × `universalai`, 3 × `fletchcorp` | inside the F-03 **regression guard** — a tuple of `real_names` the test asserts must never ship |
| `tests/test_renderer.py` | 1 × `finance_nexus` | prose in a module docstring ("finance_nexus reached ~33k tokens") |

The codex-adapter case deserves a straight answer rather than a reflex. That
tuple is the *guard* — the test that proves the scrub works, and it needs real
strings to be a real trigger control. Deleting it weakens a test that exists
because an owner ruling was reversed once already. But the names ship either
way: a public repo containing `("agent-messaging", "universalai", "fletchcorp")`
has published those names, whatever the surrounding assertion says.

**Recommended fix, and it keeps the guard:** move the real-name list out of the
source tree into an untracked local file (or an env var) that the test reads,
skipping with a clear message when absent. The guard then runs in full on
Scott's machine and in any private CI, and a public clone runs everything else.
`finance_nexus` in `test_merger.py` and `test_renderer.py` has no such defence —
it is a placeholder that happens to be real, and should just become
`example_project`.

**Owner call needed:** Scott, these are your project names. B-1 assumes you want
them out. Say so if you do not — it is a one-line ruling and it changes the fix.

### B-2 — `server.py` is incompatible with `mcp` 2.x (F-26)
Deferred by owner decision (b): the pin holds, the port folds in **here**. That
placement is deliberate — the port becomes due at exactly the moment there are
new users. So on a publish decision, this stops being deferred and becomes work.
Reopen condition, already written into F-26: anything that forces `mcp>=2` into
the resolved dependency set.

### B-3 — A fresh install has never been verified on a machine that is not this one
The `mcp<2` defect existed for months and was invisible because every working
machine resolved its dependencies in March. That is a category, not an incident:
**we have no evidence about first-run behaviour except for the one bug we
tripped over.** Nothing in this repo's history records a clean-machine install.

Required before push: run `install.sh` in a container with no `~/.claude`, no
existing venv, and no `mcp` in any cache. Record the transcript. The pilot board
already found the adjacent version of this on agent-messaging — `pip install -e`
hit PEP 668 and produced no `am` on PATH (`codex-pilot`, `01786627849021242593`).

### B-4 — The store path is hardcoded in ~38 places (F-18 / D2)
`grep` finds ~38 sites across `.py`/`.sh` computing `~/.claude/memory`
independently, with no shared resolver. For a published project this is worse
than untidy: it hardcodes *another vendor's* directory as the storage root, and
a user with no Claude Code install has nowhere sensible for it to go.

Scott already ruled the scope (17 Aug): a generic folder, **the variable in the
application, not the vendor name**. `merger.py:38 _memory_root()` is the pattern
to generalise. This is queued as a slice and **should land before publish**, not
after — it changes public API-ish surface (an env var users will set).

---

## 2. SHOULD-FIX — first-run experience

Each of these is an existing ledger row whose only victim is a new user.

| row | what a stranger hits |
|---|---|
| **F-19** | the rules line has never been observed to work on *any* client — the documented integration may simply not function |
| **F-11** | `memory_wrap` reports a missing project to the model but not to the user: it looks like it worked |
| **F-12** | `resume` turns an empty `conversation_md` into a directory-read error |
| **F-14** | the standalone oracle script exits 0 with no corpus — a green run that checked nothing |
| **F-13** | the dev `.venv` holds `mcp` 2.0.0 and cannot collect the suite; a contributor's first `pytest` fails |

**F-19 is the one to take seriously.** A documented feature nobody has ever seen
work is a README claim we cannot support. Either verify it on one client or
soften the docs before publishing.

---

## 3. THE THING THAT IS NOT ON ANY LIST

**The narrative pipeline can die silently, and did — 13 days, on this very
repository, while ingestion ran perfectly** (T-01). A stranger who clones this,
installs it, and uses it for a fortnight may get exactly what we got: a store
full of transcripts, a narrative frozen on day one, and no signal that anything
is wrong.

That is not a bug in a subsystem; it is the product's central promise failing
without saying so. Publishing before it is fixed means shipping the failure mode
we know about and have not guarded. **This is the queued next slice on job
`llm-memory-pipeline`** and it is the strongest argument for `NOT YET` being the
right call.

---

## 4. HYGIENE — mechanical, no judgement needed

- Working tree carries **19** `tmp_delta_*.json`, plus `tmp_before_rerun_*/` and
  `tmp_old_narrative.md`. All are gitignored (`tmp_*` covers them) so none would
  ship, but they should be cleared so `git status` is readable.
- Untracked `AGENTS.md`, `CLAUDE.md`, `HOW-WE-WORK.md` are agent-messaging
  scaffolding, not product. Decide: gitignore them, or ship `HOW-WE-WORK.md`
  deliberately as a contributor doc.
- Secrets scan run 2026-08-23 across `*.py`/`*.sh`/`*.json`: **no literal
  credentials found**. Re-run immediately before push, not now.
- `LICENSE`, `README.md`, `CONTRIBUTING.md`, `CHANGELOG.md`, `RELEASE_NOTES.md`
  all present. README was corrected in S3 (`7b96b22`); re-read it end to end
  against actual behaviour before push — it is the first thing anyone reads and
  the last thing anyone verifies.
- `docs/FEEDBACK.md` now ships (merged `3c2aa42`). It is candid about defects,
  which is a good look for an open-source project — but confirm Scott wants the
  internal ledger public. **Owner call.**

---

## 5. ORDER OF OPERATIONS, when Scott says go

1. B-1 scrub + owner ruling on the guard.
2. `LLM_MEMORY_HOME` slice (B-4) — before publish, it changes user-facing config.
3. Narrative-liveness slice (§3) — the silent-death guard.
4. B-2 `mcp` 2.x port.
5. B-3 clean-container install, transcript recorded.
6. §2 first-run fixes, at least F-19 verified-or-softened.
7. §4 hygiene, secrets re-scan, README re-read.
8. Push. `origin/main` moves from `e3ae2a7` for the first time since 17 August.

**Nothing above is started.** This document exists so that when the answer
changes from NOT YET to go, the pass is a checklist and not a design session.
