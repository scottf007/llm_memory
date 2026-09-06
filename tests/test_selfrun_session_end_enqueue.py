"""Frozen tests — self-running extraction, area 1: SessionEnd enqueue
(design doc D1, acceptance #1; docs/design/self-running-extraction-2026-09-06.md).

`hooks/session_end.sh` must, after its existing archive/strip succeeds,
atomically write exactly one extraction request under
`$LLM_MEMORY_HOME/runtime/extraction-requests/` and dispatch
`systemctl --user start --no-block llm-memory-extract.service` (faked here —
see tests/fixtures/selfrun/fake_systemctl.sh) — all in well under the hook's
1s representative budget, and without ever invoking a model backend itself.
A transcript the hook already skips today must keep creating no request and
calling no systemctl.

RED on base c0570b2: session_end.sh has no enqueue step at all, so it never
creates a request file and never calls systemctl — this file's trigger test
fails on both counts. GREEN only once the implementer adds the enqueue step
per the Appendix.
"""

import json
from pathlib import Path

import pytest

from tests.fixtures.selfrun import helpers as H

REQUEST_SCHEMA_FIELDS = {
    "schema", "request_id", "project", "session_id", "transcript_path",
    "enqueued_at", "source",
}


def _requests(memory_home: Path) -> list[Path]:
    d = memory_home / "runtime" / "extraction-requests"
    if not d.is_dir():
        return []
    return sorted(d.glob("*.json"))


def test_substantive_session_end_enqueues_exactly_one_request_and_dispatches_systemctl(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    raw_dir = tmp_path / "raw-transcripts"
    raw_dir.mkdir()
    transcript = H.write_transcript(raw_dir, "selfrun-enqueue-sess")

    systemctl_log = tmp_path / "systemctl.log"
    claude_log_dir = tmp_path / "claude-calls"

    input_json = json.dumps({
        "session_id": "selfrun-enqueue-sess",
        "transcript_path": str(transcript),
        "cwd": "/home/user/projects/selfrunproj",
    })
    stdout, stderr, rc, wall = H.run_hook(
        "session_end.sh", home, memory_home, input_json,
        extra_env={
            "FAKE_SYSTEMCTL_LOG": str(systemctl_log),
            "FAKE_CLAUDE_LOG_DIR": str(claude_log_dir),
        },
    )
    assert rc == 0, f"session_end.sh must exit 0. stderr:\n{stderr}"

    # The pre-existing archive/extract path must be unaffected.
    assert (memory_home / "transcripts" / "selfrun-enqueue-sess.jsonl").is_file()
    assert (memory_home / "conversations" / "selfrun-enqueue-sess.md").is_file()

    requests = _requests(memory_home)
    assert len(requests) == 1, (
        f"expected exactly one extraction request, found {len(requests)}: {requests}. "
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
    req = json.loads(requests[0].read_text())
    assert REQUEST_SCHEMA_FIELDS <= req.keys(), (
        f"request file missing fields: {REQUEST_SCHEMA_FIELDS - req.keys()}; got {req}"
    )
    assert req["schema"] == 1
    assert req["session_id"] == "selfrun-enqueue-sess"
    assert req["project"] == "selfrunproj"
    assert req["source"] == "session_end"

    log_text = systemctl_log.read_text() if systemctl_log.exists() else ""
    assert "--user" in log_text and "start" in log_text and "--no-block" in log_text, (
        f"expected a `systemctl --user start --no-block ...` dispatch, got:\n{log_text!r}"
    )
    assert "llm-memory-extract.service" in log_text

    assert wall < 1.0, f"SessionEnd took {wall:.3f}s, budget is < 1s"

    # No model call is ever made from the hook itself.
    backend_calls = list(claude_log_dir.glob("call-*.argv")) if claude_log_dir.exists() else []
    assert backend_calls == [], f"hook must never invoke a backend directly, saw: {backend_calls}"


def test_control_transcript_the_hook_already_skips_enqueues_nothing(tmp_path):
    """Control: the pre-existing exit-0 skip path (no locatable transcript)
    must keep creating no request and calling no systemctl. This must stay
    green on base AND on the candidate."""
    home, memory_home = H.make_home(tmp_path)
    systemctl_log = tmp_path / "systemctl.log"

    input_json = json.dumps({
        "session_id": "selfrun-skip-sess",
        "transcript_path": "/does/not/exist.jsonl",
        "cwd": "/home/user/projects/selfrunproj",
    })
    stdout, stderr, rc, wall = H.run_hook(
        "session_end.sh", home, memory_home, input_json,
        extra_env={"FAKE_SYSTEMCTL_LOG": str(systemctl_log)},
    )
    assert rc == 0

    assert _requests(memory_home) == []
    assert not systemctl_log.exists() or systemctl_log.read_text() == ""
