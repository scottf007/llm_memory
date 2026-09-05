# Fresh-install proof — plan-gate design note

**Date:** 2026-09-06 · **Author:** llm-pm-4 (PM seat) · **Rigor:** c · **Status:** plan gate; first seat launched under the 6 Sep continuous-pull rule (chairman 01788651030218353394)

**Product gap (roadmap Tier 3, items #13 and #17):** llm_memory is proven only on the owner's machine. The first real fresh install (25 Aug) "did not work"; B-3 then verified an install inside an `ubuntu:24.04` container by hand, and nothing has re-verified it since. Every later change to `install.sh` (B-3/B3-N1 venv deps, B-4 store root, T-F12 self-replace, T-F13 codex hooks, the codex MCP step) shipped with unit tests that fake the tarball and the client CLIs, never with a real install on a clean machine. For the owner this is the difference between "I can give this to someone" and "it works for me".

## 1. What exists, measured 6 Sep

| Fact | Value |
|---|---|
| Installer entry | `install.sh` (22 KB): fetches the GitHub tarball of `origin/main`, copies into `$LLM_MEMORY_HOME/lib` (default `~/.claude/memory/lib`), creates a venv, installs `requirements.txt`, registers the MCP server with Claude (`claude mcp add-json`) and with codex when present, installs hooks (`hooks/install_hooks.sh`), skills, agents; `--update` re-execs itself from a temp copy since T-F12 |
| Existing tests | `tests/test_install.py` (33) + `tests/test_install_update_self_replace.py` (5): fake `$HOME`, fake tarball source, fake `claude`/`codex` on PATH — they prove the script's logic, not a real machine |
| CI | `.github/workflows/test.yml` runs `python -m pytest tests/` on Python 3.10–3.12; no job installs the product |
| Prior real-machine evidence | B-3 (26 Aug): `ubuntu:24.04` container, `python3-venv` missing → fixed (B3-N1), `tzdata` prompt hang → fixed; the 17 Aug half-installed-lib self-check (`fd474bf`) exists because an update once shipped code without its package |
| Container tooling on this host | to be measured by the test seat: `docker` or `podman` availability, and whether GitHub Actions can run the same container job |

## 2. Decisions

**D1 — The proof is a real install in a throwaway environment, run in CI and locally.** A new workflow job (`fresh-install`) starts from `ubuntu:24.04`, installs only what the README tells a stranger to install, runs `install.sh` against the checked-out tree's tarball equivalent (the job must exercise the same code path as the GitHub fetch: build the tarball from the checkout and serve it to the installer via the `LLM_MEMORY_TARBALL_URL`/override the installer already supports for tests, or add one), then verifies: lib present with `VERSION`, venv imports `mcp`, `server.py` answers an MCP `list_tools` over stdio with the four tools, hooks installed into a scratch `~/.claude/settings.json`, a session-start hook run prints a narrative for a seeded project, and `process_transcripts.py` ingests a seeded Claude transcript. No real `claude` binary in the container: the Claude MCP registration step must degrade to the documented manual one-liner without failing the install (it already does), and that branch is asserted.

**D2 — Same script, same machine.** The job's steps are one script, `tools/fresh_install_check.sh`, runnable locally with `podman`/`docker` so a PM can reproduce a CI failure here; the workflow just invokes it.

**D3 — What "works" means is asserted, not eyeballed:** every check above is a non-zero-exit assertion with a one-line reason; the job's summary prints the installed `VERSION` and the four tool names.

**D4 — Out of scope:** publishing/release (Tier 4, owner-gated), non-Ubuntu targets, codex/grok binaries inside the container (their branches must no-op cleanly and be asserted to), and any installer feature work beyond what the proof finds broken; defects found become tickets and are fixed in follow-up seats, not absorbed.

## 3. Mechanical impact map

Production files: `install.sh` (only if a defect is found), `.github/workflows/test.yml` (new job), new `tools/fresh_install_check.sh`, README install section if the stranger's steps are wrong. `uai callers` is not meaningful for shell; blast radius is CI plus one tool script.

## 4. Seats (rigor c; author ≠ implementer ≠ judge; no grok; no metered spend)

| Seat | Vendor / tier | Produces |
|---|---|---|
| fresh-tests | claude / sonnet | frozen tests: (T1) `tools/fresh_install_check.sh --dry-run` prints the ordered check list; (T2) a hermetic run of the check script against a fake install root asserts each verification step's failure message (seeded broken states: missing VERSION, venv without mcp, missing hooks) and the success path; (T3) workflow file contains the `fresh-install` job invoking the script on `ubuntu:24.04`; measured: container tooling on this host |
| fresh-impl | codex / terra | the script, the workflow job, the tarball-serving override if missing; a real local container run recorded (log attached), and the CI run green on the branch |
| fresh-judge | claude / opus | verdict by execution: re-runs the container locally, reads the CI run, mutation on one verification step |

## 5. Acceptance (PM-run)

1. CI `fresh-install` job green on main after merge; the run's summary shows `VERSION` = merged sha and the four tool names.
2. `tools/fresh_install_check.sh` run locally in a container on this host: same result.
3. Default test suite still 0 failed; installed lib on this machine untouched (the proof never runs against the real `$HOME`).
4. A SHIPPED.md line: "llm_memory now proves on every push that a stranger can install it on a clean Ubuntu machine".
