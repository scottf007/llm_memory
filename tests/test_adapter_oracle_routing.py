"""F-08 thread 2 — the oracle must route each client through its own adapter.

Pins `docs/design/f08-thread2-oracle-2026-09-03.md` (D1-D6). Proven by judge
verdict 01788087479282960606-f08-codexdrift-review-opus-16edf6a6: on main,
`regenerate()` hardcodes the Claude adapter against every stored session, so a
foreign client's `raw_source:` (the client's own file) is never consulted and
every codex/grok session fails "by construction," not by real drift.

Every test here monkeypatches `adapter_oracle.CONV_DIR` / `ARCHIVE_DIR` to a
tmp_path sandbox and stores sessions exactly as production does — never the
live store. A foreign session is built the way `process_transcripts.
process_foreign_session` builds it: `adapters.render(ref)` for the stored
`.md`, `adapters.write_envelope(meta, turns, ARCHIVE_DIR)` for the envelope —
so the stored file is byte-true to what the pipeline actually writes.

Test authorship: frozen tests only, no implementation. Does not modify
`tools/adapter_oracle.py`, `adapters/`, `docs/`, or the pinned oracle sample.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import adapter_oracle  # noqa: E402
import adapters  # noqa: E402
from adapters import codex as codex_adapter  # noqa: E402
from adapters import grok as grok_adapter  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CODEX_FIXTURE = FIXTURES / "codex" / "10-depth_3-user-turns-u3.jsonl"
GROK_FIXTURE = FIXTURES / "grok" / "02-single-prompt-primary"
GROK_SUPERSEDED_FIXTURE = FIXTURES / "grok" / "04-superseded-parent"


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """CONV_DIR / ARCHIVE_DIR redirected to tmp_path. Never the live store."""
    conv_dir = tmp_path / "conversations"
    archive_dir = tmp_path / "transcripts"
    conv_dir.mkdir()
    archive_dir.mkdir()
    monkeypatch.setattr(adapter_oracle, "CONV_DIR", conv_dir)
    monkeypatch.setattr(adapter_oracle, "ARCHIVE_DIR", archive_dir)
    return conv_dir, archive_dir


def _store_foreign_session(fixture_path: Path, adapter) -> str:
    """Store one foreign session exactly as `process_foreign_session` does.

    `adapters.render(ref)` -> CONV_DIR/<sid>.md (the stored conversation);
    `adapters.write_envelope` -> ARCHIVE_DIR/<sid>.jsonl (the Claude-shaped
    envelope). The client's own file at `fixture_path` is left untouched and
    is what `raw_source:` in the stored frontmatter points at.
    """
    ref = adapter.ref_for_path(fixture_path)
    md_text = adapters.render(ref)
    (adapter_oracle.CONV_DIR / f"{ref.session_id}.md").write_text(md_text)
    meta, turns = adapter.parse(ref)
    adapters.write_envelope(meta, turns, adapter_oracle.ARCHIVE_DIR)
    return ref.session_id


def _store_claude_session(session_id: str, entries: list[dict]) -> str:
    """Store one Claude session the current (unchanged) way: ARCHIVE_DIR
    holds the archived transcript directly, no envelope indirection."""
    jsonl_path = adapter_oracle.ARCHIVE_DIR / f"{session_id}.jsonl"
    jsonl_path.write_text("".join(__import__("json").dumps(e) + "\n" for e in entries))
    md_text = adapters.extract_session(jsonl_path)
    (adapter_oracle.CONV_DIR / f"{session_id}.md").write_text(md_text)
    return session_id


def _drop_frontmatter_line(text: str, prefix: str) -> str:
    return "".join(line for line in text.splitlines(keepends=True) if not line.startswith(prefix))


def _counts_near(out: str, client: str) -> list[str]:
    """Numbers on whatever line names `client`, for a format-agnostic check
    of D4's 'per-client ok/fail/unavailable counts' without pinning the
    implementer's exact wording."""
    for line in out.splitlines():
        if client in line:
            nums = re.findall(r"\d+", line)
            if nums:
                return nums
    return []


# --------------------------------------------------------------------------
# Plan test 1 — trigger, codex
# --------------------------------------------------------------------------

def test_codex_session_routed_through_its_own_adapter_is_ok(sandbox):
    """D1: a codex session's raw_source is the client's own file, not the
    project's own envelope. Red on main: regenerate() renders the envelope
    through the Claude adapter, producing `client: claude` against the
    stored `client: codex` (and unrelated content, since the envelope only
    carries a minimal Claude-shaped subset of the real transcript)."""
    sid = _store_foreign_session(CODEX_FIXTURE, codex_adapter)
    assert sid.startswith("codex-")

    ok, detail, diff = adapter_oracle.check(sid)
    assert ok, f"{detail}\n" + "".join(diff[:60])


