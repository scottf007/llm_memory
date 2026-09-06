"""Frozen tests — self-running extraction, area 8: hook budgets (design doc
D1, D3; acceptance #6, #5's SessionStart-under-100ms claim;
docs/design/self-running-extraction-2026-09-06.md).

SessionEnd's representative run (archive + extract + enqueue + dispatch)
must have p95 < 1s over 20 runs — its existing 30s Claude Code timeout
budget, but the design's own tighter target. SessionStart, when only doing
the cheap extraction-status sidecar read (no coverage sweep, no model
subprocess), must have p95 < 100ms over 20 runs.

Unlike the other seven files, this one is NOT a plain RED/GREEN pair: on
base c0570b2, SessionEnd does no enqueue work at all yet, so its budget is
trivially met today (there is no new work to blow the budget) — this test
is a frozen non-regression CEILING the implementer's enqueue step must stay
under, not a trigger that starts failing. It is recorded GREEN on base for
that reason, not "RED for the wrong reason": there is no missing interface
here, only a number that must not get worse once the enqueue step exists.
The SessionStart-with-failed-status timing sub-test IS RED on base, because
`session_start.sh` does not read an extraction-status sidecar at all yet
(area 7 in test_selfrun_visibility.py covers correctness of that literal;
this file only covers ITS wall-clock cost once it exists).
"""

import json
import statistics
import time

import pytest

from tests.fixtures.selfrun import helpers as H


def _percentile95(samples):
    ordered = sorted(samples)
    idx = max(0, int(round(0.95 * (len(ordered) - 1))))
    return ordered[idx]


def test_session_end_p95_under_1s_over_20_runs(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    systemctl_log = tmp_path / "systemctl.log"
    walls = []
    for i in range(20):
        raw_dir = tmp_path / f"raw-{i}"
        raw_dir.mkdir()
        transcript = H.write_transcript(raw_dir, f"selfrun-budget-sess-{i}")
        input_json = json.dumps({
            "session_id": f"selfrun-budget-sess-{i}",
            "transcript_path": str(transcript),
            "cwd": "/home/user/projects/selfrunproj",
        })
        stdout, stderr, rc, wall = H.run_hook(
            "session_end.sh", home, memory_home, input_json,
            extra_env={"FAKE_SYSTEMCTL_LOG": str(systemctl_log)},
        )
        assert rc == 0, f"run {i} failed: {stderr}"
        walls.append(wall)

    p95 = _percentile95(walls)
    assert p95 < 1.0, f"SessionEnd p95 over 20 runs was {p95:.3f}s (samples: {walls})"


def test_session_start_with_failed_status_p95_under_100ms_over_20_runs(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    H.write_project_state(memory_home, "selfrunproj")
    status_path = memory_home / "projects" / "selfrunproj.extraction-status.json"
    status_path.write_text(json.dumps({
        "state": "failed", "unprocessed": 2, "stale": 0,
        "oldest_waiting": "2026-09-01T00:00:00Z", "last_attempt": "2026-09-06T00:00:00Z",
        "last_success": None, "backend": "claude", "request_ids": [],
        "retry_after": None, "error_summary": "auth expired",
        "quarantined_revaluations": {"count": 0, "paths": []},
    }))
    (memory_home / "config" / "no-auto-update").touch()

    walls = []
    for i in range(20):
        input_json = json.dumps({
            "source": "startup", "trigger": "startup",
            "cwd": "/home/user/projects/selfrunproj",
            "session_id": f"selfrun-budget-start-{i}",
        })
        stdout, stderr, rc, wall = H.run_hook("session_start.sh", home, memory_home, input_json)
        assert rc == 0, f"run {i} failed: {stderr}"
        assert "LLM_MEMORY_WARN: extraction" in stdout, (
            f"run {i} must still print the warning while measuring its cost:\n{stdout}"
        )
        walls.append(wall)

    p95 = _percentile95(walls)
    assert p95 < 0.100, f"SessionStart-with-failed-status p95 over 20 runs was {p95:.3f}s (samples: {walls})"
