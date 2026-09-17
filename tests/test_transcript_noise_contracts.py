"""Frozen contracts for transcript-noise ingest and legacy reconciliation.

The representation of the persistent skip index is deliberately not fixed: a
JSON file, SQLite table, or equivalent durable store may implement it.  These
tests assert its observable consequences at the real ingest and coverage
boundaries instead.  In particular, ``test_r3_legacy_archives...`` starts
with *already archived* junk.  It prevents an implementation from indexing
only the no-envelope ingest path while retaining the old mtime early return.

Requirements covered:

* R1 — PONG health-checks, keep-alive loops, empty sources, and low-turn Grok
  sources do not get active archive artefacts and receive durable reasons.
* R2 — ordinary three-turn Grok work remains archived and coverage-visible.
* R3 — both newly seen and pre-existing junk are persistently excluded before
  coverage opens their JSONL bodies, including in a new Python process.
* R4 — reconciliation preserves legacy archive material in the sandbox; it
  may move it to quarantine, but must not destructively delete it.
"""

from __future__ import annotations

import builtins
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from adapters import grok
import process_transcripts as process
import server


SUBSTANTIVE_REPLY = (
    "This is deliberately substantive assistant prose. It records the outcome, "
    "the trade-off considered, and the next concrete verification step so that "
    "it clears the existing assistant-content gate."
)

KEEPALIVE_PROMPT = (
    "Self-wake KEEP-ALIVE FOR SEAT demo-seat on job demo-job. Run ONLY: "
    "am sync --job demo-job --seat demo-seat"
)

PONG_HEALTHCHECK_PROMPT = "Reply with exactly the word: PONG. Nothing else."