# --------------------------------------------------------------------------
# Plan test 2 — trigger, grok
# --------------------------------------------------------------------------

def test_grok_session_routed_through_its_own_adapter_is_ok(sandbox):
    """Same defect, other foreign client. `raw_source:` is the
    chat_history.jsonl path as adapters/grok.py writes it; `ref_for_path`
    takes the session directory, not that file, per D1's note that the
    adapter -- not the oracle -- owns the raw_source -> ref mapping."""
    sid = _store_foreign_session(GROK_FIXTURE, grok_adapter)
    assert sid.startswith("grok-")

    ok, detail, diff = adapter_oracle.check(sid)
    assert ok, f"{detail}\n" + "".join(diff[:60])


def test_foreign_source_ref_is_owned_by_the_adapter(sandbox, monkeypatch, tmp_path):
    """T-F1: the oracle delegates source-path interpretation to its adapter."""
    source = tmp_path / "foreign" / "source.dat"
    source.parent.mkdir()
    source.write_text("source")
    session_id = "fake-source-routing"
    (adapter_oracle.CONV_DIR / f"{session_id}.md").write_text(
        f"---\nclient: fake\nraw_source: {source}\n---\n"
    )
    calls = []

    def ref_for_source(path, session_id=None):
        calls.append((path, session_id))
        return object()

    fake = SimpleNamespace(ref_for_source=ref_for_source)
    monkeypatch.setattr(adapter_oracle.adapters, "client_for_session_id", lambda _: "fake")
    monkeypatch.setattr(adapter_oracle.adapters, "get", lambda _: fake)
    monkeypatch.setattr(adapter_oracle.adapters, "render", lambda _: "rendered")

    assert adapter_oracle.regenerate(session_id) == "rendered"
    assert calls == [(source, session_id)]


# --------------------------------------------------------------------------
# Plan test 3 — control, claude
# --------------------------------------------------------------------------

def test_claude_session_control_reproduces_with_and_without_client_line(sandbox):
    """Non-trigger control: D1/D2 must not touch the Claude path. Green
    today and after -- this is the behaviour the fix must preserve."""
    sid = _store_claude_session("oracle-ctrl-claude", [
        {"type": "user", "timestamp": "2026-01-01T00:00:00.000Z",
         "cwd": "/home/user/projects/demo",
         "message": {"role": "user", "content": "hello"}},
        {"type": "assistant", "timestamp": "2026-01-01T00:00:01.000Z",
         "message": {"content": [{"type": "text", "text": "hi"}]}},
    ])

    ok, detail, diff = adapter_oracle.check(sid)
    assert ok, f"{detail}\n" + "".join(diff[:60])

    # The single-`client:`-line allowance for an unmigrated stored file.
    md_path = adapter_oracle.CONV_DIR / f"{sid}.md"
    md_path.write_text(_drop_frontmatter_line(md_path.read_text(), "client:"))

    ok2, detail2, diff2 = adapter_oracle.check(sid)
    assert ok2, f"{detail2}\n" + "".join(diff2[:60])


# --------------------------------------------------------------------------
# Plan test 4 — the bug as a test
# --------------------------------------------------------------------------

def test_compare_fails_on_the_client_line_when_wrong_adapter_renders(sandbox):
    """Pins the exact mechanism `regenerate()`'s Claude-only hardcoding
    triggers today: a Claude-adapter rendering compared against an
    already-migrated `client: codex` stored file fails on the client line.
    This is `compare()` in isolation -- unaffected by D1/D2, since those
    change what `check()`/`regenerate()` feed into `compare()`, not
    `compare()` itself. Green today and after; the mechanism must not
    silently stop firing once routing is fixed."""
    stored_codex_bytes = (
        "---\nsession_id: s\nclient: codex\nraw: r\nraw_source: src\nturns: 1\n"
        "---\n\nbody\n"
    ).encode()
    claude_rendered_text = (
        "---\nsession_id: s\nclient: claude\nraw: r\nraw_source: src\nturns: 1\n"
        "---\n\nbody\n"
    )

    ok, detail, diff = adapter_oracle.compare(
        stored_codex_bytes, claude_rendered_text, expected_client="codex"
    )
    assert not ok, detail
    diff_text = "".join(diff)
    assert "client: codex" in diff_text
    assert "client: claude" in diff_text


# --------------------------------------------------------------------------
# Plan test 5 — unavailable
# --------------------------------------------------------------------------

def test_unavailable_when_raw_source_file_is_missing(sandbox, tmp_path):
    """D3: a foreign session whose raw_source no longer exists is
    `unavailable`, not a diff -- distinct from both ok and fail."""
    movable_source = tmp_path / "movable" / "10-depth_3-user-turns-u3.jsonl"
    movable_source.parent.mkdir()
    shutil.copy(CODEX_FIXTURE, movable_source)
    sid = _store_foreign_session(movable_source, codex_adapter)
    movable_source.unlink()

    ok, detail, diff = adapter_oracle.check(sid)
    assert not ok
    assert "unavailable" in detail
    assert str(movable_source) in detail
    assert diff == []


