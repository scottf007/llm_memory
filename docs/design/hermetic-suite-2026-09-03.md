# Hermetic default suite — plan-gate design note

**Date:** 2026-09-03 · **Author:** llm-pm-4 (PM seat) · **Rigor:** c · **Status:** plan gate; seats launch on the `project` board after this note is committed

**Ticket:** project 01788390843831275711-llm-pm-3-3bd38ad8 (SUITE HEALTH: 18 tests fail on main on the dev machine). Queue item 1 of the llm-pm-4 charter.

Everything below was measured on this machine on 3 Sep 2026 against main `b744e84`, by execution, not by reading the ticket.

## 1. What is actually wrong, measured

| Run | Result | Wall time |
|---|---|---|
| `pytest tests/` with the real `$HOME` | 641 passed / **18 failed** / 3 skipped | 95 s |
| `pytest tests/` with `HOME=<empty dir>` and `LLM_MEMORY_HOME=` | **607 passed / 0 failed** / 36 skipped | 24 s |

The suite is already hermetic on a machine without the owner's stores: every reader of `~/.claude/memory`, `~/.codex/sessions` and `~/.grok/sessions` skips with a named reason when the store is absent (the 26 Aug CI-portability arc, `e758073`). The dev machine is the only place the suite is red, and it is red because the live-conditional rows *run* here and the data they were pinned to has moved.

The 18 failures have two causes, not eighteen:

1. **17 rows share one assertion.** `tests/fixtures/certification/live_ledger.py:33` pins the sha256 of the owner's live `projects/llm_memory.json` to the 24 Aug evidence pack (`f3d6e0b8…`). The live file is now `35564275…`; it changes on every merge/sweep. The loader fails loudly by design ("needs independent re-verification, not a blind re-pin"). The 17: test_archive_class ×6, test_claim_match ×5, test_certify ×2, test_cascade ×2, test_renderer::test_founding_case_omitted_before_ranking, test_live_corpus_guard::test_live_ledger_loader_runs_when_present. No copy of the ledger at the pinned hash exists anywhere on this machine (searched `~/.claude/memory`, every board runtime dir, every worktree), so a re-pin against the original pack is impossible; a re-pin against today's file would rot within a day.
2. **1 row is an over-broad live guard.** `test_codex_adapter.py::test_no_real_session_silently_yields_zero_turns` walks every real codex rollout and asserts each yields a user turn. The offender, `rollout-2026-08-27T06-47-20-01a03fd3-…jsonl`, is a two-record file (`session_meta` + `event_msg/task_started`) from an am smoke test under `/tmp/…/jobs/smoke/…`: it never had a user message, so zero turns is the correct answer and the guard's premise ("a session with content that renders empty") does not apply. This is not the legacy-schema gap `f08-codexdrift-codex` documented (those are archived `transcripts/codex-*.jsonl`, a different corpus).

The population of live-conditional rows is **33** (36 fake-HOME skips minus the 3 permanent wall-clock exclusions): the 17 above, the codex guard, plus 15 that pass today only because today's store happens to satisfy them and will drift the same way (test_codex_adapter ×2, test_grok_adapter ×3 live-store census rows, test_narrative_coverage ×4 `test_live_*`, test_adapter_oracle ×3 corpus checks, test_rank_below ×1 live panel, test_live_corpus_guard ×1 replay meta-test, test_replay_oracle rows via the replay loader).

## 2. Decisions