PONG_DISCUSSION_PROMPT = (
    "The protocol documentation calls the health-check response PONG. Review "
    "that wording and propose a migration plan for ordinary user work."
)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A complete memory root plus mutable Grok-shaped source directories."""
    home = tmp_path / "home"
    memory = home / ".claude" / "memory"
    for child in ("transcripts", "conversations", "projects"):
        (memory / child).mkdir(parents=True, exist_ok=True)
    (memory / "projects" / "demo.json").write_text(json.dumps({"sessions": []}))

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("LLM_MEMORY_HOME", str(memory))
    monkeypatch.setattr(process, "DB_DIR", memory)
    monkeypatch.setattr(process, "ARCHIVE_DIR", memory / "transcripts")
    monkeypatch.setattr(process, "CONVERSATIONS_DIR", memory / "conversations")
    monkeypatch.setattr(server, "DB_DIR", memory)

    # Adapter metadata caches include source stat state.  Tests own their
    # fixture paths, but clearing them also makes the source-version contract
    # independent of filesystem timestamp resolution and test ordering.
    grok._SUMMARY_CACHE.clear()
    grok._SUPERSEDED_CACHE.clear()
    return {"home": home, "memory": memory, "sources": tmp_path / "sources"}


def _tree_fingerprint(root: Path) -> dict[Path, str]:
    """Snapshot persistent memory state without assuming its index format."""
    return {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def _changed_skip_material(memory: Path, session_id: str, before: dict[Path, str]) -> list[bytes]:
    """Return changed durable records carrying an ineligible session identity.

    The index may be a single database/file for all sessions or one record per
    session.  It need only make the ID discoverable in its durable material;
    we intentionally do not require a particular path, schema, or API.
    """
    result: list[bytes] = []
    needle = session_id.encode()
    for path in memory.rglob("*"):
        if not path.is_file():
            continue
        contents = path.read_bytes()
        if before.get(path) != hashlib.sha256(contents).hexdigest() and needle in contents:
            result.append(contents.lower())
    return result


def _assert_durable_skip(
    memory: Path,
    session_id: str,
    before: dict[Path, str],
    reason_markers: tuple[bytes, ...],
) -> None:
    """Require a durable, inspectable classification reason for this source."""
    records = _changed_skip_material(memory, session_id, before)
    assert records, (
        "the ineligible session needs a durable index record carrying its ID; "
        "a per-process cache leaves the next coverage call to rescan it"
    )
    material = b"\n".join(records)
    assert any(marker in material for marker in reason_markers), (
        "the durable record must retain a stable skip reason, not merely a "
        f"boolean exclusion; expected one of {reason_markers!r} for {session_id}"
    )


def _write_grok_source(
    sandbox: dict[str, Path],
    raw_id: str,
    first_prompt: str | None,
    user_turns: int,
) -> tuple[Path, object]:
    """Create a mutable non-synthetic Grok source and return its real ref."""
    session_dir = sandbox["sources"] / raw_id
    session_dir.mkdir(parents=True, exist_ok=True)
    # Keep this independent of this artifact's own project path.  Grok's
    # project attribution intentionally derives the first projects/<name>
    # component of cwd, so a fixture-owned CWD gives a known demo project.
    cwd = Path("/fixture-root/projects/demo")
    (session_dir / "summary.json").write_text(json.dumps({
        "created_at": "2026-09-17T00:00:00.000Z",
        "last_active_at": "2026-09-17T00:10:00.000Z",
        "info": {"cwd": str(cwd)},
    }))

    records: list[dict] = []
    events: list[dict] = []
    for index in range(user_turns):
        prompt = first_prompt if index == 0 else (
            f"Follow-up {index}: preserve normal mergeable narrative work."
        )
        records.extend((
            {
                "type": "user",
                "prompt_index": index,
                "content": [{"type": "text", "text": prompt}],
            },
            {"type": "assistant", "content": SUBSTANTIVE_REPLY},
        ))
        events.append({"type": "turn_started", "ts": f"2026-09-17T00:0{index}:00.000Z"})
    (session_dir / "chat_history.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    (session_dir / "events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
    session_id = grok.session_id_for(raw_id)
    return session_dir, grok.ref_for_path(session_dir, session_id=session_id)


def _active_paths(memory: Path, session_id: str) -> tuple[Path, Path]:
    return (
        memory / "transcripts" / f"{session_id}.jsonl",
        memory / "conversations" / f"{session_id}.md",
    )


@pytest.mark.parametrize(
    ("raw_id", "first_prompt", "user_turns", "reason_markers"),
    [
        ("pong-healthcheck", PONG_HEALTHCHECK_PROMPT, 3, (b"pong", b"health")),
        ("keepalive-loop", KEEPALIVE_PROMPT, 3, (b"keepalive", b"keep-alive", b"harness")),
        ("two-turn", "Please summarize the current issue.", 2, (b"low", b"turn")),
        ("empty", None, 0, (b"empty", b"content")),
    ],
)
def test_r1_ineligible_grok_source_is_indexed_without_active_archive(
    sandbox, raw_id, first_prompt, user_turns, reason_markers
):
    """R1 trigger: classify before either normal archive write occurs.

    The PONG and keep-alive cases have three user turns.  Their rejection is
    therefore structural and cannot be an accidental low-turn success.
    """
    _source, ref = _write_grok_source(sandbox, raw_id, first_prompt, user_turns)
    memory = sandbox["memory"]
    before = _tree_fingerprint(memory)

    process.process_foreign_session(ref)

    envelope, conversation = _active_paths(memory, ref.session_id)
    assert not envelope.exists(), "an ineligible session must not receive an envelope"
    assert not conversation.exists(), "an ineligible session must not enter the registry"
    _assert_durable_skip(memory, ref.session_id, before, reason_markers)


def test_r1_and_r2_pong_discussion_and_three_turn_work_remain_mergeable(sandbox):
    """R1/R2 controls: only the narrow Grok health-check form is excluded."""
    _source, discussion_ref = _write_grok_source(
        sandbox, "pong-discussion", PONG_DISCUSSION_PROMPT, 3
    )
    _source, work_ref = _write_grok_source(
        sandbox, "ordinary-work", "Plan the data migration in three stages.", 3
    )
    memory = sandbox["memory"]

    process.process_foreign_session(discussion_ref)
    process.process_foreign_session(work_ref)

    for ref in (discussion_ref, work_ref):
        envelope, conversation = _active_paths(memory, ref.session_id)
        assert envelope.exists(), f"{ref.session_id} is genuine work and must archive"
        assert conversation.exists()
        assert "client: grok" in conversation.read_text()

    coverage = server.compute_narrative_coverage("demo")
    discussion_envelope, _ = _active_paths(memory, discussion_ref.session_id)
    work_envelope, _ = _active_paths(memory, work_ref.session_id)
    assert str(discussion_envelope) in coverage["unprocessed"]
    assert str(work_envelope) in coverage["unprocessed"]
    assert {entry["path"] for entry in coverage["unprocessed_sorted"]} >= {
        str(discussion_envelope), str(work_envelope)
    }


def test_r1_skip_is_scoped_to_grok_not_a_global_keepalive_phrase_ban(sandbox):
    """R1 non-trigger control: real Codex text may quote the Grok marker."""
    memory = sandbox["memory"]
    session_id = "codex-quoting-grok-marker"
    envelope, conversation = _active_paths(memory, session_id)
    envelope.write_text("".join(json.dumps(record) + "\n" for record in (
        {
            "type": "user", "timestamp": "2026-09-17T00:00:00Z",
            "message": {"role": "user", "content": KEEPALIVE_PROMPT},
        },
        {
            "type": "assistant", "timestamp": "2026-09-17T00:00:01Z",
            "message": {"role": "assistant", "content": [{"type": "text", "text": SUBSTANTIVE_REPLY}]},
        },
    )))
    conversation.write_text(
        f"---\nsession_id: {session_id}\nproject: demo\nclient: codex\n---\n\nreal work\n"
    )

    coverage = server.compute_narrative_coverage("demo")
    assert str(envelope) in coverage["unprocessed"]


def test_r1_source_growth_replaces_a_versioned_low_turn_skip_with_normal_archive(sandbox):
    """R1/R2 control: a skip cannot outlive an in-place source growth."""
    source, ref = _write_grok_source(
        sandbox, "growing-session", "Investigate the first part of this task.", 2
    )
    memory = sandbox["memory"]
    before = _tree_fingerprint(memory)
    process.process_foreign_session(ref)
    envelope, conversation = _active_paths(memory, ref.session_id)
    assert not envelope.exists() and not conversation.exists()
    _assert_durable_skip(memory, ref.session_id, before, (b"low", b"turn"))

    # Grok appends a third exchange to the same mutable source.  A decision
    # without source-version scope would leave this session wrongly excluded.
    history = source / "chat_history.jsonl"
    with history.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "type": "user", "prompt_index": 2,
            "content": [{"type": "text", "text": "Third substantive follow-up."}],
        }) + "\n")
        handle.write(json.dumps({"type": "assistant", "content": SUBSTANTIVE_REPLY}) + "\n")
    time.sleep(0.01)
    stamp = history.stat()
    os.utime(history, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000))

    process.process_foreign_session(ref)

    assert envelope.exists(), "a changed source that clears Grok's bar must archive"
    assert conversation.exists()
    coverage = server.compute_narrative_coverage("demo")
    assert str(envelope) in coverage["unprocessed"]


def _legacy_envelope_bytes(first_prompt: str, user_turns: int) -> bytes:
    """Claude-shaped bytes from the previous archive generation.

    They deliberately predate the new ingest feature, so the test writes the
    active envelope/registry pair before asking current ingest to reconcile.
    """
    records: list[dict] = []
    for index in range(user_turns):
        prompt = first_prompt if index == 0 else f"legacy follow-up {index}"
        records.extend((
            {
                "type": "user",
                "timestamp": f"2026-09-17T00:0{index}:00Z",
                "message": {"role": "user", "content": prompt},
            },
            {
                "type": "assistant",
                "timestamp": f"2026-09-17T00:0{index}:30Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": SUBSTANTIVE_REPLY}],
                },
            },
        ))
    return "".join(json.dumps(record) + "\n" for record in records).encode()


def _write_preexisting_archive(
    memory: Path, session_id: str, first_prompt: str, user_turns: int
) -> tuple[Path, Path, bytes, bytes]:
    """Plant a full legacy active pair, bypassing new ingest on purpose."""
    envelope, conversation = _active_paths(memory, session_id)
    envelope_bytes = _legacy_envelope_bytes(first_prompt, user_turns)
    conversation_bytes = (
        f"---\nsession_id: {session_id}\nproject: demo\nclient: grok\n"
        "started: 2026-09-17T00:00:00Z\nended: 2026-09-17T00:10:00Z\n"
        "---\n\nlegacy pre-feature archive\n"
    ).encode()
    envelope.write_bytes(envelope_bytes)
    conversation.write_bytes(conversation_bytes)
    return envelope, conversation, envelope_bytes, conversation_bytes


def _assert_legacy_pair_preserved(memory: Path, envelope_bytes: bytes, conversation_bytes: bytes) -> list[Path]:
    """S4 control: reconciliation may move an archive but may not delete it."""
    files = [path for path in memory.rglob("*") if path.is_file()]
    preserved_envelopes = [path for path in files if path.read_bytes() == envelope_bytes]
    assert preserved_envelopes, (
        "legacy junk may leave the active archive only by non-destructive "
        "quarantine; its envelope must remain somewhere under the memory root"
    )
    assert any(path.read_bytes() == conversation_bytes for path in files), (
        "legacy registry material may be quarantined but must not be destructively deleted"
    )
    return preserved_envelopes


def _fresh_process_coverage_must_not_open(
    sandbox: dict[str, Path], blocked: list[Path], eligible: Path
) -> None:
    """Run coverage after import, excluding an in-memory cache as a solution."""
    script = f"""
