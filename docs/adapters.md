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

The comparison is on bytes, not decoded text: transcripts contain bare `\r`
from captured progress bars, and universal-newline translation on read
invents differences that are not on disk.

One difference is declared and allowed: the `client:` frontmatter line, which
did not exist before adapters. The oracle removes exactly that line from the
regenerated text and requires everything else to match, so the allowance
cannot hide a second change.

### Known pre-existing drift

`--all` reports 10 sessions out of 5,543 that do not reproduce. They fail on
`main` too, from before this refactor: their stored `.md` was written while
the transcript was still growing, so regeneration legitimately produces more
turns and a later `ended`. That is the stale-session problem, not a parser
bug, and it belongs to whoever fixes staleness.

They are not suppressed anywhere. The sample is drawn blind — by age, size and
project, with no knowledge of which sessions pass — and simply did not land on
one of the ten. If a future draw does include one, the way to tell the two
kinds of failure apart is to run the same session through `main`'s
`extract_conversation.extract()`: if it mismatches there too, it is drift, not
a regression. A suppression list would be easier and would be the end of the
oracle.
