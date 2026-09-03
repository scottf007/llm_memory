"""Unit tests for the adapter cache/dispatch scaffolding.

Base content is `tests/test_adapter_base.py` as it stands at `37e9bc9`
(reviewed PASS, not yet landed on main) — the first five tests plus the
`test_adapter_facades_keep_their_public_module_identity_and_docs`
parametrisation, unmodified. Everything below the `# --- grok rebase ---`
marker is new: main has since gained `adapters/grok.py` (`b3cf2b2`), whose
`SessionRef.path` is a session *directory*, not a file. Re-landing the
08681f1/37e9bc9 facade on top of grok needs the facade to key its cache on
the adapter's source *file* (`source_path(ref)`) when the adapter exposes
one, not on `ref.path` — otherwise an in-place append to grok's
`chat_history.jsonl` would not change the directory's mtime/size and the
facade would silently serve stale turns for a growing session.

Whole-file status on main (`b3cf2b2`): collection-red (`ImportError`), same
frozen-test convention `tests/test_grok_adapter.py` already documents —
`archive_path` and `make_adapter_parser` do not exist in `adapters/base.py`
until 08681f1 lands, so nothing in this file, old or new, can even import.
That gate is inherent to re-landing 08681f1 at all; it says nothing about
which *behaviour* each new test pins. Every new test below the marker calls
only the adapters' real public `parse`/`session_meta`/`turns` (never
`make_adapter_parser` directly, unlike the four original tests), so its
underlying claim was independently verified by calling those same functions
directly against the current hand-rolled `claude.py`/`codex.py`/`grok.py` —
bypassing this file's own import gate — with these results:

- `test_adapter_facades_keep_their_public_module_identity_and_docs[grok]`:
  RED once collectible. `session_meta`/`turns` have no docstring on any of
  the three adapters today (only `parse` does) — same reason the claude/codex
  cases are red too.
- `test_registered_adapter_facades_share_the_same_underlying_code_object`:
  RED once collectible. Each adapter still hand-rolls its own
  `parse`/`session_meta`/`turns`; none share a `__code__` object yet. This is
  what proves consolidation actually happened once it passes, for grok as
  much as claude/codex.
- `test_grok_missing_chat_history_is_not_cached`: RED once collectible.
  Grok's own `_cache_key` treats a directory whose `chat_history.jsonl` is
  absent as a stable key (only that one file's stat entry goes `None`; the
  directory and `summary.json` stats don't change), so two calls return the
  identical cached object (`m1 is m2` verified `True` against current
  `grok.parse`). The desired behaviour — mirroring
  `test_adapter_parser_does_not_cache_a_missing_path` above — never caches a
  missing source.
- `test_grok_parser_rereads_an_in_place_growth_even_when_directory_mtime_is_masked`:
  GREEN once collectible (verified directly against current `grok.parse`).
  Grok's current hand-rolled cache already keys on `chat_history.jsonl`'s own
  stat (not the directory's), so it already rereads correctly;
  `os.utime`-masking the directory changes nothing either way. This is the
  control that must keep passing once grok is rewired onto the shared facade
  with `source_path` wired through — it is the exact case that goes red if a
  future change keys the facade on `ref.path` instead.
- `test_unchanged_session_memoizes_identically_across_clients[claude|codex|grok]`:
  GREEN once collectible for all three (verified directly against current
  `claude.parse`/`codex.parse`/`grok.parse` — each adapter's own cache
  already memoizes an untouched session). Kept as the companion control to
  the growth test: it must keep passing after the rebase too.
"""

from __future__ import annotations

import json
import os

import pytest

import adapters
from adapters import claude, codex, grok
from adapters.base import SessionMeta, SessionRef, Turn, archive_path, make_adapter_parser