**D1 — Live-corpus rows are opt-in, default run is the fake-HOME run everywhere.** Introduce a pytest marker `live_corpus`. `pyproject.toml` gains `[tool.pytest.ini_options]` with `markers = ["live_corpus: reads the owner's live stores (~/.claude/memory, ~/.codex, ~/.grok); opt in with -m live_corpus"]` and `addopts = "-m 'not live_corpus'"`. `pytest tests/` on any machine, including this one, collects exactly what the fake-HOME run collects. `pytest -m live_corpus tests/` runs the 33 against the real stores. Rejected: making the loaders skip on drift (hides the drift the loader was built to expose; contradicts its own docstring); re-pinning (no original pack; rots daily); deleting the 17 rows (they are the Tier-1 spec's evidence rows and `test_ci_portable_cert.py` covers the same production paths synthetically, so they keep value as an opt-in re-verification set, not as a gate).

**D2 — Marking is dynamic in `tests/conftest.py`, the frozen files stay byte-identical.** `pytest_collection_modifyitems` applies the marker from one explicit, sorted list `LIVE_CORPUS_NODE_IDS` (33 entries, pinned in this note's acceptance) plus the rule "any test function whose name starts with `test_live_`". The Tier-1 frozen modules (`test_archive_class`, `test_cascade`, `test_certify`, `test_claim_match`, `test_renderer`, `test_replay_oracle`) are not edited. Rejected: decorating each test (touches six frozen modules for a bookkeeping change; the 26 Aug arc set the precedent that portability changes live in the loaders/conftest, not in frozen bodies).

**D3 — The codex zero-turn guard keeps its intent and loses its false positive.** In `test_no_real_session_silently_yields_zero_turns`, a rollout is only a candidate if its raw file contains at least one user-authored message record (an `event_msg` of the user dialects the adapter recognises, or the legacy top-level `user` shape); a session with no user record at source is not "a session that would vanish", it is an empty session. The test stays under `live_corpus`. Frozen tests pin both sides: a session_meta+task_started-only rollout is not reported (non-trigger control), a rollout with a real user record that the adapter renders as zero turns is reported (trigger).

**D4 — Explicit node ids are still runnable.** pytest deselects marker-filtered tests even when named on the command line, so `docs/testing.md` (new, short) records: default run, `-m live_corpus` for the live set, `-m ""` to clear the filter for a single node id. `.github/workflows/test.yml` is unchanged: `python -m pytest tests/ -v` is now hermetic by construction rather than by absence of the stores.

**D5 — Out of scope, ticketed separately.** (a) Whether the 17 spec-era rows should be re-verified against a fresh evidence pack and re-pinned, or retired in favour of `test_ci_portable_cert.py`: a spec-evidence decision for the owner, not a hermeticity fix. (b) The legacy top-level `user`/`assistant` codex transcript schema (356 archived files, documented in `docs/adapters.md`): an adapter compatibility ticket, unchanged by this work. (c) T-F6 (session_start.sh silent NEW_SESSIONS=0) and T-F5 (A3.5 follow-ups): separate rigor-d tickets already on the board.

## 3. Mechanical impact map (UAI, fresh graph built 3 Sep on b744e84)

`uai callers tests.fixtures.certification.live_ledger.load_live_state` → 18 test functions across test_archive_class, test_cascade, test_certify, test_claim_match, test_renderer, test_live_corpus_guard (the 17 failing rows + the absent-file meta test). `uai callers adapters.codex.discover` → 5 test functions in test_codex_adapter (three live: `test_no_real_session_is_mixed_dialect`, `test_every_real_session_parses_without_crashing`, `test_no_real_session_silently_yields_zero_turns`) + `tools/make_codex_fixtures.select`. `uai callers tests.fixtures.certification.replay_oracle.load_snapshot` → test_replay_oracle::test_source_pins_match, test_live_corpus_guard ×2, replay_oracle.build_oracle. **No production callable changes.** Blast radius is `tests/conftest.py`, `pyproject.toml`, one test function body in `tests/test_codex_adapter.py`, and a new `docs/testing.md`.

## 4. Seats (rigor c; test author ≠ implementer ≠ judge; no grok seats, no metered spend)

| Seat | Vendor / tier | Consumes | Produces |
|---|---|---|---|
| hermetic-tests | claude / sonnet | this note | frozen tests: (T1) collection contract — with the repo's config, `--collect-only -q` default yields no `live_corpus` item and `-m live_corpus --collect-only -q` yields exactly the pinned 33 node ids; (T2) `test_live_`-prefix rule and explicit-list rule both mark (trigger) and an unrelated test is unmarked (non-trigger); (T3) D3 trigger + non-trigger fixtures on a temp `SESSIONS_DIR`; (T4) `docs/testing.md` exists and names all three invocations. Frozen at a recorded sha256 manifest; RED proof on b744e84 |
| hermetic-impl | codex / terra | frozen tests + this note | conftest marking, pyproject config, D3 guard fix, docs/testing.md; frozen modules byte-identical (sha256 before/after recorded) |
| hermetic-judge | claude / opus | the delivered blob | verdict by execution: re-run the acceptance protocol below, not the report |

## 5. Acceptance protocol (PM-run, mechanical, before merge)

1. `sha256sum` of the six frozen modules before and after: identical.
2. `pytest tests/` with the real `$HOME`: 0 failed; deselected count = 33; pass/skip node sets identical to the same command under `HOME=<empty dir>` (the fake-HOME control), and no skip reason mentions a live store under either.
3. `pytest -m live_corpus tests/` with the real `$HOME`: collects exactly 33; the 17 ledger-pinned rows still fail with the drift message (the drift is still visible, on purpose); the codex guard passes; nothing else changes.
4. `pytest -m "" tests/test_archive_class.py::test_unclassified_trio_exact` runs (does not deselect).
5. Live store untouched: sha256 of `~/.claude/memory/projects/llm_memory.json` and `memory.db` identical before and after every seat.
6. GitHub Actions `Tests` workflow: green on the merged sha (it was already green on clean runners; this proves the config change did not break collection there).
