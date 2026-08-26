"""Tests for the codex adapter, the envelope, and the adapter layer's defences.

There is no stored codex corpus, so S1's byte-for-byte oracle has no
counterpart here. Four things stand in for it:

1. **Golden fixtures** — ten sanitised real sessions with pinned output. They
   keep every structural detail of the originals and none of the prose.
2. **The envelope self-check** — the named silent-drop failure. A session whose
   envelope reads as zero user turns disappears from `narrative_coverage` with
   no error anywhere, so it is asserted, not assumed.
3. **Conformance attacks** — a broken adapter must be caught by the registry,
   not discovered later as an empty narrative.
4. **Discovery edge cases** — unreadable files and malformed lines are skipped
   and counted, never fatal. Discovery that dies on one bad file reports zero
   sessions, which is worse than reporting the rest.
"""

from __future__ import annotations

import os
import json
import types
from pathlib import Path

import pytest

import adapters
from adapters import base, codex, envelope

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "codex"
FIXTURES = sorted(p for p in FIXTURE_DIR.glob("*.jsonl") if ".expected" not in p.name)


def _ref(path: Path):
    return codex.ref_for_path(path, session_id=f"codex-{path.stem}")


def _parse_fixture(path: Path):
    meta, turns = codex.parse(_ref(path))
    meta.raw_source = f"tests/fixtures/codex/{path.name}"
    return meta, turns


# --------------------------------------------------------------------------
# Registration and protocol
# --------------------------------------------------------------------------

def test_codex_is_registered():
    assert "codex" in adapters.names()
    assert adapters.get("codex") is codex
    assert codex.client_name() == "codex"


def test_codex_ids_are_prefixed_and_do_not_collide_with_reserved_stems():
    assert codex.ID_PREFIX == "codex-"
    for reserved in adapters.RESERVED_PREFIXES:
        assert not codex.ID_PREFIX.startswith(reserved)
        assert not reserved.startswith(codex.ID_PREFIX)


def test_session_id_prefixing_is_idempotent():
    assert codex.session_id_for("abc") == "codex-abc"
    assert codex.session_id_for("codex-abc") == "codex-abc"


def test_bare_ids_route_to_claude_and_prefixed_ids_to_their_owner():
    assert adapters.client_for_session_id("019ffa3a-5670-7b12") == "claude"
    assert adapters.client_for_session_id("codex-019ffa3a-5670") == "codex"


@pytest.mark.parametrize("session_id", ["CODEX-019ffa3a", "Codex-019ffa3a", "cOdEx-019ffa3a"])
def test_prefix_routing_is_case_insensitive(session_id):
    """Routing reads whatever is on disk, not only what an adapter wrote.

    A file renamed by hand, or one that arrived through a case-insensitive
    filesystem, must not fall through to Claude and get re-extracted into a
    `client: claude` conversation. Failing open to the wrong client is the
    expensive direction.
    """
    assert adapters.client_for_session_id(session_id) == "codex"


def test_case_insensitivity_does_not_capture_unrelated_ids():
    """Non-trigger control: near-misses still belong to Claude."""
    for session_id in ("codexish-abc", "code-019", "xcodex-019", "019-codex-1"):
        assert adapters.client_for_session_id(session_id) == "claude"


# --------------------------------------------------------------------------
# Golden fixtures
# --------------------------------------------------------------------------

def test_fixtures_are_present():
    assert len(FIXTURES) >= 10, f"expected the committed fixture set, found {len(FIXTURES)}"


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_fixture_renders_to_its_pinned_conversation(path):
    meta, turns = _parse_fixture(path)
    expected = (FIXTURE_DIR / f"{path.stem}.expected.md").read_text()
    assert adapters.render_conversation(meta, turns) == expected


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_fixture_renders_to_its_pinned_envelope(path):
    meta, turns = _parse_fixture(path)
    expected = (FIXTURE_DIR / f"{path.stem}.expected.envelope.jsonl").read_text()
    assert adapters.render_envelope(meta, turns) == expected


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_fixture_frontmatter_carries_codex_provenance(path):
    md = (FIXTURE_DIR / f"{path.stem}.expected.md").read_text()
    head = md.split("\n---\n", 1)[0]
    assert "client: codex\n" in head
    assert "raw: ~/.claude/memory/transcripts/codex-" in head


