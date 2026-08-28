"""The adapter oracle, as a test.

Two layers, because they fail for different reasons:

1. **Golden fixtures** — synthetic transcripts with hand-written expected
   output. These run everywhere, including CI on a machine with no memory
   corpus, and they pin the format rules one at a time (assistant grouping,
   the [L:N] ref, noise-tag stripping, the subagent stub).

2. **The corpus oracle** — regenerate real stored conversations and require
   byte equality. This is the strong test and the reason the refactor is
   safe, but it can only run where the corpus exists, so it skips elsewhere.

Layer 1 without layer 2 would let a subtle parser change through. Layer 2
without layer 1 would go quiet on CI. Both.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import adapter_oracle  # noqa: E402
import adapters  # noqa: E402
from adapters import claude as claude_adapter  # noqa: E402


def _write_jsonl(path: Path, entries: list[dict]) -> Path:
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))
    return path


def _render(path: Path) -> str:
    return adapters.extract_session(path)


# --------------------------------------------------------------------------
# Protocol
# --------------------------------------------------------------------------

def test_every_registered_adapter_implements_the_protocol():
    assert adapters.names(), "no adapters registered"
    for name in adapters.names():
        module = adapters.get(name)
        for func in ("discover", "session_meta", "turns", "client_name"):
            assert callable(getattr(module, func, None)), f"{name} is missing {func}()"
        assert module.client_name() == name


def test_every_adapter_module_on_disk_is_registered():
    """A written-but-unregistered adapter is invisible, not broken.

    That failure mode has no error message: the client's sessions simply never
    appear in any narrative. Cheaper to fail here.
    """
    infrastructure = {"__init__", "base", "render", "envelope"}
    package_dir = Path(__file__).resolve().parent.parent / "adapters"
    on_disk = {p.stem for p in package_dir.glob("*.py") if p.stem not in infrastructure}
    assert on_disk <= set(adapters.names()), f"unregistered adapters: {on_disk - set(adapters.names())}"


def test_installer_ships_the_adapters_package():
    """extract_conversation.py imports adapters/; the installer must copy it.

    ~/.claude/memory/lib self-updates from GitHub on session start, replacing
    flat *.py. If the copy block for adapters/ is ever dropped, that update
    lands a new extract_conversation.py next to no adapters package and every
    session stops producing a conversation .md — silently, from a hook.
    """
    install_sh = (Path(__file__).resolve().parent.parent / "install.sh").read_text()
    assert 'mkdir -p "$LIB_DIR/adapters"' in install_sh
    assert 'cp "$EXTRACTED/adapters/"*.py "$LIB_DIR/adapters/"' in install_sh


def test_unknown_client_is_a_loud_error():
    with pytest.raises(KeyError):
        adapters.get("nosuchclient")


def test_project_attribution_is_shared_not_per_adapter():
    assert adapters.project_from_cwd("/home/user/projects/llm_memory/tools") == "llm_memory"
    assert adapters.project_from_cwd("/home/user/scratch") == ""


def test_project_attribution_skips_dotted_infrastructure_directories():
    cwd = "/home/user/projects/.agent-messaging-worktrees/llm_memory/job/seat"
    assert adapters.project_from_cwd(cwd) == "llm_memory"
    assert adapters.project_from_cwd("/home/user/projects/.infrastructure") == ""


# --------------------------------------------------------------------------
# Golden fixtures — the .md contract, rule by rule
# --------------------------------------------------------------------------

def test_full_session_renders_the_documented_contract(tmp_path):
    path = _write_jsonl(tmp_path / "demo-session.jsonl", [
        {"type": "user", "timestamp": "2026-01-01T00:00:00.000Z",
         "cwd": "/home/user/projects/demo",
         "message": {"role": "user",
                     "content": "hello <system-reminder>noise</system-reminder>there"}},
        {"type": "assistant", "timestamp": "2026-01-01T00:00:01.000Z",
         "message": {"content": [{"type": "text", "text": "thinking about it"},
                                 {"type": "tool_use", "name": "Read"}]}},
        # A synthetic user entry carrying only a tool result: no prose, and it
        # must not split the assistant group around it.
        {"type": "user", "timestamp": "2026-01-01T00:00:02.000Z",
         "message": {"content": [{"type": "tool_result", "content": "..."}]}},
        {"type": "assistant", "timestamp": "2026-01-01T00:00:03.000Z",
         "message": {"content": [{"type": "text", "text": "done"}]}},
        {"type": "user", "timestamp": "2026-01-01T00:00:04.000Z",
         "message": {"content": "thanks"}},
    ])

    assert _render(path) == (
        "---\n"
        "session_id: demo-session\n"
        "project: demo\n"
        "client: claude\n"
        "raw: transcripts/demo-session.jsonl\n"
        "turns: 2\n"
        "started: 2026-01-01T00:00:00.000Z\n"
        "ended: 2026-01-01T00:00:04.000Z\n"
        "---\n"
        "\n"
        "=== user 2026-01-01T00:00:00.000Z ===\n"
        "hello there\n"
        "\n"
        "=== assistant 2026-01-01T00:00:01.000Z [L:2] ===\n"
        "thinking about it\n"
        "\n"
        "done\n"
        "\n"
        "=== user 2026-01-01T00:00:04.000Z ===\n"
        "thanks\n"
    )


def test_tool_only_entry_donates_its_line_ref_to_the_group(tmp_path):
    """An assistant entry can be all tool call and no prose.

    It still names the line the group's [L:N] points at — that ref exists to
    locate the side effect, and the side effect is on that line.
    """
    path = _write_jsonl(tmp_path / "toolref.jsonl", [
        {"type": "assistant", "timestamp": "2026-01-01T00:00:00.000Z",
         "message": {"content": [{"type": "tool_use", "name": "Bash"}]}},
        {"type": "assistant", "timestamp": "2026-01-01T00:00:05.000Z",
         "message": {"content": [{"type": "text", "text": "ran it"}]}},
    ])

    out = _render(path)
    # Line 1 held the tool call; line 2 held the first text, which sets the
    # block timestamp.
    assert "=== assistant 2026-01-01T00:00:05.000Z [L:1] ===\nran it\n" in out


def test_text_only_assistant_block_has_no_line_ref(tmp_path):
    """Non-trigger control for the [L:N] ref: no tool call, no ref."""
    path = _write_jsonl(tmp_path / "noref.jsonl", [
        {"type": "assistant", "timestamp": "2026-01-01T00:00:00.000Z",
         "message": {"content": [{"type": "thinking", "thinking": "hmm"},
                                 {"type": "text", "text": "just talking"}]}},
    ])

    out = _render(path)
    assert "=== assistant 2026-01-01T00:00:00.000Z ===\njust talking\n" in out
    assert "[L:" not in out


def test_unattributed_session_omits_the_project_line(tmp_path):
    path = _write_jsonl(tmp_path / "nocwd.jsonl", [
        {"type": "user", "timestamp": "2026-01-01T00:00:00.000Z",
         "cwd": "/home/user/scratch", "message": {"content": "hi"}},
    ])

    out = _render(path)
    assert "project:" not in out
    assert "client: claude\n" in out


def test_empty_transcript_still_renders_a_valid_registry_entry(tmp_path):
    """Garbage in, frontmatter out.

    An unparseable or empty transcript must still produce a file the session
    registry can read; a missing .md would make the session invisible rather
    than visibly empty.
    """
    path = tmp_path / "empty.jsonl"
    path.write_text("not json\n\n{broken\n")

    assert _render(path) == (
        "---\n"
        "session_id: empty\n"
        "client: claude\n"
        "raw: transcripts/empty.jsonl\n"
        "turns: 0\n"
        "---\n"
        "\n"
        "\n"
    )


def test_subagent_session_is_stubbed_without_reading_the_transcript(tmp_path):
    path = tmp_path / "agent-abc123.jsonl"
    path.write_text("{}\n")

    assert _render(path) == (
        "---\n"
        "session_id: agent-abc123\n"
        "client: claude\n"
        "agent_session: true\n"
        "skipped: true\n"
        "raw: transcripts/agent-abc123.jsonl\n"
        "---\n"
        "\n"
        "_This is a subagent session. Full conversation is in the parent "
        "session's transcript; raw JSONL is preserved at the path above "
        "for inspection._\n"
    )


def test_parent_session_id_is_carried_into_frontmatter(tmp_path):
    path = _write_jsonl(tmp_path / "child.jsonl", [
        {"type": "user", "timestamp": "2026-01-01T00:00:00.000Z",
         "parentSessionId": "parent-999", "message": {"content": "hi"}},
    ])
    assert "parent_session_id: parent-999\n" in _render(path)


def test_growing_transcript_is_not_served_from_cache(tmp_path):
    """session_meta() and turns() share one parse; the cache is content-keyed.

    Live transcripts grow while a session runs, and a cache keyed on path
    alone would render yesterday's conversation forever.
    """
    path = _write_jsonl(tmp_path / "growing.jsonl", [
        {"type": "user", "timestamp": "2026-01-01T00:00:00.000Z", "message": {"content": "first"}},
    ])
    first = _render(path)
    assert "second" not in first

    with open(path, "a") as f:
        f.write(json.dumps({"type": "user", "timestamp": "2026-01-01T00:00:09.000Z",
                            "message": {"content": "second"}}) + "\n")
    second = _render(path)
    assert "second" in second
    assert "turns: 2\n" in second


# --------------------------------------------------------------------------
# The oracle's own comparison logic
# --------------------------------------------------------------------------

def test_oracle_accepts_only_the_declared_client_line():
    stored = "---\nsession_id: s\nraw: r\nturns: 1\n---\n\nbody\n"
    produced = "---\nsession_id: s\nclient: claude\nraw: r\nturns: 1\n---\n\nbody\n"

    ok, detail, _ = adapter_oracle.compare(stored.encode(), produced)
    assert ok
    assert "client: claude" in detail


def test_oracle_accepts_only_the_declared_archive_path_migration():
    stored = (
        "---\nsession_id: s\n"
        "raw: ~/.claude/memory/transcripts/s.jsonl\nturns: 1\n---\n\nbody\n"
    )
    produced = (
        "---\nsession_id: s\nclient: claude\n"
        "raw: transcripts/s.jsonl\nturns: 1\n---\n\nbody\n"
    )

    ok, detail, _ = adapter_oracle.compare(stored.encode(), produced)
    assert ok
    assert "declared path migration" in detail

    wrong_session = produced.replace("transcripts/s.jsonl", "transcripts/other.jsonl")
    ok2, _, _ = adapter_oracle.compare(stored.encode(), wrong_session)
    assert not ok2


def test_archive_path_normalization_does_not_touch_conversation_body():
    text = (
        "---\nsession_id: s\nraw: transcripts/s.jsonl\n---\n\n"
        "raw: ~/.claude/memory/transcripts/s.jsonl\n"
    )

    normalized, legacy = adapter_oracle.normalize_archive_path(text)
    assert normalized == text
    assert legacy == []


def test_oracle_rejects_a_duplicated_client_line():
    """Trigger (s1-judge M5): two excused lines instead of one.

    The stripper removes every `client:` line in the frontmatter, so without a
    count assertion a duplicate is excused and the oracle still reports the
    session as byte identical.
    """
    stored = "---\nsession_id: s\nraw: r\nturns: 1\n---\n\nbody\n"
    produced = "---\nsession_id: s\nclient: claude\nclient: claude\nraw: r\nturns: 1\n---\n\nbody\n"

    ok, detail, _ = adapter_oracle.compare(stored.encode(), produced)
    assert not ok
    assert "exactly" in detail


def test_oracle_rejects_a_wrong_client_value():
    """Trigger (s1-judge M5): the excused line says something else.

    This is the line the next adapter changes, so its value has to be checked
    before a second adapter exists.
    """
    stored = "---\nsession_id: s\nraw: r\nturns: 1\n---\n\nbody\n"
    produced = "---\nsession_id: s\nclient: claude\nclient: totally-bogus-value\nraw: r\nturns: 1\n---\n\nbody\n"

    ok, _, _ = adapter_oracle.compare(stored.encode(), produced)
    assert not ok

    single_bogus = "---\nsession_id: s\nclient: totally-bogus-value\nraw: r\nturns: 1\n---\n\nbody\n"
    ok2, _, _ = adapter_oracle.compare(stored.encode(), single_bogus)
    assert not ok2


def test_oracle_checks_the_client_value_it_was_told_to_expect():
    """Non-trigger control: the same line passes or fails on `expected_client`."""
    stored = "---\nsession_id: s\nraw: r\nturns: 1\n---\n\nbody\n"
    produced = "---\nsession_id: s\nclient: codex\nraw: r\nturns: 1\n---\n\nbody\n"

    ok, _, _ = adapter_oracle.compare(stored.encode(), produced, expected_client="codex")
    assert ok

    ok2, _, _ = adapter_oracle.compare(stored.encode(), produced, expected_client="claude")
    assert not ok2


def test_oracle_excuses_nothing_when_the_stored_file_already_has_provenance():
    """Once the corpus carries `client:`, there is nothing left to excuse."""
    both = "---\nsession_id: s\nclient: claude\nraw: r\nturns: 1\n---\n\nbody\n"
    ok, _, _ = adapter_oracle.compare(both.encode(), both)
    assert ok

    dropped = "---\nsession_id: s\nraw: r\nturns: 1\n---\n\nbody\n"
    ok2, _, _ = adapter_oracle.compare(both.encode(), dropped)
    assert not ok2


def test_oracle_rejects_any_other_difference():
    """Trigger control: one changed byte in the body must fail."""
    stored = "---\nsession_id: s\nraw: r\nturns: 1\n---\n\nbody\n"
    produced = "---\nsession_id: s\nclient: claude\nraw: r\nturns: 1\n---\n\nBody\n"

    ok, _, diff = adapter_oracle.compare(stored.encode(), produced)
    assert not ok
    assert diff


def test_oracle_does_not_strip_a_client_line_from_the_body():
    """The stripper must not reach past the frontmatter.

    A conversation that discusses `client: claude` in its text would otherwise
    have that line silently deleted before comparison, which is exactly how an
    oracle stops being one.
    """
    produced = "---\nsession_id: s\nclient: claude\nraw: r\n---\n\nclient: claude\n"
    stripped, removed = adapter_oracle.strip_client_line(produced)

    assert removed == ["client: claude"]
    assert stripped == "---\nsession_id: s\nraw: r\n---\n\nclient: claude\n"


def test_oracle_compares_bytes_not_decoded_text():
    """Bare \\r survives on disk; text-mode reads turn it into \\n.

    Comparing decoded text would call two different files identical (or two
    identical files different), so the comparison takes bytes.
    """
    body = "progress 50%\rprogress 100%\n"
    stored = ("---\nsession_id: s\nraw: r\n---\n\n" + body).encode()
    produced = "---\nsession_id: s\nclient: claude\nraw: r\n---\n\n" + body

    ok, _, _ = adapter_oracle.compare(stored, produced)
    assert ok

    newline_translated = produced.replace("\r", "\n")
    ok2, _, _ = adapter_oracle.compare(stored, newline_translated)
    assert not ok2


# --------------------------------------------------------------------------
# The corpus oracle
# --------------------------------------------------------------------------

def _corpus_sample() -> list[str]:
    if not adapter_oracle.CONV_DIR.exists() or not adapter_oracle.ARCHIVE_DIR.exists():
        return []
    return [
        sid
        for sid in (adapter_oracle.load_sample() or adapter_oracle.select(20))
        if (adapter_oracle.CONV_DIR / f"{sid}.md").exists()
        and (adapter_oracle.ARCHIVE_DIR / f"{sid}.jsonl").exists()
    ]


CORPUS_SAMPLE = _corpus_sample()


@pytest.mark.skipif(not CORPUS_SAMPLE, reason="no local conversation corpus to check against")
@pytest.mark.parametrize("session_id", CORPUS_SAMPLE)
def test_stored_conversation_regenerates_byte_for_byte(session_id):
    ok, detail, diff = adapter_oracle.check(session_id)
    assert ok, f"{session_id}: {detail}\n" + "".join(diff[:60])


@pytest.mark.skipif(not CORPUS_SAMPLE, reason="no local conversation corpus to check against")
def test_corpus_sample_is_actually_diverse():
    """A sample of twenty near-identical sessions would pass while broken."""
    projects, sizes = set(), []
    for sid in CORPUS_SAMPLE:
        md = adapter_oracle.CONV_DIR / f"{sid}.md"
        projects.add(adapter_oracle._stored_field(md, "project"))
        sizes.append(md.stat().st_size)

    assert len(projects) >= 3, f"sample spans only {projects}"
    assert max(sizes) > 100 * min(sizes) + 1000, "sample has no size spread"


@pytest.mark.skipif(not CORPUS_SAMPLE, reason="no local conversation corpus to check against")
def test_discover_finds_the_sampled_sessions():
    found = {ref.session_id for ref in claude_adapter.discover()}
    missing = [sid for sid in CORPUS_SAMPLE if sid not in found]
    assert not missing, f"discover() missed {missing}"
