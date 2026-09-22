"""Regression tests for extraction-backend resolution (2026-09-22).

The suite was green while the worker had never once succeeded on the owning
machine: `llm-memory-extract.service` died on
`FileNotFoundError: [Errno 2] No such file or directory: 'claude'` at every
activation, leaving `last_success: null` since setup.

The gap was that every existing backend test pins `LLM_MEMORY_CLAUDE_CMD` to
`tests/fixtures/selfrun/fake_claude.sh` -- an absolute path to a `.sh`, which
takes the `["bash", command]` branch and never touches PATH. The production
default (the bare name `claude`, resolved off whatever PATH systemd hands the
unit) had no coverage at all. These tests exercise that default.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

import extraction_worker as W


def _fake_cli(directory: Path, name: str = "claude") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    cli = directory / name
    cli.write_text("#!/bin/bash\nexit 0\n")
    cli.chmod(cli.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return cli


def test_bare_name_resolves_off_path(tmp_path, monkeypatch):
    cli = _fake_cli(tmp_path / "bin")
    monkeypatch.delenv("LLM_MEMORY_CLAUDE_CMD", raising=False)
    monkeypatch.setenv("PATH", str(cli.parent))
    assert W._resolve_claude_cmd() == [str(cli)]


def test_falls_back_to_user_local_bin_when_path_excludes_home(tmp_path, monkeypatch):
    """The exact production failure: a real CLI in ~/.local/bin, and a PATH
    that is systemd's minimal default, which does not include it."""
    home = tmp_path / "home"
    cli = _fake_cli(home / ".local" / "bin")
    monkeypatch.delenv("LLM_MEMORY_CLAUDE_CMD", raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv(
        "PATH",
        "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/snap/bin",
    )
    assert W._resolve_claude_cmd() == [str(cli)]


def test_unresolvable_backend_names_the_search(tmp_path, monkeypatch):
    """A miss must say where it looked. A bare FileNotFoundError reads as a
    missing install when the fault is an environment that cannot see one."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.delenv("LLM_MEMORY_CLAUDE_CMD", raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", str(empty))
    with pytest.raises(RuntimeError) as exc:
        W._resolve_claude_cmd()
    message = str(exc.value)
    assert "claude" in message
    assert "LLM_MEMORY_CLAUDE_CMD" in message
    assert str(empty) in message


def test_explicit_command_is_honoured_verbatim(tmp_path, monkeypatch):
    """An operator who pins a path has answered the question; no PATH lookup."""
    cli = _fake_cli(tmp_path / "elsewhere", name="pinned-cli")
    monkeypatch.setenv("LLM_MEMORY_CLAUDE_CMD", str(cli))
    monkeypatch.setenv("PATH", "")
    assert W._resolve_claude_cmd() == [str(cli)]


def test_shell_script_backend_still_takes_the_bash_branch(tmp_path, monkeypatch):
    script = tmp_path / "fake_claude.sh"
    script.write_text("#!/bin/bash\nexit 0\n")
    monkeypatch.setenv("LLM_MEMORY_CLAUDE_CMD", str(script))
    assert W._resolve_claude_cmd() == ["bash", str(script)]


def test_backend_failure_is_recorded_not_raised(tmp_path, monkeypatch):
    """A backend that cannot start must fail one request, not abort the drain.

    `_call_claude` used to sit outside `_merge`'s try/except, so an
    unresolvable CLI raised past `_process_project`'s per-request handling and
    killed the worker -- starving every other project's queue.
    """
    def boom(*a, **k):
        raise RuntimeError("extraction backend 'claude' not found on PATH")

    monkeypatch.setattr(W, "_observed_transcript_bounds", lambda p: ("A", "B"))
    monkeypatch.setattr(W, "_call_claude", boom)

    # _merge short-circuits on a missing state file before it ever reaches the
    # backend, so the project must genuinely exist for this to test anything.
    (tmp_path / "projects").mkdir(parents=True, exist_ok=True)
    (tmp_path / "projects" / "proj.json").write_text(json.dumps({
        "schema_version": "0.1", "project": "proj", "last_updated": None,
        "summary": {}, "operations": [], "decisions": [], "goals": [],
        "suggestions": [], "learnings": [], "done": [], "sessions": [],
        "narrative": {"rendered_at": None, "record_uuid": None, "drift_audit": None},
    }))

    req = {
        "transcript_path": str(tmp_path / "t.jsonl"),
        "session_id": "s1",
        "request_id": "r1",
    }
    ok, error, quarantine = W._merge(tmp_path, "proj", req, False)
    assert ok is False
    assert "not found on PATH" in error
    assert quarantine is None


# -- spend accounting ------------------------------------------------------
#
# `_record_spend` used to sit at the very end of `_merge`, reachable only once
# the delta had parsed and applied. Every call that was billed and then failed
# to parse recorded nothing: on SCOTT-PC, USD 19.91 across 7 calls against a
# ledger that still read zero. The call is paid for the moment it returns, so
# every exit from the post-payment region must leave a ledger entry.

def _project(tmp_path, name="proj"):
    (tmp_path / "projects").mkdir(parents=True, exist_ok=True)
    (tmp_path / "projects" / f"{name}.json").write_text(json.dumps({
        "schema_version": "0.1", "project": name, "last_updated": None,
        "summary": {}, "operations": [], "decisions": [], "goals": [],
        "suggestions": [], "learnings": [], "done": [], "sessions": [],
        "narrative": {"rendered_at": None, "record_uuid": None, "drift_audit": None},
    }))
    return {"transcript_path": str(tmp_path / "t.jsonl"),
            "session_id": "s1", "request_id": "r1"}


def _ledger(tmp_path):
    path = tmp_path / "runtime" / "extraction-spend.json"
    return json.loads(path.read_text()) if path.exists() else {}


def test_unparseable_response_is_still_banked(tmp_path, monkeypatch):
    """The regression that cost USD 19.91: paid call, garbage response."""
    req = _project(tmp_path)
    monkeypatch.setattr(W, "_observed_transcript_bounds", lambda p: ("A", "B"))
    monkeypatch.setattr(W, "_call_claude", lambda *a, **k: ("not json at all", "hash"))

    ok, error, _ = W._merge(tmp_path, "proj", req, False)
    assert ok is False

    entries = _ledger(tmp_path).get("entries", [])
    assert len(entries) == 1, "a billed call left no ledger entry"
    assert entries[0]["request_id"] == "r1"
    # Cost unknown, so the full session cap is reserved rather than zero.
    assert entries[0]["charged_usd"] > 0


def test_call_is_banked_exactly_once(tmp_path, monkeypatch):
    """The finally must not double-charge a call the success path banked."""
    req = _project(tmp_path)
    delta = {"session_id": "s1", "ledger_delta": {}}
    monkeypatch.setattr(W, "_observed_transcript_bounds", lambda p: ("A", "B"))
    monkeypatch.setattr(W, "_call_claude", lambda *a, **k: (json.dumps(delta), "hash"))
    monkeypatch.setattr(W, "_backend_response", lambda raw: (raw, {}))

    ok, error, _ = W._merge(tmp_path, "proj", req, False)
    assert ok is True, error
    assert len(_ledger(tmp_path).get("entries", [])) == 1


def test_unreadable_ledger_refuses_to_spend(tmp_path, monkeypatch):
    """A ledger that cannot be read is not evidence of zero spend."""
    req = _project(tmp_path)
    ledger = tmp_path / "runtime" / "extraction-spend.json"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("{ this is not json")

    called = []
    monkeypatch.setattr(W, "_call_claude", lambda *a, **k: called.append(1) or ("", ""))

    ok, error, _ = W._merge(tmp_path, "proj", req, False)
    assert ok is False
    assert "unreadable" in error
    assert not called, "spent money against an unknown running total"


def test_day_cap_default_exceeds_a_single_observed_call(monkeypatch):
    """USD 3 could not bind: one observed extraction cost USD 3.29."""
    monkeypatch.delenv("LLM_MEMORY_EXTRACT_DAY_CAP_USD", raising=False)
    source = Path(W.__file__).read_text()
    assert '"LLM_MEMORY_EXTRACT_DAY_CAP_USD", "5"' in source