def test_unavailable_when_raw_source_line_is_absent(sandbox):
    """Same distinct status for a foreign id with no `raw_source:` line at
    all -- the oracle has nothing to regenerate from, which is not the same
    failure as content that no longer matches."""
    sid = _store_foreign_session(CODEX_FIXTURE, codex_adapter)
    md_path = adapter_oracle.CONV_DIR / f"{sid}.md"
    md_path.write_text(_drop_frontmatter_line(md_path.read_text(), "raw_source:"))

    ok, detail, diff = adapter_oracle.check(sid)
    assert not ok
    assert "unavailable" in detail
    assert diff == []


def test_foreign_subagent_stub_without_raw_source_replays_its_envelope(sandbox):
    """T-F3: a superseded Grok stub has no raw_source, but is still replayable."""
    sid = _store_foreign_session(GROK_SUPERSEDED_FIXTURE, grok_adapter)
    stored = (adapter_oracle.CONV_DIR / f"{sid}.md").read_text()
    assert "agent_session: true" in stored
    assert "raw_source:" not in stored

    ok, detail, diff = adapter_oracle.check(sid)
    assert ok, f"{detail}\n" + "".join(diff[:60])


def test_main_exit_code_is_zero_when_unavailable_is_the_only_problem(sandbox, tmp_path, monkeypatch, capsys):
    """D3's exit-code half: `unavailable` alone must not fail the run --
    only a genuine mismatch does."""
    movable_source = tmp_path / "movable2" / "02-single-prompt-primary"
    movable_source.parent.mkdir()
    shutil.copytree(GROK_FIXTURE, movable_source)
    sid = _store_foreign_session(movable_source, grok_adapter)
    shutil.rmtree(movable_source)

    monkeypatch.setattr(sys, "argv", ["adapter_oracle.py", "--all"])
    exit_code = adapter_oracle.main()
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "unavailable" in out
    assert sid in out


# --------------------------------------------------------------------------
# Plan test 6 — per-client summary
# --------------------------------------------------------------------------

def test_all_reports_per_client_counts_and_exit_code(sandbox, monkeypatch, capsys):
    """D4: the final summary must name each client's ok/fail/unavailable
    counts, never mix clients into one figure. D1/D2's fix is what makes
    the codex and grok sessions here reproduce at all; exit code flips to 1
    only for a genuine mismatch, never for routing."""
    _store_claude_session("oracle-c1", [
        {"type": "user", "timestamp": "2026-01-01T00:00:00.000Z", "message": {"content": "hi 1"}},
    ])
    _store_claude_session("oracle-c2", [
        {"type": "user", "timestamp": "2026-01-02T00:00:00.000Z", "message": {"content": "hi 2"}},
    ])
    codex_sid = _store_foreign_session(CODEX_FIXTURE, codex_adapter)
    grok_sid = _store_foreign_session(GROK_FIXTURE, grok_adapter)

    monkeypatch.setattr(sys, "argv", ["adapter_oracle.py", "--all"])
    exit_code = adapter_oracle.main()
    out = capsys.readouterr().out

    assert exit_code == 0, out
    for client in ("claude", "codex", "grok"):
        nums = _counts_near(out, client)
        assert nums, f"no per-client summary line mentions {client!r}:\n{out}"

    # Corrupt one stored file: the run must now fail, and only that client's
    # fail count (not an unavailable or ok count) must account for it.
    corrupted_md = adapter_oracle.CONV_DIR / f"{codex_sid}.md"
    corrupted_md.write_text(corrupted_md.read_text() + "\ncorrupted-by-test\n")

    exit_code2 = adapter_oracle.main()
    out2 = capsys.readouterr().out
    assert exit_code2 == 1, out2
    assert grok_sid  # sanity: grok session id was produced and stored


def test_all_reports_unknown_client_prefix_as_unregistered(sandbox, monkeypatch, capsys):
    """T-F3: a stored foreign prefix without an adapter is not counted as Claude."""
    sid = _store_claude_session("opencode-synthetic", [
        {"type": "user", "timestamp": "2026-01-01T00:00:00.000Z",
         "message": {"content": "hello"}},
    ])
    stored = adapter_oracle.CONV_DIR / f"{sid}.md"
    stored.write_text(stored.read_text().replace("client: claude", "client: opencode"))

    monkeypatch.setattr(sys, "argv", ["adapter_oracle.py", "--all"])
    assert adapter_oracle.main() == 1
    out = capsys.readouterr().out

    assert f"FAIL {sid}" in out
    assert "unregistered: ok=0 fail=1 unavailable=0" in out
