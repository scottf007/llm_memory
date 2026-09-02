"""D8 (docs/design/grok-ingestion-2026-09-03.md): the incremental sweep skip.

`process_foreign_session` currently re-parses and rewrites a foreign
session's envelope and conversation.md on every run, unconditionally. D8
adds one rule: skip re-parsing when the envelope already exists with mtime
>= the client's source file mtime. Today codex re-parses all 127 sessions on
every session start; grok would add 981 more, and the design note budgets
the incremental sweep at <=1.5s. This is frozen RED on main -- the skip does
not exist yet, and the fixed-source-newer-than-envelope case is the
non-trigger control that must keep working both before and after.

Uses a fake adapter registered only for the duration of each test (via
monkeypatch on `adapters._REGISTRY`), never the live store, and never
`adapters/codex.py`'s or `adapters/grok.py`'s own module state -- the rule
under test lives entirely in `process_transcripts.process_foreign_session`.
"""

from __future__ import annotations

import time
import types
from pathlib import Path

import pytest

import adapters
import process_transcripts as pt
from adapters.base import SessionMeta, SessionRef, Turn


def _make_fake_adapter(name: str, text: str = "hello") -> types.ModuleType:
    mod = types.ModuleType(f"fake_{name}_adapter")

    def client_name() -> str:
        return name

    def discover():
        return []

    def session_meta(ref: SessionRef) -> SessionMeta:
        return SessionMeta(session_id=ref.session_id, client=name,
                            raw=f"transcripts/{ref.session_id}.jsonl",
                            raw_source=str(ref.path), started="2026-01-01T00:00:00.000Z",
                            ended="2026-01-01T00:01:00.000Z")

    def turns(ref: SessionRef):
        return [
            Turn("user", "2026-01-01T00:00:00.000Z", text),
            Turn("assistant", "2026-01-01T00:00:05.000Z", "ok"),
        ]

    def parse(ref: SessionRef):
        return session_meta(ref), list(turns(ref))

    mod.discover = discover
    mod.session_meta = session_meta
    mod.turns = turns
    mod.parse = parse
    mod.client_name = client_name
    return mod


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    archive = tmp_path / "transcripts"
    conv = tmp_path / "conversations"
    monkeypatch.setattr(pt, "ARCHIVE_DIR", archive)
    monkeypatch.setattr(pt, "CONVERSATIONS_DIR", conv)
    return archive, conv


def _register(monkeypatch, name: str, text: str = "hello"):
    fake = _make_fake_adapter(name, text)
    monkeypatch.setitem(adapters._REGISTRY, name, fake)
    return fake


def test_unchanged_source_is_not_reparsed_on_second_sweep(sandbox, tmp_path, monkeypatch):
    _register(monkeypatch, "fakeclient")
    source = tmp_path / "source.txt"
    source.write_text("v1")
    ref = SessionRef(session_id="fakeclient-s1", path=source, client="fakeclient")

    first = pt.process_foreign_session(ref)
    assert first is not None
    envelope_path, md_path = first
    env_mtime_1 = envelope_path.stat().st_mtime_ns
    md_mtime_1 = md_path.stat().st_mtime_ns

    time.sleep(0.01)
    second = pt.process_foreign_session(ref)
    assert second is not None
    envelope_path_2, md_path_2 = second
    assert envelope_path_2.stat().st_mtime_ns == env_mtime_1, (
        "an unchanged source must not cause a rewrite -- D8's whole point is "
        "that an incremental sweep skips work it already did")
    assert md_path_2.stat().st_mtime_ns == md_mtime_1


def test_source_newer_than_envelope_is_reparsed(sandbox, tmp_path, monkeypatch):
    """Non-trigger control: the skip must not become a permanent staleness."""
    _register(monkeypatch, "fakeclient", text="v1")
    source = tmp_path / "source.txt"
    source.write_text("v1")
    ref = SessionRef(session_id="fakeclient-s2", path=source, client="fakeclient")

    first = pt.process_foreign_session(ref)
    envelope_path, md_path = first
    env_mtime_1 = envelope_path.stat().st_mtime_ns

    time.sleep(0.01)
    source.write_text("v2 changed")
    source_stat = source.stat()
    import os
    os.utime(source, (source_stat.st_atime, source_stat.st_mtime + 5))

    second = pt.process_foreign_session(ref)
    envelope_path_2, _ = second
    assert envelope_path_2.stat().st_mtime_ns != env_mtime_1, (
        "a source newer than the existing envelope must be re-parsed, not skipped")


