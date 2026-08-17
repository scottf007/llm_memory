# MCP wiring recipes for non-Claude clients

Recipes only — running one of these commands registers the `llm_memory` MCP
server with *that* client's own config; nothing here is applied automatically
and nothing outside this repo is touched by adding this file. See
`notes/design-multiclient.md` (branch `claude/mc-design`) sections 2, 2b and
decisions D8/D9 for the reasoning; this file is the "how", not the "why".

Every tool call these clients make against `llm_memory` is read-only
(`memory_search`, `narrative_coverage`, `resume`, `project_lookup` —
see `server.py:52-160`). Wiring the server cannot cause a write.

## Claude (reference — already done)

Nothing to run. `~/.claude.json` already registers `llm_memory` at user
scope, stdio, `~/.claude/memory/lib/.venv/bin/python3 ~/.claude/memory/lib/server.py`.
Injection is automatic via `hooks/session_start.sh` stdout — see
`docs/mcp-wiring-recipes.md`'s sibling sections below for why every other
client needs the extra step this file documents.

## Codex

Not wired today — `~/.codex/config.toml` has no `mcp_servers` section.

```bash
codex mcp add llm_memory -- ~/.claude/memory/lib/.venv/bin/python3 ~/.claude/memory/lib/server.py
```

Verified against this machine's `codex mcp add --help`: `codex mcp add <NAME> -- <COMMAND>...` is exactly this shape, and it writes to `~/.codex/config.toml` globally (no scope flag — there is only one scope).

Rules-file line: put the text from **"The rules line (all clients)"** below
into this repo's `AGENTS.md` (preferred — per-project, so it can name the
project without guessing) or `~/.codex/rules/default.rules` (global, every
project). `AGENTS.md` already exists in this repo; add the line to it rather
than creating a new file.

Native memory caveat (D12): Codex also has its own `~/.codex/memories/` /
`memories_1.sqlite`. Wiring `llm_memory` does not touch that store — the two
are separate until D12 is actually decided.

## Gemini CLI

Not wired today — `~/.gemini/config/mcp_config.json` exists but is empty.

```bash
gemini mcp add llm_memory ~/.claude/memory/lib/.venv/bin/python3 -- ~/.claude/memory/lib/server.py -s user
```

Verified against this machine's `gemini mcp add --help`: syntax is
`gemini mcp add <name> <commandOrUrl> [args...]`, default transport is
`stdio`, default scope is **project** — pass `-s user` (as above) to make it
available everywhere the way Claude's registration already is, or drop it to
scope the server to this repo only.

Rules-file line: add it to this repo's `GEMINI.md` (create if absent).

**Antigravity is a separate configuration surface.** `~/.gemini/antigravity-cli/settings.json`
is not touched by `gemini mcp add` — if Antigravity needs `llm_memory` too,
it needs its own wiring, not covered by the command above. Not attempted
here; out of scope for this recipe (docs only, per §2b.4 of the design note).

## Grok

**Already resolved — nothing to run.** `~/.grok/sessions/*/*/events.jsonl`
already logs `llm_memory` in `mcp_config_resolved`, sourced from Claude's
user-scope config (`~/.claude.json`). Grok picks it up because it reads that
file directly; there is no separate `grok mcp add` step.

Rules-file line: add it to the project-visible Grok rules file (Grok reads
`~/.claude/settings.json` for hook compatibility, but rules text is a
separate, project-local file — follow whatever your Grok install's docs
name for that; not modified here).

What wiring does *not* solve for Grok: push injection. `SessionStart` stdout
is ignored by Grok (`~/.grok/docs/user-guide/10-hooks.md:415`). The rules
line below is obedience-dependent until the `UserPromptSubmit` probe
(D9, see `docs/grok-userpromptsubmit-probe.md`) proves otherwise, or until
`tools/memory_wrap` is used as the launch wrapper instead.

## qwen-local (bonus — not in the D8 item list, included for completeness)

Not wired today — gemini-CLI-derived, `~/.qwen/settings.json` has no MCP
block. If `qwen mcp add` exists it almost certainly mirrors gemini's syntax
above; not verified on this machine (`qwen` binary is present but MCP
subcommand support was not probed here — see design note §3.5, "whether a
local model can drive stdio MCP tool calls reliably at all" is the bigger
open question, and is not resolved by wiring alone). Per the design note,
assume the rules-line path does not hold on a local 27B model regardless —
`tools/memory_wrap` (D10) is the realistic path for qwen.

## The rules line (all clients)

Verbatim from the design note §2.4 (D8) — every clause maps to a specific
observed failure mode; do not shorten it:

> **Memory.** At the start of every session, before answering anything, call
> the `llm_memory` MCP tool `resume` with `project` set to the last path
> segment of the repository root (for `/home/scott/projects/llm_memory`, that
> is `llm_memory`). If it returns nothing useful, call `project_lookup` with
> the same `project` and the topic you are about to work on. Do this without
> asking permission. If both return empty, say so explicitly rather than
> assuming the project has no history.

Where it goes, per client:

| Client | File |
|---|---|
| Codex | `AGENTS.md` (preferred, per-project) or `~/.codex/rules/default.rules` (global) |
| Gemini CLI | `GEMINI.md` |
| Grok | the project-visible Grok rules file |
| qwen-local | `QWEN.md` — but see the qwen caveat above; the rules line is not expected to be obeyed reliably at this model size |

None of these files were created or edited by this recipe doc — adding the
line is a separate, deliberate step for whoever wires a given client, not
something this branch does on their behalf.
