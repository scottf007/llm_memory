"""Cost-accounting coverage for the self-running extraction worker.

These tests use the existing fake backend.  JSON envelopes are queued as its
ordinary response bytes, so no test fixture or frozen selfrun contract is
changed and no model call can occur.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.fixtures.selfrun import helpers as H


PROJECT = "selfrunproj"


def _delta(session_id: str) -> dict:
    value = json.loads(H.DELTA_SESSION_A.read_text())
    value["session_id"] = session_id
    value["ledger_delta"]["introduced"]["decisions"][0]["text"] = (
        f"COST-MARKER-{session_id}: a distinct accounting fixture decision"
    )
    return value


def _queue(queue: Path, *responses: str) -> None:
    queue.mkdir(parents=True, exist_ok=True)
    for index, response in enumerate(responses, 1):
        (queue / f"response-{index}.json").write_text(response)


def _run(env: dict, memory_home: Path, session_id: str, transcript: Path):
    enqueued = H.run_worker(
        ["enqueue", "--project", PROJECT, "--session-id", session_id,
         "--transcript", str(transcript), "--source", "manual"], env,
    )
    assert enqueued.returncode == 0, enqueued.stderr
    result = H.run_worker(["run", "--once", "--project", PROJECT], env, timeout=30)
    assert result.returncode == 0, result.stderr


def _session(memory_home: Path, session_id: str) -> dict:
    state = json.loads((memory_home / "projects" / f"{PROJECT}.json").read_text())
    return next(row for row in state["sessions"] if row["session_id"] == session_id)


def _setup(tmp_path, response: str, session_id: str = "selfrun-sess-a"):
    home, memory_home = H.make_home(tmp_path)
    H.write_project_state(memory_home, PROJECT)
    transcript = H.write_transcript(memory_home / "transcripts", session_id)
    logs, queue = tmp_path / "claude-calls", tmp_path / "queue"
    _queue(queue, response)
    return memory_home, transcript, H.worker_env(home, memory_home, claude_log_dir=logs, response_queue=queue), logs


def test_json_envelope_records_reported_cost_tokens_and_preserves_raw(tmp_path):
    envelope = json.dumps({
        "result": json.dumps(_delta("selfrun-sess-a")),
        "usage": {"input_tokens": 1234, "output_tokens": 56, "cost_usd": 0.042},
    })
    memory_home, transcript, env, logs = _setup(tmp_path, envelope)

    _run(env, memory_home, "selfrun-sess-a", transcript)

    extraction = _session(memory_home, "selfrun-sess-a")["extraction"]
    assert extraction["cost_usd"] == 0.042
    assert extraction["tokens_in"] == 1234
    assert extraction["tokens_out"] == 56
    assert extraction["cost_source"] == "reported"
    spend = json.loads((memory_home / "runtime" / "extraction-spend.json").read_text())
    assert spend["entries"][0]["cost_usd"] == 0.042
    assert spend["entries"][0]["tokens_in"] == 1234
    assert spend["entries"][0]["cost_source"] == "reported"
    assert spend["total_usd"] == 0.042
    assert "--output-format\njson" in (logs / "call-1.argv").read_text()
    result = next((memory_home / "runtime" / "extraction-results").glob("**/*.json"))
    assert json.loads(result.read_text())["usage"]["cost_usd"] == 0.042


def test_plain_text_backend_still_merges_as_unknown_cost_control(tmp_path):
    memory_home, transcript, env, logs = _setup(tmp_path, json.dumps(_delta("selfrun-sess-a")))

    _run(env, memory_home, "selfrun-sess-a", transcript)

    extraction = _session(memory_home, "selfrun-sess-a")["extraction"]
    assert extraction["cost_usd"] is None
    assert extraction["cost_source"] == "unknown"
    assert len(list(logs.glob("call-*.argv"))) == 1


def test_usage_without_reported_cost_uses_override_price_table(tmp_path):
    envelope = json.dumps({
        "text": json.dumps(_delta("selfrun-sess-a")),
        "usage": {"input_tokens": 1000, "output_tokens": 2000},
    })
    memory_home, transcript, env, _ = _setup(tmp_path, envelope)
    env["LLM_MEMORY_EXTRACT_COST_TABLE"] = json.dumps({
        "sonnet": {"input_per_million_usd": 1.0, "output_per_million_usd": 2.0},
    })

    _run(env, memory_home, "selfrun-sess-a", transcript)

    extraction = _session(memory_home, "selfrun-sess-a")["extraction"]
    assert extraction["cost_source"] == "estimated"
    assert extraction["cost_usd"] == 0.005
    spend = json.loads((memory_home / "runtime" / "extraction-spend.json").read_text())
    assert spend["entries"][0]["charged_usd"] == 0.005


def test_unknown_cost_reserves_cap_and_blocks_a_retry_without_backend_call(tmp_path):
    memory_home, transcript, env, logs = _setup(tmp_path, json.dumps(_delta("selfrun-sess-a")))
    env["LLM_MEMORY_EXTRACT_SESSION_CAP_USD"] = "0.50"

    _run(env, memory_home, "selfrun-sess-a", transcript)
    _run(env, memory_home, "selfrun-sess-a", transcript)

    assert len(list(logs.glob("call-*.argv"))) == 1
    spend = json.loads((memory_home / "runtime" / "extraction-spend.json").read_text())
    assert spend["total_usd"] == 0.50
    assert spend["entries"][0]["charged_usd"] == 0.50
    state = json.loads((memory_home / "projects" / f"{PROJECT}.extraction-status.json").read_text())
    assert state["state"] == "waiting"


def test_known_cost_over_session_cap_blocks_a_retry_but_lower_cost_control_runs(tmp_path):
    high = json.dumps({"result": json.dumps(_delta("selfrun-sess-a")), "cost_usd": 0.51})
    memory_home, transcript, env, logs = _setup(tmp_path, high)

    _run(env, memory_home, "selfrun-sess-a", transcript)
    _run(env, memory_home, "selfrun-sess-a", transcript)
    assert len(list(logs.glob("call-*.argv"))) == 1

    lower = json.dumps({"result": json.dumps(_delta("selfrun-sess-b")), "cost_usd": 0.49})
    (Path(env["FAKE_CLAUDE_RESPONSE_QUEUE"]) / "response-2.json").write_text(lower)
    transcript_b = H.write_transcript(memory_home / "transcripts", "selfrun-sess-b")
    _run(env, memory_home, "selfrun-sess-b", transcript_b)
    assert len(list(logs.glob("call-*.argv"))) == 2


def test_day_cap_binds_after_unknown_sessions_with_no_extra_backend_call(tmp_path):
    home, memory_home = H.make_home(tmp_path)
    H.write_project_state(memory_home, PROJECT)
    logs, queue = tmp_path / "claude-calls", tmp_path / "queue"
    session_ids = [f"cost-sess-{index}" for index in range(7)]
    _queue(queue, *(json.dumps(_delta(session_id)) for session_id in session_ids))
    env = H.worker_env(home, memory_home, claude_log_dir=logs, response_queue=queue)
    env["LLM_MEMORY_EXTRACT_DAY_CAP_USD"] = "3.00"

    for session_id in session_ids[:6]:
        _run(env, memory_home, session_id, H.write_transcript(memory_home / "transcripts", session_id))
    _run(env, memory_home, session_ids[6], H.write_transcript(memory_home / "transcripts", session_ids[6]))

    assert len(list(logs.glob("call-*.argv"))) == 6
    spend = json.loads((memory_home / "runtime" / "extraction-spend.json").read_text())
    assert spend["total_usd"] == 3.0
    state = json.loads((memory_home / "projects" / f"{PROJECT}.extraction-status.json").read_text())
    assert state["state"] == "waiting"
    assert list((memory_home / "runtime" / "extraction-requests").glob("*.json"))
