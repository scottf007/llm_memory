# Codex client memory — plan-gate design note

**Date:** 2026-09-03 · **Author:** llm-pm-4 (PM seat) · **Rigor:** c · **Status:** plan gate; seats launch on `llm-memory-multiclient` after this note is committed

**Owner order (3 Sep 14:59, llm-pm-3's session 6cf9bfdc):** "do it" on the recommendation to keep one PM and close the codex client-memory gap with codex as implementer and acceptance subject: (1) wire the llm_memory MCP server into codex, (2) give codex capture the immediacy Claude's session-end hook has, (3) settle injection by measurement, not preference. The session was closed a minute later; this note is the first artefact of that order.

Everything below was measured on this machine on 3 Sep 2026 unless it cites the upstream doc (`learn.chatgpt.com/docs/hooks`, fetched 3 Sep).

## 1. Codex surfaces, as measured

| Fact | Value |
|---|---|
| `codex --version` | `codex-cli 0.150.1` |
| `codex features list` | `hooks stable true` (enabled by default); `mcp_2026_07_28` and `enable_mcp_apps` under development, off |
| `codex mcp list` | "No MCP servers configured yet" — llm_memory is NOT wired for codex |
| `~/.codex/hooks.json`, `[hooks]` in `config.toml` | absent — no hooks configured |
| Hook config locations (doc) | `~/.codex/hooks.json` or inline `[hooks]` in `~/.codex/config.toml` (user); `<repo>/.codex/hooks.json` or `.codex/config.toml` (project); the binary also carries a `hooks` key pointing at `./hooks.json` for plugins |
| Hook events (doc + binary strings) | `SessionStart` (source `startup`/`resume`/`clear`/`compact`), `SessionEnd` (reason always `other`), `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, `SubagentStart`/`SubagentStop` |
| Hook input | JSON on stdin: `session_id`, `cwd`, `hook_event_name`; commands run with the session `cwd` |
| Hook output that reaches the model | plain stdout is added as context for `SessionStart`, `UserPromptSubmit`, `SubagentStart`; or JSON `{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "..."}}`; `systemMessage` surfaces a UI warning; `continue: false` stops |
| Trust | a non-managed hook must be reviewed and trusted by the user via `/hooks`; trust is recorded against the hook's hash, so any change re-requires review; `--dangerously-bypass-hook-trust` runs enabled hooks without stored trust for one invocation; managed hooks (requirements.toml/MDM) are trusted by policy |
| Rules file | `AGENTS.md` (per project, exists in this repo) or `~/.codex/rules/default.rules` |
| Existing llm_memory pieces | `docs/mcp-wiring-recipes.md` has the verified one-liner `codex mcp add llm_memory -- ~/.claude/memory/lib/.venv/bin/python3 ~/.claude/memory/lib/server.py`; `tools/memory_wrap` + `memory_wrap_clients.json` has a `codex` row (`codex exec <prompt>`); `hooks/install_hooks.sh` writes only Claude's settings; the codex adapter reads `~/.codex/sessions` rollouts; capture today happens only in the sweep at the next Claude session start |
| Grok | `~/.grok/hooks/` empty; Grok's doc says `SessionStart` stdout is ignored; `UserPromptSubmit` probe (F-21, `docs/grok-userpromptsubmit-probe.md`) still unrun; grok is out of credits (host kill-switch) — deferred, not dropped |

**The decisive fact:** codex's hook protocol is the Claude Code protocol. A `SessionStart` hook whose stdout (or `additionalContext`) carries the narrative is the same mechanism `hooks/session_start.sh` uses for Claude today. Injection for codex is therefore not an open question of *whether* but of *trust and packaging*.

## 2. Decisions

**D1 — MCP wiring is an installer step, idempotent.** `install.sh` step 5 gains a codex branch: if `codex` is on PATH, run `codex mcp get llm_memory` and, when absent, `codex mcp add llm_memory -- <lib venv python3> <lib>/server.py` (the recipe already verified in `docs/mcp-wiring-recipes.md`); on failure, log the manual one-liner exactly as the Claude branch does. `install.sh --update` re-runs it (the venv path can move with `LLM_MEMORY_HOME`). Never touches `~/.codex/memories` (D12 stays open).

**D2 — Capture immediacy = a codex `SessionEnd` hook running the existing sweep for that one session.** `hooks/codex_session_end.sh` reads `session_id` from stdin JSON and runs the sweep restricted to that session (`process_transcripts.py` gains `--session <id>` if it lacks it; the seat measures), so `conversations/codex-<sid>.md` exists seconds after the codex session ends, not at the next Claude start. Failure is logged to stdout as `LLM_MEMORY_WARN` (the T-F6 rule: never silent). The full sweep at Claude session start stays as the catch-all.

**D3 — Injection = a codex `SessionStart` hook that emits `resume(project)`-shaped context, chosen by experiment against the two alternatives.** `hooks/codex_session_start.sh` derives the project from `cwd` exactly as `session_start.sh` does, prints the rendered narrative (same budget) and the `AUTOMATIC TASK` line when sessions are unprocessed, and fires only for `source` = `startup`/`resume` (not `compact`/`clear`). The arc runs the three mechanisms as controlled experiments with codex sessions as subjects: (a) the hook, (b) the F-19 rules line in `AGENTS.md`, (c) `tools/memory_wrap codex`. Each subject is a `codex exec` session whose only prompt is "quote the sentinel line from your project context"; the narrative for a scratch project carries a unique sentinel. Pass = the sentinel appears in the session's own rollout output. F-19 is settled either way by measurement.

**D4 — Trust is the owner's, not the installer's.** `install_hooks.sh` writes `~/.codex/hooks.json` (merge-safe: only the `llm_memory` handlers it owns, never other entries) and then tells the owner to run `/hooks` once to trust the two commands; it does not use `--dangerously-bypass-hook-trust`. Seats that need hooks to fire (the D3 experiment subjects) are launched with `--dangerously-bypass-hook-trust` by the experiment harness, explicitly, and only for scratch projects. The am launcher does not pass that flag; whether it should for codex seats is an am question (ticketed, not assumed).

**D5 — Grok is deferred to a follow-up note.** F-21 stays unrun until credits return; the D3 experiment harness is written so the grok `UserPromptSubmit` probe can reuse it.

**D6 — Out of scope:** D12 (codex native memories vs llm_memory), gemini/qwen wiring (recipes exist; no consumer), any change to the delta-extractor prompt spec.

## 3. Mechanical impact map (UAI, graph rebuilt 3 Sep)

Production callables touched: `install.sh` (step 5 and step 6, shell), `hooks/install_hooks.sh` (new codex target), two new hook scripts, `process_transcripts.py` (one optional `--session` filter if absent). `uai callers` for `process_transcripts.main` resolves the session_start sweep and tests/test_process_transcripts_sweep.py; no adapter, server, merger or renderer change. Blast radius is installer + hooks; the store format is untouched.

## 4. Seats (rigor c; author ≠ implementer ≠ judge; codex is implementer AND subject; no grok; no metered spend)

| Seat | Vendor / tier | Produces |
|---|---|---|
| ccm-tests | claude / sonnet | frozen tests: installer branch idempotent (fake `codex` on PATH recording its argv; absent codex = no-op), hooks.json merge-safety (existing foreign entries preserved byte-for-byte), `--session` sweep filter (one session in, one conversation out, others untouched), SessionEnd hook happy path + WARN path, SessionStart hook prints the narrative only for `startup`/`resume`; RED on main |
| ccm-impl | codex / terra | D1, D2, D3 hook, D4 installer; plus the experiment harness `tools/codex_injection_probe.sh` (scratch project, sentinel narrative, three subjects, prints a three-row table) |
| ccm-subject | codex / terra | runs the probe against its own client and posts the measured table with the rollout ids as evidence (the only honest acceptance for "codex saw the narrative") |
| ccm-judge | claude / opus | verdict by execution: re-runs frozen tests, re-runs the probe, checks the live store read-only |

## 5. Acceptance protocol (PM-run, mechanical)

1. `codex mcp list` shows `llm_memory`; a `codex exec` session in this repo calls `resume` (tool call visible in its rollout).
2. End a codex session; within 30 s `conversations/codex-<sid>.md` exists and `narrative_coverage(llm_memory)` lists it, with no Claude session started in between.
3. Probe table: the hook row passes; the rules-line and wrapper rows are recorded as measured (either outcome is a result); F-19 is closed with the measurement.
4. `~/.codex/hooks.json` contains only the llm_memory handlers plus whatever was there before, byte-for-byte for foreign entries.
5. Full default suite 0 failed; live ledger unchanged except by the sweep's own recorded writes.

## 6. Owner decisions to escalate (not pre-empted)

- **Trust model:** one-time `/hooks` trust by the owner (default) vs managed hooks via `requirements.toml`. Default: interactive trust; the installer prints the instruction.
- **AGENTS.md rules line:** ship it in this repo only (default) or also in `~/.codex/rules/default.rules` for every project. Default: repo only, until the probe says it works.

## 7. Amendment 1 (3 Sep 18:55, after the first subject run)

The first subject run (ccm-subject-codex on candidate 514f2ff, event 01788425513336727727) was inconclusive: the subject supplied a fresh scratch `HOME` with no `~/.codex/auth.json`, so every `codex exec` subject failed with `401 Unauthorized` before a model turn, and the harness exited after the second row. The SessionEnd capture path passed (conversation written in 0 s, no Claude session). Harness requirements added to D3:

- **H1 — the harness owns HOME isolation.** It creates a scratch `HOME`, copies exactly `~/.codex/auth.json` from the real home into it (credentials only; never `config.toml`, `hooks.json`, `memories/`, `sessions/`), and runs every subject under that `HOME`. It must refuse to run if the real auth file is absent, with a clear message, rather than producing three 401 rows.
- **H2 — every row runs.** A failing or crashing subject never stops the harness; the table always has three rows.
- **H3 — stderr is evidence.** Each row prints the subject's exit status and the last 3 lines of stderr next to the sentinel result; the table is the acceptance record.
- `--dry-run` additionally prints the scratch HOME layout it would create (which real file is copied, and that nothing else is).

## 8. Amendment 2 (3 Sep 20:0x, after the judge's verdict)

Merged: candidate d4e79f6 → main 8247b02 on ccm-judge-opus PASS (01788427819251055431). Acceptance §5: items 1, 2, 4, 5 met by execution; **item 3 is NOT met** — the probe's rules-line row wrote the sentinel into `AGENTS.md` itself and llm_memory's MCP server was not registered inside the subject's scratch HOME, so the row measured nothing about llm_memory (judge finding F1; the judge's control with the narrative deleted still passed). **F-19 stays open.** The SessionStart hook row is a clean PASS: it ran before any `AGENTS.md` existed with the sentinel only in the narrative (rollout 01a06682-4a28-79d3-82a3-d91e34903616). The wrapper row was never measured (exit 127 in seat worktrees; T-F10). Findings F2–F7 (row independence, a forked new-session counter in `codex_session_start.sh`, hooks.json reformat on first install, matcher width, unconditional hooks.json creation, T6's dry-run contract) are project ticket T-F11, which absorbs T-F10 and is the vehicle to measure F-19 honestly: fresh scratch project per row, the canonical F-19 line, llm_memory MCP registered in the scratch HOME.