def test_both_dialogue_dialects_are_covered_by_the_fixture_set():
    """The regression this whole file exists for.

    `event_msg`/`user_message` is the documented shape and covers 123 of the
    127 sessions on this machine. TUI sessions use `item_completed` instead,
    and an adapter that reads only the documented shape renders them as an
    empty conversation — no error, no warning, just a session that never
    reaches any narrative.
    """
    dialects = set()
    for path in FIXTURES:
        with open(path, errors="replace") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                p = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
                if rec.get("type") != "event_msg":
                    continue
                if p.get("type") in ("user_message", "agent_message"):
                    dialects.add("event_msg")
                elif p.get("type") == "item_completed":
                    item = p.get("item") if isinstance(p.get("item"), dict) else {}
                    if item.get("type") in ("UserMessage", "AgentMessage"):
                        dialects.add("item_completed")
    assert dialects == {"event_msg", "item_completed"}, f"only covers {dialects}"


def test_item_completed_dialect_actually_yields_turns():
    """Non-trivial control for the dialect above.

    A subagent fixture contains `item_completed` records and still renders as a
    stub, so "the fixture set mentions the dialect" is not evidence that the
    dialect is parsed. At least one non-subagent fixture must produce real
    turns from it.
    """
    found = []
    for path in FIXTURES:
        meta, turns = _parse_fixture(path)
        if meta.is_subagent:
            continue
        with open(path, errors="replace") as f:
            uses_ic = any('"item_completed"' in line and '"UserMessage"' in line for line in f)
        if uses_ic and any(t.role == "user" and t.text for t in turns):
            found.append(path.name)
    assert found, "no non-subagent fixture produces user turns from the item_completed dialect"


_TOKEN_RE = __import__("re").compile(r"^[A-Za-z0-9_.:+-]{1,40}$")
_PLACEHOLDER_RE = __import__("re").compile(r"^<[A-Za-z0-9_]+:\d+(?::[0-9a-f]{8})?>$|^<[A-Za-z0-9_]+>$")
# The one class of long string the sanitiser deliberately keeps: the rewritten
# neutral cwd, because project attribution has to stay testable. The project
# component itself must be the digest placeholder `make_codex_fixtures.py`
# generates, never a real directory name — a real name is just as short and
# just as token-shaped, so this has to check the placeholder's exact form
# rather than merely "starts with /home/user".
_ALLOWED_LITERALS = {"/home/user"}
_PLACEHOLDER_CWD_RE = __import__("re").compile(
    r"^/home/user/projects/project-[0-9a-f]{8}$")