def test_skip_applies_to_codex_too(sandbox, tmp_path, monkeypatch):
    """D8: 'the skip applies to codex as well as grok' -- pinned against the
    real codex adapter's own ref shape, not a fake, so the rule is proven
    against the module the implementer will actually touch."""
    from adapters import codex

    real_fixtures = sorted((Path(__file__).parent / "fixtures" / "codex").glob("*.jsonl"))
    real_fixtures = [p for p in real_fixtures if ".expected" not in p.name]
    assert real_fixtures, "codex fixtures must be present for this control to mean anything"
    ref = None
    for candidate in real_fixtures:
        candidate_ref = codex.ref_for_path(candidate, session_id=f"codex-sweep-{candidate.stem}")
        meta, _ = codex.parse(candidate_ref)
        if not meta.is_subagent:
            ref = candidate_ref
            break
    assert ref is not None, "every codex fixture is a subagent stub; need a non-stub for this control"

    first = pt.process_foreign_session(ref)
    assert first is not None
    envelope_path, md_path = first
    env_mtime_1 = envelope_path.stat().st_mtime_ns

    time.sleep(0.01)
    second = pt.process_foreign_session(ref)
    envelope_path_2, _ = second
    assert envelope_path_2.stat().st_mtime_ns == env_mtime_1, (
        "the D8 skip must not be grok-only -- codex sessions never change once "
        "written, so re-parsing all 127 of them on every sweep is exactly the "
        "cost D8 exists to remove")


# ---------------------------------------------------------------------------
# PM amendment 2 (3 Sep 2026, ruling on judge verdict 7a00ec19, finding B1):
# D4 outranks D8. A session ingested as a chain tail can later be forked; its
# own source file is untouched by the fork, so a skip evaluated before the
# supersession check keeps the stale envelope + .md forever and the parent's
# prose is extracted twice. The contract: an adapter may expose
# `is_superseded(ref) -> bool`; process_foreign_session consults it BEFORE the
# mtime skip, removes stale artifacts for a superseded session and returns
# None. Adapters without that hook get the same cleanup after parse when the
# meta reports is_subagent. The unchanged-and-not-superseded case is the
# non-trigger control: the skip must still fire.
# ---------------------------------------------------------------------------

def test_superseded_after_envelope_exists_removes_stale_artifacts(sandbox, tmp_path, monkeypatch):
    """Trigger (B1): tail becomes superseded; source untouched; artifacts must go."""
    state = {"superseded": False}
    fake = _register(monkeypatch, "fakefork", text="PARENT_PROSE")
    fake.is_superseded = lambda ref: state["superseded"]
    source = tmp_path / "parent.txt"
    source.write_text("parent v1")
    ref = SessionRef(session_id="fakefork-parent", path=source, client="fakefork")

    first = pt.process_foreign_session(ref)
    assert first is not None
    envelope_path, md_path = first
    assert envelope_path.exists() and md_path.exists()
    assert "PARENT_PROSE" in md_path.read_text()

    # A child fork now names this session as its parent. The parent's own
    # source file does not change, so the mtime skip would fire if consulted
    # first.
    state["superseded"] = True
    second = pt.process_foreign_session(ref)
    assert second is None, (
        "a superseded session must return None even when its envelope already "
        "exists and its source is unchanged -- D4 outranks the D8 skip")
    assert not envelope_path.exists(), "stale envelope of a superseded session must be removed"
    assert not md_path.exists(), "stale conversation.md of a superseded session must be removed"
    archive, conv = sandbox
    assert sum(p.read_text().count("PARENT_PROSE") for p in conv.glob("*.md")) == 0


def test_reparse_reporting_subagent_removes_stale_artifacts(sandbox, tmp_path, monkeypatch):
    """Trigger (post-parse path): an adapter with no is_superseded hook whose
    parse now reports is_subagent, after a source change, must clean up too."""
    state = {"sub": False}
    fake = _register(monkeypatch, "fakesub", text="PARENT_PROSE")
    assert not hasattr(fake, "is_superseded")
    base_meta = fake.session_meta

    def session_meta(ref):
        meta = base_meta(ref)
        meta.is_subagent = state["sub"]
        return meta

    fake.session_meta = session_meta
    fake.parse = lambda ref: (session_meta(ref), list(fake.turns(ref)))
    source = tmp_path / "parent2.txt"
    source.write_text("v1")
    ref = SessionRef(session_id="fakesub-parent", path=source, client="fakesub")

    first = pt.process_foreign_session(ref)
    assert first is not None
    envelope_path, md_path = first

    state["sub"] = True
    time.sleep(0.01)
    source.write_text("v2")
    import os
    st = source.stat()
    os.utime(source, (st.st_atime, st.st_mtime + 5))

    second = pt.process_foreign_session(ref)
    assert second is None
    assert not envelope_path.exists() and not md_path.exists(), (
        "a re-parse that reports is_subagent must remove the artifacts it "
        "previously wrote, not leave them beside a None")


def test_not_superseded_unchanged_source_still_skips(sandbox, tmp_path, monkeypatch):
    """Non-trigger control: consulting is_superseded must not cost the skip."""
    fake = _register(monkeypatch, "fakefork2")
    fake.is_superseded = lambda ref: False
    source = tmp_path / "tail.txt"
    source.write_text("v1")
    ref = SessionRef(session_id="fakefork2-tail", path=source, client="fakefork2")

    first = pt.process_foreign_session(ref)
    assert first is not None
    envelope_path, md_path = first
    env_mtime_1 = envelope_path.stat().st_mtime_ns

    time.sleep(0.01)
    second = pt.process_foreign_session(ref)
    assert second is not None
    assert second[0].stat().st_mtime_ns == env_mtime_1, "the D8 skip must still fire for a live tail"
