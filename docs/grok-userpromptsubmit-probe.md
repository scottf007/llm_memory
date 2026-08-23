# Grok `UserPromptSubmit` probe (D9)

**Status: UNRUN.** Documented here so it can be run in five minutes by
whoever next has a window where it's safe to touch the one file it needs.
See below for why it wasn't run as part of this branch.

## What this answers

`notes/design-multiclient.md` §2b.2 / D9: Grok's `SessionStart` hook stdout is
confirmed ignored (`~/.grok/docs/user-guide/10-hooks.md:415` — "for events
like `SessionStart` or `PostToolUse`, stdout is ignored"). But Grok also
lists `UserPromptSubmit` as a per-turn event (`10-hooks.md:86`), and that doc
never actually says its stdout is ignored — it says events *like*
`SessionStart` are, which conspicuously excludes `UserPromptSubmit`. In
Claude Code, `UserPromptSubmit` stdout **does** reach context. If the same is
true for Grok, firing the memory narrative on a session's first
`UserPromptSubmit` is a genuine automatic injection path — no rules line, no
`tools/memory_wrap`, no obedience dependency. If it's false, the wrapper
(D10, `tools/memory_wrap`) is the only mechanism Grok gets.

This is a one-bit, falsifiable question. The procedure below answers it
directly rather than by inference from documentation.

## Why this wasn't run now

The only hook-config surface Grok trusts is the single shared, global
`~/.claude/settings.json` — confirmed on this machine both by the design
note (`10-hooks.md:66`, "always-trusted global hook location") and
independently here via `grok inspect` from a scratch directory outside any
project, which reported `Permissions / Source: /home/user/.claude/settings.json`
with no project- or cwd-scoped alternative offered. There is no
`hooks-paths` entry, no `.grok/settings.json` override, and no `GROK_HOME`-
style env var that redirects hook discovery to an isolated file (`grok
--help` has no such flag; `~/.grok/hooks-paths` and `~/.grok/hooks/` are
both empty on this machine).

That means running the probe means editing the live, shared
`~/.claude/settings.json` that this very job's other seats' Claude Code
sessions are running under concurrently (`am status` showed `s1-adapter` and
`s1-judge` seats active during this session). That fails "safely touched,
read-mostly" — it's a write to a shared file multiple live sessions depend
on for their own hook behavior, not a read. Per this job's own policy
("claim shared paths ... before changing or starting them") and the S3
scope note ("no live client config mutation beyond the probe" — the probe
itself still has to be safe to run), the right call is to write the runbook
and leave it unrun rather than mutate a file other active seats are relying
on mid-session.

**Run it when:** no other seat/session has a live Claude Code or Grok
process depending on `~/.claude/settings.json` staying stable — e.g. a
dedicated window with no concurrent job activity — or once a project-scoped
/ sandboxed hook surface exists for Grok (worth filing as its own follow-up
if this probe proves `UserPromptSubmit` is worth building on).

## Procedure (five minutes)

1. **Snapshot first.**
   ```bash
   cp ~/.claude/settings.json ~/.claude/settings.json.pre-grok-probe.bak
   ```

2. **Add a sentinel `UserPromptSubmit` hook — with a positive control.** A
   hook that only prints a sentinel can't distinguish "stdout is ignored"
   from "the hook never fired at all" (wrong file, malformed JSON, Grok not
   reading `~/.claude/settings.json` the way the docs claim). Give it a
   side effect that doesn't depend on stdout reaching the model, so there's
   an independent trigger/non-trigger control: edit `~/.claude/settings.json`
   and add, under `hooks`:
   ```json
   "UserPromptSubmit": [
     {
       "hooks": [
         {
           "type": "command",
           "command": "echo 'MEMORY_PROBE_SENTINEL_7f3a2c'; date >> /tmp/grok-probe-fired.log"
         }
       ]
     }
   ]
   ```
   (If a `UserPromptSubmit` array already exists — it doesn't on this
   machine as of this branch — append to it rather than replacing it.)

3. **Run one Grok turn in a throwaway project** (not this repo, so a bad
   result can't contaminate a real session), in single-turn headless mode so
   it actually returns a reply instead of opening the interactive TUI
   (`grok "prompt"` — the bare positional form — launches the TUI, which
   won't work piped into a read-the-output step; use `-p`/`--single`):
   ```bash
   rm -f /tmp/grok-probe-fired.log
   mkdir -p /tmp/grok-probe-scratch && cd /tmp/grok-probe-scratch
   grok -p "Without using any tool, reply with only the exact sentinel string \
   you can see above this message in your context, or the single word NONE \
   if you cannot see one." --output-format plain
   ```

4. **Read the result directly, don't infer it — check the control before
   trusting a negative.**
   - `/tmp/grok-probe-fired.log` empty → the hook never fired. Inconclusive,
     not negative — fix the install (wrong file, bad JSON, Grok not reading
     `~/.claude/settings.json` on this build) and rerun before drawing any
     conclusion about `UserPromptSubmit`.
   - Log has an entry (hook fired) and model replies
     `MEMORY_PROBE_SENTINEL_7f3a2c` → `UserPromptSubmit` stdout reaches
     context. **D9 resolves positive.** Grok graduates from
     obedience-dependent to automatic injection; `hooks/session_start.sh`'s
     approach (or a Grok-specific equivalent) becomes viable for Grok, and
     `tools/memory_wrap` demotes from "the mechanism" to "the fallback" for
     Grok specifically (per §2b.6 — it stays the mechanism for Codex,
     Gemini and qwen regardless of this result).
   - Log has an entry (hook fired) but model replies `NONE` or anything
     else → **D9 resolves negative, with a real control behind it.**
     `tools/memory_wrap` remains the only non-obedience-dependent path for
     Grok, same as the other three obedience-dependent clients.

5. **Restore immediately, whatever the result:**
   ```bash
   mv ~/.claude/settings.json.pre-grok-probe.bak ~/.claude/settings.json
   rm -rf /tmp/grok-probe-scratch /tmp/grok-probe-fired.log
   ```

## What this probe does not test

- Whether Grok's hook payload includes enough to derive `project` on
  `UserPromptSubmit` the way `session_start.sh` does from `cwd` — a second,
  separate check once/if D9 resolves positive.
- Whether the *narrative itself* (not just a short sentinel) fits in
  whatever Grok does with `UserPromptSubmit` stdout, e.g. a size limit.
  A positive sentinel result is necessary but not sufficient to wire real
  injection — treat it as "go build the real thing," not "done."
