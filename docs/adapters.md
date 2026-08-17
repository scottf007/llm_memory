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
5. Self-check the envelope against `_count_user_turns`. A session whose
   envelope counts zero user turns is dropped below `min_user_turns` with no
   error and no log line — memory just quietly gets a hole in it.

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