import builtins
from pathlib import Path
import server

blocked = {{Path(item) for item in {tuple(str(path) for path in blocked)!r}}}
eligible = Path({str(eligible)!r})
real_open = builtins.open
real_path_open = Path.open
real_read_text = Path.read_text
real_read_bytes = Path.read_bytes

def _guard(path):
    if path in blocked:
        raise AssertionError(f"coverage opened reconciled legacy junk: {{path}}")

def guarded_open(file, *args, **kwargs):
    try:
        _guard(Path(file))
    except TypeError:
        pass
    return real_open(file, *args, **kwargs)

def guarded_path_open(self, *args, **kwargs):
    _guard(self)
    return real_path_open(self, *args, **kwargs)

def guarded_read_text(self, *args, **kwargs):
    _guard(self)
    return real_read_text(self, *args, **kwargs)

def guarded_read_bytes(self, *args, **kwargs):
    _guard(self)
    return real_read_bytes(self, *args, **kwargs)

builtins.open = guarded_open
Path.open = guarded_path_open
Path.read_text = guarded_read_text
Path.read_bytes = guarded_read_bytes

coverage = server.compute_narrative_coverage("demo")
assert all(str(path) not in coverage["unprocessed"] for path in blocked)
assert str(eligible) in coverage["unprocessed"]
assert isinstance(coverage["unprocessed"], list)
assert isinstance(coverage["unprocessed_sorted"], list)
assert coverage["unprocessed_count"] == len(coverage["unprocessed"])
assert any(entry["path"] == str(eligible) for entry in coverage["unprocessed_sorted"])
"""
    env = os.environ.copy()
    env["HOME"] = str(sandbox["home"])
    env["LLM_MEMORY_HOME"] = str(sandbox["memory"])
    repo_root = str(next(
        parent for parent in Path(__file__).resolve().parents
        if (parent / "server.py").is_file()
    ))
    env["PYTHONPATH"] = repo_root + os.pathsep + env.get("PYTHONPATH", "")
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, (
        "fresh-process coverage must apply the durable exclusion before any "
        f"legacy body reader. stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )


def test_r3_legacy_archives_are_reconciled_non_destructively_before_coverage(sandbox):
    """R3/S1 trigger: reconcile old active junk, then never reread its bodies.

    This intentionally differs from the rejected first-round test: each junk
    envelope and conversation registry entry exists *before* the first ingest
    call.  A feature that only writes skip records when an envelope is absent
    leaves the old mtime fast path untouched and fails this test.  Three
    distinct historic classes model the large PONG/keep-alive/low-turn corpus.
    """
    memory = sandbox["memory"]
    legacy_cases = (
        ("legacy-pong", PONG_HEALTHCHECK_PROMPT, 3, (b"pong", b"health")),
        ("legacy-keepalive", KEEPALIVE_PROMPT, 3, (b"keepalive", b"keep-alive", b"harness")),
        ("legacy-low-turn", "Read these three files and reply with a verdict.", 2, (b"low", b"turn")),
    )
    planted = []
    for raw_id, first_prompt, turns, reason_markers in legacy_cases:
        _source, ref = _write_grok_source(sandbox, raw_id, first_prompt, turns)
        planted.append((ref, reason_markers, _write_preexisting_archive(
            memory, ref.session_id, first_prompt, turns
        )))

    # The active legacy files are newer than their source, exactly the state
    # that previously selected process_foreign_session's mtime early return.
    before = _tree_fingerprint(memory)
    for ref, reason_markers, _legacy in planted:
        process.process_foreign_session(ref)
        _assert_durable_skip(memory, ref.session_id, before, reason_markers)

    blocked: list[Path] = []
    for _ref, _reason_markers, (active, _conversation, envelope_bytes, conversation_bytes) in planted:
        # Include the former active pathname and every resulting quarantined
        # pathname, so neither an indexed implementation nor a moving one can
        # satisfy the fresh-process proof by reopening old material.
        blocked.append(active)
        blocked.extend(_assert_legacy_pair_preserved(memory, envelope_bytes, conversation_bytes))

    _source, eligible_ref = _write_grok_source(
        sandbox, "coverage-control", "Review the release candidate.", 3
    )
    process.process_foreign_session(eligible_ref)
    eligible, _ = _active_paths(memory, eligible_ref.session_id)
    assert eligible.exists(), "the qualifying control must enter normal coverage"

    _fresh_process_coverage_must_not_open(sandbox, blocked, eligible)