def test_adapter_parser_memoizes_one_unchanged_transcript(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text("first\n")
    ref = SessionRef("session", path, "test")
    calls = []

    def parse_impl(current_ref):
        calls.append(current_ref.path.read_text())
        return SessionMeta(session_id="session", client="test"), [Turn("user", "", calls[-1])]

    parse, session_meta, turns = make_adapter_parser(parse_impl)

    assert session_meta(ref).session_id == "session"
    assert [turn.text for turn in turns(ref)] == ["first\n"]
    assert parse(ref)[1][0].text == "first\n"
    assert calls == ["first\n"]


def test_adapter_parser_rereads_when_a_transcript_grows(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text("first\n")
    ref = SessionRef("session", path, "test")
    calls = 0

    def parse_impl(current_ref):
        nonlocal calls
        calls += 1
        return SessionMeta(session_id="session", client="test"), [
            Turn("user", "", current_ref.path.read_text())
        ]

    parse, _, _ = make_adapter_parser(parse_impl)
    assert parse(ref)[1][0].text == "first\n"
    with path.open("a") as handle:
        handle.write("second\n")
    assert parse(ref)[1][0].text == "first\nsecond\n"
    assert calls == 2


def test_adapter_parser_does_not_cache_a_missing_path(tmp_path):
    path = tmp_path / "later.jsonl"
    ref = SessionRef("later", path, "test")
    calls = 0

    def parse_impl(current_ref):
        nonlocal calls
        calls += 1
        return SessionMeta(session_id="later", client="test"), []

    parse, _, _ = make_adapter_parser(parse_impl)
    parse(ref)
    parse(ref)
    assert calls == 2


def test_archive_path_is_portable_and_client_neutral():
    assert archive_path("claude-session") == "transcripts/claude-session.jsonl"
    assert archive_path("codex-session") == "transcripts/codex-session.jsonl"


@pytest.mark.parametrize("adapter", [claude, codex, grok])
def test_adapter_facades_keep_their_public_module_identity_and_docs(adapter):
    for name in ("parse", "session_meta", "turns"):
        facade = getattr(adapter, name)
        assert facade.__module__ == adapter.__name__
        assert facade.__qualname__ == name
        assert facade.__doc__


# --- grok rebase: new tests below, see module docstring for red/green ------


def _write_claude_session(path, texts):
    lines = []
    for i, (role, text) in enumerate(texts):
        if role == "user":
            entry = {
                "type": "user",
                "message": {"content": text},
                "timestamp": f"2026-01-01T00:00:{i:02d}Z",
            }
        else:
            entry = {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": text}]},
                "timestamp": f"2026-01-01T00:00:{i:02d}Z",
            }
        lines.append(json.dumps(entry))
    path.write_text("\n".join(lines) + "\n")


def _write_codex_session(path, texts):
    lines = []
    for i, (role, text) in enumerate(texts):
        ptype = "user_message" if role == "user" else "agent_message"
        entry = {
            "type": "event_msg",
            "timestamp": f"2026-01-01T00:00:{i:02d}Z",
            "payload": {"type": ptype, "message": text},
        }
        lines.append(json.dumps(entry))
    path.write_text("\n".join(lines) + "\n")


def _write_grok_session(session_dir, chat_records):
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "summary.json").write_text(
        json.dumps(
            {
                "info": {"cwd": "/home/user"},
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        )
    )
    (session_dir / "chat_history.jsonl").write_text(
        "\n".join(json.dumps(record) for record in chat_records) + "\n"
    )


def _claude_session_ref(tmp_path):
    path = tmp_path / "claude-session.jsonl"
    _write_claude_session(path, [("user", "hi"), ("assistant", "hello")])
    return claude, claude.ref_for_path(path)


def _codex_session_ref(tmp_path):
    path = tmp_path / "codex-session.jsonl"
    _write_codex_session(path, [("user", "hi"), ("assistant", "hello")])
    return codex, codex.ref_for_path(path)


def _grok_session_ref(tmp_path):
    session_dir = tmp_path / "grok-session"
    _write_grok_session(
        session_dir,
        [
            {"type": "user", "content": [{"type": "text", "text": "hi"}], "prompt_index": 0},
            {"type": "assistant", "content": "hello"},
        ],
    )
    return grok, grok.ref_for_path(session_dir, session_id="grok-session")


@pytest.mark.parametrize(
    "build_ref",
    [_claude_session_ref, _codex_session_ref, _grok_session_ref],
    ids=["claude", "codex", "grok"],
)
def test_unchanged_session_memoizes_identically_across_clients(tmp_path, build_ref):
    """Control: an untouched session must keep returning the cached object."""
    adapter, ref = build_ref(tmp_path)
    first = adapter.parse(ref)
    second = adapter.parse(ref)
    assert first is second


def test_grok_parser_rereads_an_in_place_growth_even_when_directory_mtime_is_masked(tmp_path):
    """Control: the exact trap a directory-keyed facade cache would fall into.

    Grok appends to `chat_history.jsonl` in place on every turn; the session
    *directory*'s mtime is reset back to its pre-growth value with
    `os.utime` to rule out the cache accidentally invalidating on a
    directory-level signal rather than the source file's own stat.
    """
    session_dir = tmp_path / "grok-growth"
    _write_grok_session(
        session_dir,
        [
            {"type": "user", "content": [{"type": "text", "text": "first"}], "prompt_index": 0},
            {"type": "assistant", "content": "ack"},
        ],
    )
    ref = grok.ref_for_path(session_dir, session_id="grok-growth")

    _, first_turns = grok.parse(ref)
    assert sum(1 for turn in first_turns if turn.role == "user") == 1

    dir_stat = session_dir.stat()
    with (session_dir / "chat_history.jsonl").open("a") as handle:
        handle.write(
            json.dumps({"type": "user", "content": [{"type": "text", "text": "second"}], "prompt_index": 1})
            + "\n"
        )
        handle.write(json.dumps({"type": "assistant", "content": "ack again"}) + "\n")
    os.utime(session_dir, ns=(dir_stat.st_atime_ns, dir_stat.st_mtime_ns))

    _, second_turns = grok.parse(ref)
    assert sum(1 for turn in second_turns if turn.role == "user") == 2


def test_grok_missing_chat_history_is_not_cached(tmp_path):
    """Mirror of `test_adapter_parser_does_not_cache_a_missing_path` for grok.

    No `chat_history.jsonl` ever exists here, so two calls should each parse
    fresh rather than share a cached result — asserted via object identity
    since the public facade gives no direct call counter, unlike the
    inline `parse_impl` closures above.
    """
    session_dir = tmp_path / "grok-missing"
    session_dir.mkdir()
    (session_dir / "summary.json").write_text(
        json.dumps({"info": {"cwd": "/home/user"}, "created_at": "2026-01-01T00:00:00Z"})
    )
    ref = grok.ref_for_path(session_dir, session_id="grok-missing")

    first_meta, _ = grok.parse(ref)
    second_meta, _ = grok.parse(ref)
    assert first_meta is not second_meta


def test_registered_adapter_facades_share_the_same_underlying_code_object():
    """Proves consolidation happened for every registered adapter, grok
    included, rather than a third hand-rolled cache/dispatch copy surviving
    alongside the shared facade.
    """
    for name in ("parse", "session_meta", "turns"):
        code_objects = {
            client: getattr(adapters.get(client), name).__code__ for client in adapters.names()
        }
        first = next(iter(code_objects.values()))
        for client, code in code_objects.items():
            assert code is first, f"{client}.{name} does not share the facade's code object"