def _walk_strings(node, path="$"):
    """Yield (json-path, string) for every string anywhere in the tree."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield f"{path}.{key}", str(key)
            yield from _walk_strings(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk_strings(value, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


def _is_sanitised(value: str) -> bool:
    if value in _ALLOWED_LITERALS or _PLACEHOLDER_CWD_RE.match(value):
        return True
    if _PLACEHOLDER_RE.match(value) or _TOKEN_RE.match(value):
        return True
    return False


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_every_string_at_every_depth_is_sanitised(path):
    """Depth-blind, not key-blind.

    The earlier checks looked for known leaks — a home directory, a filename.
    That is the same mistake the first sanitiser made: it can only catch what
    someone thought to list. This asserts the invariant instead. Every string
    anywhere in the tree, at any nesting depth, on either dialect's payload
    path, in a dict key or a value, must be a structural token or a
    placeholder. Nested prose cannot ship without failing here.
    """
    offenders = []
    for line_no, line in enumerate(path.read_text().splitlines(), 1):
        try:
            record = json.loads(line)
        except Exception:
            offenders.append((line_no, "$", "<unparseable line>"))
            continue
        for json_path, value in _walk_strings(record):
            if not _is_sanitised(value):
                offenders.append((line_no, json_path, value[:80]))
    assert not offenders, (
        f"{path.name}: {len(offenders)} unsanitised string(s), first few: {offenders[:5]}")


def test_the_depth_walk_would_catch_nested_prose():
    """Trigger control for the walk above.

    A check that never fires proves nothing, and this one is asserting a
    negative over ~1.8 MB of fixtures.
    """
    smuggled = {"type": "event_msg", "payload": {"item": {"content": [
        {"deep": {"deeper": ["a sentence of real prose that should never ship"]}}]}}}
    bad = [v for _, v in _walk_strings(smuggled) if not _is_sanitised(v)]
    assert bad == ["a sentence of real prose that should never ship"]

    # And a key, not just a value — the leak the first sanitiser actually had.
    keyed = {"/home/user/projects/thing/notes.md": {"type": "x"}}
    assert any(not _is_sanitised(v) for _, v in _walk_strings(keyed))


# --------------------------------------------------------------------------
# Real-name guard sourcing (F-03/F-24)
# --------------------------------------------------------------------------
#
# The two guards below need REAL project names to be real trigger controls —
# a shape-only check cannot tell a real directory name from the digest
# placeholder, which is the whole point of the guard. But this repository is
# published, so the literal names cannot live in it: a public repo containing
# the list has leaked the list, whatever the assertion around it says.
#
# So the names are sourced from outside the tree, and the guards degrade
# honestly rather than silently:
#   * LLM_MEMORY_REAL_NAMES  — comma-separated, or
#   * tests/.real-names      — one per line, gitignored.
# Absent, the literal-name guard SKIPS with a message saying so, while the
# synthetic control below still runs everywhere. A public clone therefore
# proves the mechanism works; the maintainer's machine additionally proves it
# catches the specific strings an owner ruled out.
#
# `_REAL_NAMES_FILE` is a module-level path so tests can isolate the skip
# path (monkeypatch to a missing tmp path) even when the untracked file is
# present on this machine. conftest.py exposes the same file as a fixture.

# Real-shaped but fictional. These must be caught by the same code path, so a
# public clone still has a working trigger control rather than a disabled test.
SYNTHETIC_REAL_NAMES = ("acme-messaging", "globex-platform", "initech")

_REAL_NAMES_FILE = Path(__file__).parent / ".real-names"


def _real_names():
    """Names to guard against, from outside the published tree, or None."""
    env = os.environ.get("LLM_MEMORY_REAL_NAMES", "").strip()
    if env:
        return tuple(n.strip() for n in env.split(",") if n.strip())
    local = _REAL_NAMES_FILE
    if local.exists():
        names = tuple(n.strip() for n in local.read_text().splitlines() if n.strip())
        if names:
            return names
    return None


_NO_NAMES = (
    "no real-name list available: set LLM_MEMORY_REAL_NAMES=a,b,c or create "
    "tests/.real-names (gitignored). The synthetic trigger control still ran."
)


def test_the_depth_walk_would_catch_a_real_project_name():
    """Trigger + non-trigger control for the cwd project component.

    A real project name is exactly as token-shaped as the digest placeholder
    `make_codex_fixtures.py` generates, so a shape-only check ("looks like a
    token", or the old "starts with /home/user") cannot tell them apart. This
    pins the actual invariant: only the placeholder form survives, so a real
    directory name landing in `cwd` — the leak this guard exists for — can
    never ship again.
    """
    # Synthetic control: runs everywhere, including a public clone.
    for name in SYNTHETIC_REAL_NAMES + (_real_names() or ()):
        smuggled = {"type": "session_meta",
                    "payload": {"cwd": f"/home/user/projects/{name}"}}
        bad = [v for _, v in _walk_strings(smuggled) if not _is_sanitised(v)]
        assert f"/home/user/projects/{name}" in bad, f"{name} was not caught"

    # Non-trigger control: the actual placeholder shape must keep passing.
    placeholder = {"type": "session_meta",
                   "payload": {"cwd": "/home/user/projects/project-580b9887"}}
    assert not [v for _, v in _walk_strings(placeholder) if not _is_sanitised(v)]


def test_fixture_tree_contains_no_symlinks():
    """`cp -r` follows or preserves symlinks depending on flags and platform.

    A symlink in a committed fixture tree either dangles after install or
    points somewhere outside it. Neither is a fixture.
    """
    links = [p for p in FIXTURE_DIR.rglob("*") if p.is_symlink()]
    assert not links, f"symlinks in the fixture tree: {links}"


def test_fixtures_carry_no_identifying_content():
    """The fixtures are committed to a public repository.

    Sanitising keeps structure and drops prose. This is the check that the
    dropping actually happened — a deny-list version of the sanitiser passed
    its own tests while leaking absolute paths through dictionary *keys*.
    """
    for path in FIXTURES:
        text = path.read_text()
        # Derived, not hardcoded: this catches the home directory of whoever
        # is actually running, which is a stronger guard than one owner's
        # literal path and leaks nothing into a published tree.
        assert str(Path.home()) not in text, f"{path.name} leaks a real home directory"
        for literal in (_real_names() or ()):
            assert literal not in text, f"{path.name} leaks {literal!r}"
        # Every cwd must be the neutral rewrite.
        for line in text.splitlines():
            rec = json.loads(line)
            payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
            for holder in (rec, payload):
                cwd = holder.get("cwd") if isinstance(holder, dict) else None
                if isinstance(cwd, str) and cwd.startswith("/home"):
                    assert cwd.startswith("/home/user"), f"{path.name}: unsanitised cwd {cwd}"


def test_no_real_project_name_ships_in_a_fixture():
    """Named regression guard for the real project names the owner ruled out
    of the public fixture set. The list is sourced from outside this tree (see
    `_real_names`) because naming them here would publish them.

    Belt-and-suspenders alongside the shape-based depth walk: this fails loudly
    and specifically if any of those literal strings ever reappears anywhere in
    the committed fixture tree. Skips, with a message, where the list is not
    configured — the depth-walk guard still runs its synthetic control there.
    """
    real_names = _real_names()
    if not real_names:
        pytest.skip(_NO_NAMES)
    for path in list(FIXTURES) + list(FIXTURE_DIR.glob("*.expected.*")):
        text = path.read_text()
        for name in real_names:
            assert name not in text, f"{path.name} leaks real project name {name!r}"


# --------------------------------------------------------------------------
# Noise handling
# --------------------------------------------------------------------------

def _session(records: list[dict], tmp_path: Path, name: str = "rollout-x") -> Path:
    path = tmp_path / f"{name}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return path


def _meta_record(cwd="/home/u/projects/demo", **extra):
    return {"timestamp": "2026-01-01T00:00:00.000Z", "type": "session_meta",
            "payload": {"id": "abc", "cwd": cwd, **extra}}


def test_event_msg_dialect_renders_a_conversation(tmp_path):
    path = _session([
        _meta_record(),
        {"timestamp": "2026-01-01T00:00:01.000Z", "type": "event_msg",
         "payload": {"type": "user_message", "message": "do the thing"}},
        {"timestamp": "2026-01-01T00:00:02.000Z", "type": "response_item",
         "payload": {"type": "custom_tool_call", "name": "exec", "input": "ls"}},
        {"timestamp": "2026-01-01T00:00:03.000Z", "type": "event_msg",
         "payload": {"type": "agent_message", "message": "done"}},
    ], tmp_path)

    meta, turns = codex.parse(codex.ref_for_path(path, session_id="codex-x"))
    out = adapters.render_conversation(meta, turns)
    assert meta.project == "demo"
    assert "client: codex\n" in out
    assert "=== user 2026-01-01T00:00:01.000Z ===\ndo the thing\n" in out
    # The tool call is dropped as content but survives as the block's line ref.
    assert "=== assistant 2026-01-01T00:00:03.000Z [L:3] ===\ndone\n" in out
    assert "exec" not in out


def test_item_completed_dialect_renders_the_same_conversation(tmp_path):
    path = _session([
        _meta_record(),
        {"timestamp": "2026-01-01T00:00:01.000Z", "type": "event_msg",
         "payload": {"type": "item_completed",
                     "item": {"type": "UserMessage",
                              "content": [{"type": "text", "text": "do the thing"}]}}},
        {"timestamp": "2026-01-01T00:00:02.000Z", "type": "event_msg",
         "payload": {"type": "item_completed",
                     "item": {"type": "CommandExecution", "command": "ls"}}},
        {"timestamp": "2026-01-01T00:00:03.000Z", "type": "event_msg",
         "payload": {"type": "item_completed",
                     "item": {"type": "AgentMessage", "phase": "final_answer",
                              "content": [{"type": "Text", "text": "done"}]}}},
    ], tmp_path)

    meta, turns = codex.parse(codex.ref_for_path(path, session_id="codex-y"))
    out = adapters.render_conversation(meta, turns)
    assert "=== user 2026-01-01T00:00:01.000Z ===\ndo the thing\n" in out
    assert "=== assistant 2026-01-01T00:00:03.000Z [L:3] ===\ndone\n" in out
    assert "ls" not in out


@pytest.mark.parametrize("record", [
    {"type": "response_item", "payload": {"type": "message", "role": "developer",
                                          "content": [{"type": "input_text", "text": "SECRET-NOISE"}]}},
    {"type": "response_item", "payload": {"type": "message", "role": "user",
                                          "content": [{"type": "input_text", "text": "SECRET-NOISE"}]}},
    {"type": "response_item", "payload": {"type": "reasoning", "encrypted_content": "SECRET-NOISE"}},
    {"type": "response_item", "payload": {"type": "agent_message",
                                          "content": [{"type": "input_text", "text": "SECRET-NOISE"}]}},
    {"type": "event_msg", "payload": {"type": "token_count", "info": {"note": "SECRET-NOISE"}}},
    {"type": "event_msg", "payload": {"type": "task_complete", "last_agent_message": "SECRET-NOISE"}},
    {"type": "event_msg", "payload": {"type": "item_completed",
                                      "item": {"type": "Reasoning", "summary_text": "SECRET-NOISE"}}},
    {"type": "world_state", "payload": {"state": {"agents_md": {"text": "SECRET-NOISE"}}}},
    {"type": "turn_context", "payload": {"cwd": "SECRET-NOISE"}},
    {"type": "compacted", "payload": {"message": "SECRET-NOISE"}},
    {"type": "inter_agent_communication_metadata", "payload": {"note": "SECRET-NOISE"}},
])
def test_noise_records_never_reach_the_conversation(record, tmp_path):
    """One case per dropped record type, from the catalogue in the adapter."""
    record.setdefault("timestamp", "2026-01-01T00:00:02.000Z")
    path = _session([
        _meta_record(),
        {"timestamp": "2026-01-01T00:00:01.000Z", "type": "event_msg",
         "payload": {"type": "user_message", "message": "real question"}},
        record,
        {"timestamp": "2026-01-01T00:00:03.000Z", "type": "event_msg",
         "payload": {"type": "agent_message", "message": "real answer"}},
    ], tmp_path)

    meta, turns = codex.parse(codex.ref_for_path(path, session_id="codex-z"))
    out = adapters.render_conversation(meta, turns)
    assert "SECRET-NOISE" not in out
    # Control: the real dialogue on either side of the noise still survives.
    assert "real question" in out and "real answer" in out


def test_mixed_dialect_file_keeps_both_and_says_so(tmp_path, capsys):
    """The disjointness assumption, made falsifiable.

    Reading both dialects is safe today because no codex session uses both —
    123 files use one, 4 use the other, none overlap. That is an observation
    about one machine at one version, not a guarantee. If it ever stops
    holding, turns may be duplicated.

    The adapter keeps both anyway: dropping half a conversation to protect an
    invariant is the worse failure. But it must not do that quietly, because a
    silently doubled transcript reads as a real conversation.
    """
    path = _session([
        _meta_record(),
        {"timestamp": "2026-01-01T00:00:01.000Z", "type": "event_msg",
         "payload": {"type": "user_message", "message": "from the exec dialect"}},
        {"timestamp": "2026-01-01T00:00:02.000Z", "type": "event_msg",
         "payload": {"type": "item_completed",
                     "item": {"type": "UserMessage",
                              "content": [{"type": "text", "text": "from the tui dialect"}]}}},
    ], tmp_path)

    meta, turns = codex.parse(codex.ref_for_path(path, session_id="codex-mixed"))
    texts = [t.text for t in turns]
    assert "from the exec dialect" in texts, "the exec dialect was dropped"
    assert "from the tui dialect" in texts, "the tui dialect was dropped"

    assert meta.notes, "a mixed-dialect session must be flagged"
    note = meta.notes[0]
    assert "event_msg" in note and "item_completed" in note
    assert "duplicated" in note
    assert "WARNING" in capsys.readouterr().err


def test_single_dialect_file_is_not_flagged(tmp_path, capsys):
    """Non-trigger control: the warning must stay quiet in the normal case,
    which is every codex session that exists today."""
    path = _session([
        _meta_record(),
        {"timestamp": "2026-01-01T00:00:01.000Z", "type": "event_msg",
         "payload": {"type": "user_message", "message": "just the one dialect"}},
        {"timestamp": "2026-01-01T00:00:02.000Z", "type": "event_msg",
         "payload": {"type": "agent_message", "message": "still the one dialect"}},
    ], tmp_path)

    meta, _ = codex.parse(codex.ref_for_path(path, session_id="codex-single"))
    assert meta.notes == []
    assert "WARNING" not in capsys.readouterr().err


def test_no_real_session_is_mixed_dialect():
    """The observation the adapter relies on, asserted against the real corpus."""
    if not HAS_CORPUS:
        pytest.skip("no ~/.codex/sessions on this machine")
    flagged = []
    for ref in codex.discover():
        meta, _ = codex.parse(ref)
        if meta.notes:
            flagged.append((ref.path.name, meta.notes))
    assert not flagged, f"disjointness no longer holds: {flagged}"


def test_kept_text_is_not_tag_stripped(tmp_path):
    """Non-trigger control against copying Claude's noise regex.

    Codex noise is dropped by record type, so angle brackets in kept text are
    prose. Stripping them would silently eat real content.
    """
    path = _session([
        _meta_record(),
        {"timestamp": "2026-01-01T00:00:01.000Z", "type": "event_msg",
         "payload": {"type": "user_message",
                     "message": "post am status --job <job> --seat <seat> at <sha>"}},
    ], tmp_path)
    meta, turns = codex.parse(codex.ref_for_path(path, session_id="codex-tags"))
    assert "<job>" in adapters.render_conversation(meta, turns)


def test_subagent_thread_is_stubbed(tmp_path):
    path = _session([
        _meta_record(agent_path="/root/critic", parent_thread_id="parent-1"),
        {"timestamp": "2026-01-01T00:00:01.000Z", "type": "event_msg",
         "payload": {"type": "user_message", "message": "spawned work"}},
    ], tmp_path)

    meta, turns = codex.parse(codex.ref_for_path(path, session_id="codex-sub"))
    out = adapters.render_conversation(meta, turns)
    assert meta.is_subagent
    assert meta.parent_session_id == "codex-parent-1"
    assert "agent_session: true" in out
    assert "spawned work" not in out


# --------------------------------------------------------------------------
# The envelope, and the silent-drop failure it guards
# --------------------------------------------------------------------------

def test_envelope_matches_the_servers_own_user_turn_rules():
    """`envelope.count_user_turns` re-implements `server.py`'s counter.

    A re-implementation that drifts from the original is worse than no check
    at all, so the two are pinned against each other here rather than trusted.
    """
    server_py = (Path(__file__).resolve().parent.parent / "server.py").read_text()
    start = server_py.index("def _count_substantive_user_turns")
    body = server_py[start:server_py.index("\ndef ", start + 10)]
    for rule in ('rec.get("type") != "user"', 'rec.get("isSidechain")',
                 'msg.get("role") != "user"', 'c.get("type") == "tool_result"'):
        assert rule in body, f"server.py no longer uses {rule}; envelope.py must be re-synced"
        assert rule in Path(envelope.__file__).read_text(), f"envelope.py is missing {rule}"


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_fixture_envelope_survives_the_turn_counter(path, tmp_path):
    """Acceptance (3): a session with real user turns must never read as zero."""
    meta, turns = _parse_fixture(path)
    if meta.is_subagent:
        pytest.skip("subagent threads are captured by their parent")
    written = envelope.write_envelope(meta, turns, tmp_path)
    expected = envelope.user_turn_count(turns)
    ok, detail = envelope.verify_envelope(written, expected)
    assert ok, detail


def test_verify_envelope_catches_a_zero_turn_envelope(tmp_path):
    """Trigger control for the check above."""
    broken = tmp_path / "codex-broken.jsonl"
    broken.write_text(json.dumps({"type": "assistant", "timestamp": "t",
                                  "message": {"role": "assistant", "content": []}}) + "\n")
    ok, detail = envelope.verify_envelope(broken, expected_user_turns=7)
    assert not ok
    assert "would be dropped" in detail


def test_envelope_user_content_is_a_string_not_a_block_list(tmp_path):
    """A subtle way to fail the counter.

    Content that is a list of `tool_result` blocks is discarded as synthetic.
    Writing user text as a block list would put the envelope one refactor away
    from being invisible; a plain string cannot be mistaken for tool output.
    """
    meta = base.SessionMeta(session_id="codex-s", client="codex", project="demo")
    turns = [base.Turn("user", "2026-01-01T00:00:00.000Z", "hello")]
    rec = json.loads(adapters.render_envelope(meta, turns).splitlines()[0])
    assert isinstance(rec["message"]["content"], str)
    assert rec["type"] == "user"
    assert rec["client"] == "codex"


def test_unattributed_session_is_ingested_and_reaches_no_project(tmp_path):
    """9 real codex sessions were started from `~` or `~/projects`.

    They have a cwd and no project, and the pipeline has to hold that without
    inventing one. Demonstrated in a sandbox against the real consumers before
    being pinned here: `conversations.iter_sessions` parses all nine,
    `list_projects()` excludes them, `_find_project_transcripts("")` returns
    nothing, and merger + renderer run clean. So they are ingested, readable
    and attached to no narrative — the same place an unattributed Claude
    session lands, not a new failure mode.
    """
    import conversations

    path = _session([
        _meta_record(cwd="/home/u"),
        {"timestamp": "2026-01-01T00:00:01.000Z", "type": "event_msg",
         "payload": {"type": "user_message", "message": "no project here"}},
    ], tmp_path)
    meta, turns = codex.parse(codex.ref_for_path(path, session_id="codex-np"))
    assert meta.project == ""
    assert meta.cwd == "/home/u"

    conv_dir = tmp_path / "conversations"
    conv_dir.mkdir()
    (conv_dir / "codex-np.md").write_text(adapters.render_conversation(meta, turns))

    rows = list(conversations.iter_sessions(conv_dir))
    assert len(rows) == 1
    assert "project" not in rows[0], "a project-less session must not gain an empty project"
    assert rows[0]["session_id"] == "codex-np"
    assert rows[0]["client"] == "codex"
    assert conversations.list_projects(conv_dir) == []
    assert conversations.list_sessions("", conv_dir) == []

    # The envelope is still correct — attribution and visibility are separate.
    written = envelope.write_envelope(meta, turns, tmp_path / "transcripts")
    assert envelope.count_user_turns(written) == 1
    rec = json.loads(written.read_text().splitlines()[0])
    assert rec["cwd"] == "/home/u"
    assert adapters.project_from_cwd(rec["cwd"]) == ""


def test_envelope_cwd_round_trips_to_the_same_project():
    meta = base.SessionMeta(session_id="codex-s", client="codex",
                            project="demo", cwd="/home/u/projects/demo/sub")
    turns = [base.Turn("user", "t", "hi")]
    rec = json.loads(adapters.render_envelope(meta, turns).splitlines()[0])
    assert adapters.project_from_cwd(rec["cwd"]) == "demo"


def test_envelope_is_ascii_so_the_server_can_open_it_as_utf8(tmp_path):
    """`server.py` opens transcripts with `encoding="utf-8"` and no error
    handler, so an envelope it cannot decode raises inside the counter."""
    meta = base.SessionMeta(session_id="codex-u", client="codex", project="demo")
    turns = [base.Turn("user", "t", "naïve — 🚀 中文")]
    written = envelope.write_envelope(meta, turns, tmp_path)
    written.read_text(encoding="utf-8")  # must not raise
    assert envelope.count_user_turns(written) == 1


# --------------------------------------------------------------------------
# The envelope/claude collision
# --------------------------------------------------------------------------

def test_claude_adapter_never_extracts_a_codex_envelope(tmp_path, monkeypatch):
    """The envelope is Claude-*shaped* on purpose, so shape cannot route it.

    It lands in the archive directory that `adapters.claude.discover()` globs.
    Without id-based routing the claude adapter would re-extract it and
    overwrite the codex conversation with `client: claude`.
    """
    import process_transcripts

    archive = tmp_path / "transcripts"
    conversations = tmp_path / "conversations"
    archive.mkdir()
    conversations.mkdir()
    monkeypatch.setattr(process_transcripts, "CONVERSATIONS_DIR", conversations)

    meta = base.SessionMeta(session_id="codex-abc", client="codex", project="demo",
                            cwd="/home/u/projects/demo")
    envelope_path = envelope.write_envelope(
        meta, [base.Turn("user", "2026-01-01T00:00:00.000Z", "hi")], archive)

    # The claude path is handed the envelope, exactly as discovery would.
    assert process_transcripts.ensure_conversation_md(envelope_path, "codex-abc") is None
    assert list(conversations.glob("*.md")) == []

    # Control: a genuinely Claude-owned id on the same path is still processed.
    claude_path = archive / "plain-session.jsonl"
    claude_path.write_text(json.dumps({
        "type": "user", "timestamp": "2026-01-01T00:00:00.000Z",
        "cwd": "/home/u/projects/demo", "message": {"role": "user", "content": "hi"}}) + "\n")
    out = process_transcripts.ensure_conversation_md(claude_path, "plain-session")
    assert out is not None and "client: claude" in out.read_text()


# --------------------------------------------------------------------------
# Conformance attacks
# --------------------------------------------------------------------------

def _fake_adapter(**overrides) -> types.ModuleType:
    mod = types.ModuleType("fake_adapter")
    mod.discover = lambda: []
    mod.session_meta = lambda ref: base.SessionMeta(session_id="x", client="fake")
    mod.turns = lambda ref: iter(())
    mod.client_name = lambda: "fake"
    for key, value in overrides.items():
        setattr(mod, key, value)
    return mod


@pytest.mark.parametrize("missing", ["discover", "session_meta", "turns", "client_name"])
def test_conforms_rejects_an_adapter_missing_a_protocol_function(missing):
    mod = _fake_adapter()
    delattr(mod, missing)
    assert not base.conforms(mod)


def test_conforms_rejects_a_non_callable_attribute():
    assert not base.conforms(_fake_adapter(discover=["not", "callable"]))


def test_conforms_accepts_a_complete_adapter():
    """Non-trigger control — otherwise `conforms` could just return False."""
    assert base.conforms(_fake_adapter())


@pytest.mark.parametrize("module", [claude_or_codex for claude_or_codex in
                                    (adapters.get(n) for n in adapters.names())],
                         ids=adapters.names())
def test_registered_adapters_conform(module):
    assert base.conforms(module)
    assert module.client_name() in adapters.names()


def test_registry_rejects_an_unknown_client():
    with pytest.raises(KeyError):
        adapters.get("gemini")


def test_discover_returns_well_typed_refs_with_unique_ids():
    """Phantom or duplicate sessions from discover() are a silent corruption.

    Two refs with the same id write to one path in the flat archive directory,
    so the second silently replaces the first.
    """
    refs = codex.discover()
    ids = [r.session_id for r in refs]
    assert len(ids) == len(set(ids)), "discover() returned duplicate session ids"
    for ref in refs[:50]:
        assert isinstance(ref, base.SessionRef)
        assert ref.client == "codex"
        assert ref.session_id.startswith("codex-")
        assert ref.path.exists(), f"discover() returned a phantom session: {ref.path}"


# --------------------------------------------------------------------------
# Discovery and parse edge cases
# --------------------------------------------------------------------------

def test_malformed_line_mid_file_is_skipped_not_fatal(tmp_path):
    path = tmp_path / "rollout-broken.jsonl"
    path.write_text(
        json.dumps(_meta_record()) + "\n"
        + '{"timestamp": "2026-01-01T00:00:01.000Z", "type": "event_msg", "payload": '
          '{"type": "user_message", "message": "before"}}\n'
        + "{not json at all\n"
        + "\n"
        + "[1, 2, 3]\n"
        + '{"timestamp": "2026-01-01T00:00:05.000Z", "type": "event_msg", "payload": '
          '{"type": "agent_message", "message": "after"}}\n'
    )
    meta, turns = codex.parse(codex.ref_for_path(path, session_id="codex-broken"))
    texts = [t.text for t in turns]
    assert "before" in texts and "after" in texts
    assert meta.ended == "2026-01-01T00:00:05.000Z"


def test_unreadable_file_yields_meta_and_no_turns(tmp_path):
    path = tmp_path / "rollout-gone.jsonl"  # never created
    meta, turns = codex.parse(codex.ref_for_path(path, session_id="codex-gone"))
    assert turns == []
    assert meta.session_id == "codex-gone"
    assert meta.client == "codex"


def test_empty_file_still_renders_a_registry_entry(tmp_path):
    path = tmp_path / "rollout-empty.jsonl"
    path.write_text("")
    meta, turns = codex.parse(codex.ref_for_path(path, session_id="codex-empty"))
    out = adapters.render_conversation(meta, turns)
    assert out.startswith("---\nsession_id: codex-empty\n")
    assert "turns: 0\n" in out


def test_discover_survives_a_missing_sessions_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(codex, "SESSIONS_DIR", tmp_path / "nope")
    assert codex.discover() == []


def test_discover_skips_files_that_are_not_rollouts(tmp_path, monkeypatch):
    root = tmp_path / "sessions" / "2026" / "01" / "01"
    root.mkdir(parents=True)
    (root / "rollout-2026-01-01T00-00-00-019ffa3a-5670-7b12-8895-334229d49024.jsonl").write_text("")
    (root / "session_index.jsonl").write_text("")
    (root / "notes.txt").write_text("")
    monkeypatch.setattr(codex, "SESSIONS_DIR", tmp_path / "sessions")

    refs = codex.discover()
    assert [r.session_id for r in refs] == ["codex-019ffa3a-5670-7b12-8895-334229d49024"]


def test_parse_cache_is_invalidated_when_a_session_grows(tmp_path):
    path = _session([
        _meta_record(),
        {"timestamp": "2026-01-01T00:00:01.000Z", "type": "event_msg",
         "payload": {"type": "user_message", "message": "first"}},
    ], tmp_path)
    ref = codex.ref_for_path(path, session_id="codex-grow")
    assert [t.text for t in codex.parse(ref)[1]] == ["first"]

    with open(path, "a") as f:
        f.write(json.dumps({"timestamp": "2026-01-01T00:00:09.000Z", "type": "event_msg",
                            "payload": {"type": "user_message", "message": "second"}}) + "\n")
    assert [t.text for t in codex.parse(ref)[1]] == ["first", "second"]


# --------------------------------------------------------------------------
# The live corpus, when it exists
# --------------------------------------------------------------------------

HAS_CORPUS = codex.SESSIONS_DIR.exists() and any(codex.SESSIONS_DIR.rglob("rollout-*.jsonl"))


@pytest.mark.skipif(not HAS_CORPUS, reason="no ~/.codex/sessions on this machine")
def test_every_real_session_parses_without_crashing():
    crashed = []
    for ref in codex.discover():
        try:
            codex.parse(ref)
        except Exception as exc:  # noqa: BLE001 - the point is that nothing escapes
            crashed.append(f"{ref.path.name}: {exc}")
    assert not crashed, crashed


@pytest.mark.skipif(not HAS_CORPUS, reason="no ~/.codex/sessions on this machine")
def test_no_real_session_silently_yields_zero_turns():
    """The failure mode this adapter was rewritten to avoid, on real data."""
    empty = []
    for ref in codex.discover():
        meta, turns = codex.parse(ref)
        if meta.is_subagent:
            continue
        if not any(t.role == "user" and t.text for t in turns):
            empty.append(ref.path.name)
    assert not empty, f"sessions that would vanish from every narrative: {empty}"
