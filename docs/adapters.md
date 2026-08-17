# Client adapters

llm_memory ingests sessions from more than one LLM client. All of the
client-specific knowledge lives in `adapters/`; everything downstream of
`~/.claude/memory/conversations/<sid>.md` is client-agnostic and does not
change when a client is added.

```
~/.claude/projects/**.jsonl  ──┐
~/.codex/sessions/**.jsonl   ──┤   adapters/<client>.py
~/.grok/sessions/**/…        ──┘        │
                                        ├──► adapters/render.py
                                        │         │
                                        │         ▼
                                        │   conversations/<sid>.md
                                        ▼         │
                              transcripts/<sid>.jsonl
                                                  │
                                   delta-extractor → merger → renderer
                                          (know nothing about clients)
```

## The protocol

An adapter is a module, not a class. Four functions:

| Function | Returns |
|---|---|
| `discover()` | `Iterable[SessionRef]` — every session this client has on disk |
| `session_meta(ref)` | `SessionMeta` — id, project, started, ended, subagent flag |
| `turns(ref)` | `Iterable[Turn]` — `(role, timestamp, text, had_tool_use, raw_line)`, in source order |
| `client_name()` | `"claude"` / `"codex"` / `"grok"` / … |

Register it in `adapters/__init__.py`. `adapters.base.conforms()` runs at
import and raises if a function is missing, and a test fails if a module in
`adapters/` was never registered — an unregistered adapter has no error
message, its client's sessions simply never appear.

Adapters for clients that store one session per file may also expose
`ref_for_path(path)`. It is a convenience, not part of the protocol.

## What the adapter does *not* do

- **It does not group turns.** Yield one `Turn` per source entry, in order.
  The shared renderer merges consecutive assistant entries into one block,
  picks the block timestamp, and attaches the `[L:N]` source-line ref. Every
  client gets identical block structure for free.
- **It does not decide the project.** Use `base.project_from_cwd()`. The
  `.../projects/<name>/...` convention is shared across clients, so it stays
  one function.
- **It does not write files.** `adapters.render()` returns a string; the
  caller decides where it goes.

A `Turn` may carry `had_tool_use=True` with empty `text`: a tool-only entry
contributes its line reference to the surrounding block but no prose.

## Adding a client

1. Write `adapters/<client>.py` with the four functions.
2. Register it in `adapters/__init__.py`.
3. Give it a session-id prefix that does not collide with `agent-` or
   `audit-`; those stems are already load-bearing filters in three places, and
   `transcripts/` is one flat directory, so two clients' UUIDs could otherwise
   overwrite each other.
4. Write the canonical envelope. `server.py`'s `_count_user_turns`,
   `_transcript_tail_ts` and `_find_project_transcripts` read raw JSONL and
   assume Claude's shape. Rather than teaching them every client format, a
   foreign adapter writes a minimal Claude-shaped JSONL to
   `transcripts/<sid>.jsonl` and records its own path as `raw_source:` in the
   frontmatter.
5. Self-check the envelope against `_count_substantive_user_turns` — use
   `adapters.verify_envelope()`. A session whose envelope counts zero user
   turns is dropped from `narrative_coverage` with no error and no log line;
   memory just quietly gets a hole in it.
6. **Read the client's own transcripts before believing the format survey.**
   See below.

## What the codex adapter cost, and what it teaches

The scoping survey said codex turn text lives in `event_msg` payloads of type
`user_message` / `agent_message`. That is true of 123 of the 127 sessions on
this machine, and false in a way that costs you the best data:

- `codex_exec` sessions use that shape.
- `codex-tui` sessions instead emit `event_msg` / `item_completed`, with the
  text under `payload.item.content[].text` and the kind in `payload.item.type`
  (`UserMessage` / `AgentMessage`).

An adapter written to the surveyed shape renders every TUI session as an empty
conversation — including the richest codex session on disk, 10,571 records and
74 real user turns. No crash, no warning: the session parses fine and produces
nothing. **A format survey tells you what one session looked like. Only a
census over every session on disk tells you what the client emits.** The two
dialects turn out to be perfectly disjoint per file, so the adapter reads
whichever it finds and cannot double-count.

Per-vendor stripping is real, and it is not the same shape of problem for each
client. Claude injects noise as tags inside otherwise-real turns, so it is
removed with a regex. Codex emits noise as whole records — developer-role
prompts, plugin catalogues, `<environment_context>`, encrypted reasoning,
telemetry — so it is dropped by record type, and copying Claude's tag regex
across would be a bug: the angle brackets in codex user text are prose
(`<sha>`, `<job>`, `<seat>`). The full drop list is a table in
`adapters/codex.py`, with a test per entry.

### `min_user_turns` is the real gate, not the envelope

Of 124 non-subagent codex sessions, **all 124** produce envelopes the server
counts correctly, and **8** clear the default `min_user_turns=5`. 114 of 127
codex sessions on this machine are single-prompt `codex exec` runs. So a
correct adapter with a correct envelope still leaves most codex work out of
`narrative_coverage`. That is a serving/config decision — the threshold is a
parameter, and it exists because short Claude sessions are usually noise —
but "codex ingest works" and "codex work reaches the narrative" are different
claims, and only the first is true by default.

## The oracle

`tools/adapter_oracle.py` regenerates stored conversations through the
adapter and requires byte equality with what is on disk. It is the guard on
5,500+ existing files, and it runs in CI via
`tests/test_adapter_oracle.py`.

```bash
python3 tools/adapter_oracle.py              # the pinned 20-session sample
python3 tools/adapter_oracle.py --all        # the whole corpus
python3 tools/adapter_oracle.py --select 20 --write-sample --list
```

**The test file is the CI gate, not the script.** `tools/adapter_oracle.py`
exits 0 when there is no corpus to check — correct for a local tool, useless
as a gate, since a machine without `~/.claude/memory` would pass vacuously.
`tests/test_adapter_oracle.py` is what CI runs: the corpus cases skip when the
corpus is absent, and 21 synthetic golden fixtures still run and still bite
(dropping a turn, reordering blocks, truncating one character of body text or
dropping an `[L:N]` ref each fail fixtures with no corpus present).

The comparison is on bytes, not decoded text: transcripts contain bare `\r`
from captured progress bars, and universal-newline translation on read
invents differences that are not on disk.

One difference is declared and allowed: the `client:` frontmatter line, which
did not exist before adapters. The oracle removes exactly that line from the
regenerated text and requires everything else to match, so the allowance
cannot hide a second change. The excuse is deliberately narrow — **exactly one
line, carrying exactly the expected client name**. An earlier version excused
any number of `client:` lines saying anything, which meant a duplicated line
or `client: nonsense` passed while the oracle printed a reassuring byte count.
`client:` is the one line a second adapter changes, so its value is asserted.
A stored file that already carries `client:` needs no excuse and is held to
plain byte equality.

### Known pre-existing drift

`--all` reports 10 sessions that do not reproduce (out of ~5,540 — the corpus
grows daily, so the denominator moves). All 10 fail on `main` too, and the two
extractors produce byte-identical output for each of them, so the refactor
changes nothing about them. Three kinds:

- **Eight are stale**: the stored `.md` was written while the transcript was
  still growing, so regeneration produces *more* turns and a later `ended`
  (`810372f1`: 15 → 116 turns). That is the stale-session problem, and it
  belongs to whoever fixes staleness.
- **`7a8a83ff`** differs only in `ended`, by 118 ms. Same turns, same bytes.
- **`08d89c12`** is the odd one, and worth stating precisely because it is
  easy to mis-file as data loss. Of 8,554 lines, 66 differ, and every one of
  the 66 is an `=== assistant … [L:N] ===` header: 53 where the ref shifts by
  1–6 lines, 13 where a ref disappears. No line of conversation text differs
  and no timestamp differs. It is `[L:N]` provenance drift from an archived
  transcript structurally offset from the one that produced the `.md` — an
  archive-integrity question, not lost narrative.

They are not suppressed anywhere. The sample is drawn blind — by age, size and
project, with no knowledge of which sessions pass — and simply did not land on
one of the ten. If a future draw does include one, the way to tell the two
kinds of failure apart is to run the same session through `main`'s
`extract_conversation.extract()`: if it mismatches there too, it is drift, not
a regression. A suppression list would be easier and would be the end of the
oracle.

## Clients with no stored corpus

The oracle needs files that already exist, so a new client has nothing to
compare against. `tests/test_codex_adapter.py` is the pattern that replaces
it — four checks, because no single one is sufficient:

1. **Golden fixtures.** `tools/make_codex_fixtures.py` builds them from real
   sessions, keeping every structural detail and discarding the prose: each
   string becomes a length-stamped placeholder. Ten fixtures chosen by feature
   cover (every record shape the client emits) and then by conversation depth,
   because this corpus is lopsided and sampling at random would return ten
   near-identical files and call it diversity. Oversized sources are committed
   as a prefix, marked in the filename.
   *Sanitise with an allow-list, not a deny-list.* The first version enumerated
   the keys holding prose and leaked absolute paths through dictionary **keys**
   while carefully scrubbing the values. A string now survives only if it looks
   like a structural token.
2. **The envelope self-check**, run over every fixture and every real session.
3. **Conformance attacks** — an adapter missing a protocol function, or with a
   non-callable in its place, must be rejected by `conforms()`; `discover()`
   must not return duplicate ids or phantom paths.
4. **Discovery edge cases** — a malformed line mid-file, an unreadable file, an
   empty file, a missing sessions directory. Skip and continue, never crash:
   discovery that dies on one bad file reports zero sessions, which is worse
   than reporting the rest.
