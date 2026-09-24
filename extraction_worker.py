#!/usr/bin/env python3
"""Durable, hermetic worker for automatic narrative extraction."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from merger import apply_delta
from narrative_lock import NarrativeLockBusy, project_lock
from tools.memory_config import memory_root
from tools.project_state import load_full, write_full


def now() -> datetime:
    value = os.environ.get("LLM_MEMORY_NOW")
    if value:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return datetime.now(timezone.utc)


def iso() -> str:
    return now().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def request_dir(home: Path) -> Path:
    return home / "runtime" / "extraction-requests"


def request_files(home: Path, project: str | None = None) -> list[Path]:
    files = sorted(request_dir(home).glob("*.json")) if request_dir(home).exists() else []
    if project is None:
        return files
    result = []
    for path in files:
        try:
            if json.loads(path.read_text()).get("project") == project:
                result.append(path)
        except (OSError, json.JSONDecodeError):
            continue
    return result


def _blocking_requests(home: Path, project: str) -> list[Path]:
    """Request files that keep *project* from reporting idle.

    A record that cannot be parsed has no attributable project, so it blocks
    every project until an operator repairs or removes it.
    """
    blocking = []
    for path in sorted(request_dir(home).glob("*.json")) if request_dir(home).exists() else []:
        try:
            owner = json.loads(path.read_text()).get("project")
        except (OSError, json.JSONDecodeError, AttributeError):
            owner = None
        if owner is None or owner == project:
            blocking.append(path)
    return blocking


def status_path(home: Path, project: str) -> Path:
    return home / "projects" / f"{project}.extraction-status.json"


def status(home: Path, project: str) -> dict:
    path = status_path(home, project)
    default = {"state": "idle", "unprocessed": 0, "stale": 0,
               "oldest_waiting": None, "last_attempt": None,
               "last_success": None, "backend": None, "request_ids": [],
               "retry_after": None, "error_summary": None,
               "quarantined_revaluations": {"count": 0, "paths": []}}
    if path.exists():
        try:
            default.update(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError):
            pass
    return default


def save_status(home: Path, project: str, **updates) -> dict:
    data = status(home, project)
    data.update(updates)
    # An idle sidecar is a promise that this project has no queued work.  Do
    # not let a partial drain turn that promise into a silent lie.
    # Physical request files, not merely parseable/project-attributable ones,
    # block the all-clear.  A malformed wake record is still unresolved work
    # and must remain visible until an operator repairs or removes it.
    #
    # Only THIS project's requests block it.  The check used to be global, so
    # one stuck request anywhere pinned every project at "waiting".
    if data.get("state") == "idle" and _blocking_requests(home, project):
        data["state"] = "waiting"
        data["error_summary"] = "extraction request(s) remain pending"
    data["write_seq"] = int(data.get("write_seq", 0)) + 1
    data["error_summary"] = (data.get("error_summary") or "")[:500] or None
    atomic_json(status_path(home, project), data)
    return data


def enqueue(home: Path, project: str, session_id: str, transcript: str, source: str) -> None:
    # Uniqueness is per active project/session. Duplicate triggers are benign.
    for path in request_files(home, project):
        try:
            if json.loads(path.read_text()).get("session_id") == session_id:
                return
        except (OSError, json.JSONDecodeError):
            continue
    request_dir(home).mkdir(parents=True, exist_ok=True)
    request_id = secrets.token_hex(8)
    record = {"schema": 1, "request_id": request_id, "project": project,
              "session_id": session_id, "transcript_path": transcript,
              "enqueued_at": iso(), "source": source, "attempts": 0}
    atomic_json(request_dir(home) / f"{iso().replace(':', '')}-{session_id}-{request_id}.json", record)


def _retire_request(path: Path, session_id: str, reason: str) -> None:
    """Delete a request that can never succeed.

    Retirement used to happen ONLY after a successful merge.  A request whose
    session is already merged can never merge again -- the merger refuses it --
    so it was marked deferred, KEPT, and retried on every pass forever.  On
    2026-09-22 the queue held 10,720 requests, one with 141 attempts.

    Note what is deliberately NOT retired here: a session coverage does not
    list.  compute_narrative_coverage cannot attribute a session to a project
    until its conversation.md exists, so "coverage does not list it" means
    "not yet visible", not "settled".  The request-only branch of
    _work_snapshot exists to carry exactly those, and retiring them drops real
    work on the floor.
    """
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return
    print(f"llm_memory: retired extraction request for {session_id} ({reason})")


def _request_orphaned(home: Path, project: str, req: dict) -> bool:
    """An old request whose PROJECT no longer exists.

    An earlier version of this retired on age alone, and a reviewer was right
    to call that a blocker: age is not evidence that work is dead.  A session
    that coverage cannot yet see -- no conversation.md, so no project
    attribution -- is invisible to coverage and will never be re-enqueued,
    because session_end fires once.  Deleting its request on a birthday
    destroys the only pointer to work that was still perfectly doable.  The
    same unconditional check also deleted the spend-cap hold (excluded from
    merged_sids, then caught by expiry anyway) and stale re-merges, whose
    enqueued_at is old by definition.

    So age is necessary but never sufficient. This fires only when the project
    state file itself is gone, which no amount of re-running can fix.
    """
    if (home / "projects" / f"{project}.json").exists():
        return False
    stamp = req.get("enqueued_at")
    if not stamp:
        return False
    try:
        when = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return False
    try:
        max_age_days = float(os.environ.get("LLM_MEMORY_REQUEST_MAX_AGE_DAYS", "30"))
    except ValueError:
        # A malformed env var must not crash the worker at import time.
        max_age_days = 30.0
    return (now() - when).total_seconds() > max_age_days * 86400


def _transcript_for(home: Path, req: dict, session_id: str) -> Path | None:
    """Locate a request's transcript, or None when it is genuinely gone.

    Requests store the ABSOLUTE path recorded at enqueue time. The store root
    moved (~/.claude/memory -> ~/.llm-memory), so every request enqueued before
    that move carries a path that no longer resolves. Treating a dangling path
    as "transcript gone" would unlink the request -- and for a session coverage
    cannot see, that request is the only pointer to the work. Look under the
    current root by session id before concluding anything is missing.
    """
    recorded = req.get("transcript_path") or ""
    # Path("") is Path("."), which exists; an empty field is missing, not a dir.
    if recorded:
        candidate = Path(recorded)
        if candidate.is_file():
            return candidate
    rebased = home / "transcripts" / f"{session_id}.jsonl"
    if rebased.is_file():
        return rebased
    return None


def _merged_session_ids(home: Path, project: str) -> set[str]:
    """Merged sessions whose pending request is safe to retire.

    A merged session is normally unreachable work: the merger refuses it, so
    its request can never succeed.  One merged session is deliberately
    excluded -- one whose extraction reserved the per-session spend cap
    (cost_source unknown, or a recorded cost at or above the cap).  _merge
    blocks a retry for those, and the request left pending IS the record that
    a retry was blocked.  Retiring it would erase that evidence and flip the
    worker's reported state from waiting to idle.  The condition below mirrors
    _merge's own check deliberately; the two must not drift.
    """
    state_path = home / "projects" / f"{project}.json"
    if not state_path.exists():
        return set()
    try:
        state = load_full(project, state_path.parent)
    except (OSError, ValueError, json.JSONDecodeError):
        return set()
    session_cap = float(os.environ.get("LLM_MEMORY_EXTRACT_SESSION_CAP_USD", "0.50"))
    retirable: set[str] = set()
    held: set[str] = set()
    for session in state.get("sessions", []):
        sid = session.get("session_id")
        if not sid:
            continue
        prior = session.get("extraction") or {}
        if isinstance(prior, dict):
            cost = _nonnegative_number(prior.get("cost_usd"))
            if prior.get("cost_source") == "unknown" or (cost is not None and cost >= session_cap):
                # The hold must be STICKY. sessions[] can carry more than one
                # row for a session id, and a single under-cap row alongside a
                # cap-reserved one would otherwise make it retirable and erase
                # the record of the blocked retry.
                held.add(sid)
                continue
        retirable.add(sid)
    return retirable - held


def _coverage(project: str) -> dict:
    # Import only after environment has selected the memory root.
    import server
    server.DB_DIR = memory_root()
    return server.compute_narrative_coverage(project)


def _qualified_local(home: Path) -> bool:
    path = home / "runtime" / "local-qualification.json"
    if not path.exists():
        return False
    try:
        report = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return bool(report.get("qualified") is True or report.get("passes") is True)


def _observed_transcript_bounds(transcript: str) -> tuple[str, str]:
    """Read the transcript bounds before asking a backend to describe it.

    The extractor may echo timestamps, but it cannot be the authority for how
    far the worker actually read.  Keeping this observation outside the model
    response also lets the final coverage check detect a transcript that grew
    while the backend call was in flight.
    """
    first: datetime | None = None
    last: datetime | None = None
    try:
        lines = Path(transcript).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"cannot read transcript bounds: {exc}") from exc
    for line in lines:
        try:
            value = json.loads(line).get("timestamp")
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        stamp = stamp.astimezone(timezone.utc)
        first = stamp if first is None or stamp < first else first
        last = stamp if last is None or stamp > last else last
    if first is None or last is None:
        raise ValueError("transcript has no parseable timestamps")
    return (first.strftime("%Y-%m-%dT%H:%M:%SZ"), last.strftime("%Y-%m-%dT%H:%M:%SZ"))


# Where a user-scoped install puts the CLI.  systemd --user hands a oneshot a
# minimal PATH (/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
# and little else) that deliberately excludes $HOME, so the bare name below
# resolves in an interactive shell and fails under the unit that actually runs
# this worker.  On 2026-09-22 that gap meant last_success had been null since
# setup: every run died on FileNotFoundError('claude') before any request was
# touched.  Searching these explicitly makes the worker independent of who
# exported what.
_CLAUDE_FALLBACK_DIRS = (
    "~/.local/bin",
    "/usr/local/bin",
    "~/.npm-global/bin",
    "~/node_modules/.bin",
)


def _resolve_claude_cmd() -> list[str]:
    """argv prefix for the extraction backend, or RuntimeError naming the search.

    An explicit LLM_MEMORY_CLAUDE_CMD is honoured verbatim -- an operator who
    pins a path (or a test fixture that pins a script) has already answered the
    question and must not be second-guessed by a PATH lookup.
    """
    pinned = os.environ.get("LLM_MEMORY_CLAUDE_CMD")
    command = pinned or "claude"
    if command.endswith(".sh"):
        return ["bash", command]
    if pinned:
        return [pinned]
    found = shutil.which(command)
    if found:
        return [found]
    for raw in _CLAUDE_FALLBACK_DIRS:
        candidate = Path(raw).expanduser() / command
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return [str(candidate)]
    # Name the search, not just the miss.  A bare FileNotFoundError here reads
    # as a missing install; the actual fault is almost always an environment
    # that cannot see an install which is present.
    raise RuntimeError(
        f"extraction backend {command!r} not found on PATH "
        f"({os.environ.get('PATH', '')!r}) or in {list(_CLAUDE_FALLBACK_DIRS)}; "
        "set LLM_MEMORY_CLAUDE_CMD to an absolute path, or add its directory to "
        "the unit's Environment=PATH"
    )


def _call_claude(state: dict, transcript: str, observed_bounds: tuple[str, str]) -> tuple[str, str]:
    prompt = ("Return one JSON delta only.\nProject active state:\n" +
              json.dumps(state, sort_keys=True) + "\nTranscript: " + transcript +
              "\nsession_started_at: " + observed_bounds[0] +
              "\nsession_ended_at: " + observed_bounds[1])
    command_argv = _resolve_claude_cmd()
    result = subprocess.run(command_argv + ["-p", "--model", "sonnet", "--output-format", "json"], input=prompt,
                            text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError((result.stderr or "Claude extraction failed").strip())
    return result.stdout, hashlib.sha256(prompt.encode()).hexdigest()


def _result_path(home: Path, project: str, session_id: str, request_id: str) -> Path:
    return home / "runtime" / "extraction-results" / project / session_id / f"{request_id}.json"


def _preserve_raw(path: Path, raw: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        # A retry retains the first immutable response for review; never
        # rewrite an evidence artifact merely because the same request woke.
        return
    path.write_text(raw)
    path.chmod(0o444)


DEFAULT_COST_TABLE = {
    # USD per one million tokens. These are deliberately local defaults, not
    # a live price lookup; installations can pin replacements with
    # LLM_MEMORY_EXTRACT_COST_TABLE.
    "sonnet": {"input_per_million_usd": 3.0, "output_per_million_usd": 15.0},
    "opus": {"input_per_million_usd": 15.0, "output_per_million_usd": 75.0},
    "haiku": {"input_per_million_usd": 0.8, "output_per_million_usd": 4.0},
}


def _backend_response(raw: str) -> tuple[str, dict]:
    """Return delta text and output metadata from text or Claude JSON output.

    The frozen backend intentionally still emits a delta directly.  Real
    ``claude --output-format json`` emits an envelope whose ``result`` (or
    legacy ``text``) contains the delta; preserve that envelope as raw
    evidence while extracting its usage fields for accounting.
    """
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        return raw, {}
    if not isinstance(envelope, dict):
        return raw, {}
    result = envelope.get("result", envelope.get("text"))
    if isinstance(result, str):
        return result, envelope
    if isinstance(result, dict):
        return json.dumps(result), envelope
    return raw, {}


def _nonnegative_number(*values: object) -> float | None:
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number >= 0:
            return number
    return None


def _usage_values(response: dict, delta: dict) -> tuple[float | None, int | None, int | None]:
    """Read a reported USD cost and token counts from either supported shape."""
    response_usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    delta_usage = delta.get("usage") if isinstance(delta.get("usage"), dict) else {}
    sources = (response, response_usage, delta, delta_usage)

    def find(*names: str) -> float | None:
        return _nonnegative_number(*(source.get(name) for source in sources for name in names))

    cost = find("cost_usd", "total_cost_usd", "cost")
    tokens_in = find("tokens_in", "input_tokens", "prompt_tokens")
    tokens_out = find("tokens_out", "output_tokens", "completion_tokens")
    return cost, int(tokens_in) if tokens_in is not None else None, int(tokens_out) if tokens_out is not None else None


def _cost_table() -> dict | None:
    """Load a deliberately pinned price table, or reject a bad override."""
    override = os.environ.get("LLM_MEMORY_EXTRACT_COST_TABLE")
    if not override:
        return DEFAULT_COST_TABLE
    try:
        value = json.loads(override)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _estimated_cost(tokens_in: int | None, tokens_out: int | None, model: str = "sonnet") -> float | None:
    if tokens_in is None or tokens_out is None:
        return None
    table = _cost_table()
    if table is None:
        return None
    normalized = next((name for name in ("sonnet", "opus", "haiku") if name in model.lower()), model.lower())
    rates = table.get(normalized)
    if not isinstance(rates, dict):
        return None
    input_rate = _nonnegative_number(rates.get("input_per_million_usd"), rates.get("input_usd_per_million"))
    output_rate = _nonnegative_number(rates.get("output_per_million_usd"), rates.get("output_usd_per_million"))
    if input_rate is None or output_rate is None:
        return None
    return (tokens_in * input_rate + tokens_out * output_rate) / 1_000_000


def _cost_details(response: dict, delta: dict) -> tuple[float | None, int | None, int | None, str]:
    """Return billable cost, token counts, and reported/estimated/unknown source."""
    reported, tokens_in, tokens_out = _usage_values(response, delta)
    if reported is not None:
        return reported, tokens_in, tokens_out, "reported"
    estimated = _estimated_cost(tokens_in, tokens_out)
    if estimated is not None:
        return estimated, tokens_in, tokens_out, "estimated"
    return None, tokens_in, tokens_out, "unknown"


def _record_spend(home: Path, request_id: str, session_id: str, backend: str,
                  cost_usd: float | None, tokens_in: int | None,
                  tokens_out: int | None, cost_source: str) -> None:
    """Append durable usage; unknown calls reserve the full session cap."""
    path = home / "runtime" / "extraction-spend.json"
    today = iso()[:10]
    try:
        data = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    if data.get("day") != today:
        data = {"day": today, "total_usd": 0.0, "entries": []}
    entries = data.setdefault("entries", [])
    if any(entry.get("request_id") == request_id for entry in entries if isinstance(entry, dict)):
        return
    session_cap = float(os.environ.get("LLM_MEMORY_EXTRACT_SESSION_CAP_USD", "0.50"))
    charged_usd = cost_usd if cost_usd is not None else session_cap
    entry = {"request_id": request_id, "session_id": session_id,
             "backend": backend, "cost_usd": cost_usd, "tokens_in": tokens_in,
             "tokens_out": tokens_out, "cost_source": cost_source,
             "charged_usd": charged_usd, "recorded_at": iso()}
    entries.append(entry)
    data["total_usd"] = float(data.get("total_usd", 0.0)) + charged_usd
    data["unknown_cost_count"] = sum(
        1 for item in entries if isinstance(item, dict) and item.get("cost_source", "unknown") == "unknown"
    )
    atomic_json(path, data)


def _provenance(backend: str, prompt_hash: str, request_id: str, raw: str,
                quarantined: dict, cost_usd: float | None, tokens_in: int | None,
                tokens_out: int | None, cost_source: str) -> dict:
    finished = iso()
    return {"backend": backend, "model": "sonnet", "prompt_hash": prompt_hash,
            "input_hash": prompt_hash, "attempted_at": finished, "completed_at": finished,
            "duration_s": 0.0, "tokens_in": tokens_in, "tokens_out": tokens_out,
            "cost_usd": cost_usd, "cost_source": cost_source,
            "validator_version": "1", "request_id": request_id,
            "quarantined_revaluations": quarantined}


def _merge(home: Path, project: str, req: dict, rerun: bool) -> tuple[bool, str | None, dict | None]:
    # 3 was below the cost of a single observed extraction (USD 3.29 on
    # 2026-09-21), so the cap could not bind before the first call of the day
    # had already exceeded it.  5 is the owner's figure.
    cap = float(os.environ.get("LLM_MEMORY_EXTRACT_DAY_CAP_USD", "5"))
    spend_path = home / "runtime" / "extraction-spend.json"
    if spend_path.exists():
        try:
            spend = json.loads(spend_path.read_text())
            if spend.get("day") == iso()[:10] and float(spend.get("total_usd", 0)) >= cap:
                return False, "day extraction spend cap reached", None
        except (OSError, ValueError, json.JSONDecodeError):
            # A ledger that cannot be read is not evidence of zero spend.
            # Falling through here spends real money against an unknown running
            # total, which is the one direction this check must never fail in.
            return False, "extraction spend ledger unreadable; refusing to spend", None
    state_path = home / "projects" / f"{project}.json"
    if not state_path.exists():
        return False, "project state missing", None
    state = load_full(project, state_path.parent)
    session_cap = float(os.environ.get("LLM_MEMORY_EXTRACT_SESSION_CAP_USD", "0.50"))
    for session in state.get("sessions", []):
        if session.get("session_id") == req["session_id"]:
            prior = session.get("extraction") or {}
            if isinstance(prior, dict) and (
                prior.get("cost_source") == "unknown" or
                _nonnegative_number(prior.get("cost_usd")) is not None and
                _nonnegative_number(prior.get("cost_usd")) >= session_cap
            ):
                return False, "session extraction spend cap reached", None
    # Both of these were outside the guard below until 2026-09-22, so a
    # backend that could not start (or a transcript with no parseable
    # timestamps) raised straight out of _merge, past _process_project's
    # per-request error handling, and aborted the whole multi-project drain.
    # One unreachable CLI therefore starved every other project's queue
    # instead of recording one project's failure and moving on.
    try:
        observed_bounds = _observed_transcript_bounds(req["transcript_path"])
        raw, prompt_hash = _call_claude(state, req["transcript_path"], observed_bounds)
    except Exception as exc:
        return False, str(exc), None
    raw_path = _result_path(home, project, req["session_id"], req["request_id"])

    # Everything below this line is post-payment: _call_claude has already been
    # billed and no later failure refunds it.  _record_spend used to sit at the
    # very end, reachable only when the delta parsed and applied, so a response
    # that could not be read cost real money and left no ledger entry at all.
    # On SCOTT-PC that gap ran to USD 19.91 across 7 calls against a ledger
    # still reading zero.  The finally below is the guarantee: every exit from
    # here banks the call exactly once.
    banked = False

    def _bank(response: dict, delta: dict) -> None:
        nonlocal banked
        if banked:
            return
        cost_usd, tokens_in, tokens_out, cost_source = _cost_details(response, delta)
        _record_spend(home, req["request_id"], req["session_id"], "claude",
                      cost_usd, tokens_in, tokens_out, cost_source)
        banked = True

    try:
        return _merge_banked(home, project, req, rerun, state, state_path,
                             raw, raw_path, prompt_hash, observed_bounds, _bank)
    finally:
        if not banked:
            # The response was never parsed, so its cost is unknown.
            # _record_spend charges the full session cap for an unknown call,
            # which is the safe direction for one we could not read.
            try:
                _bank({}, {})
            except Exception:
                pass


def _merge_banked(home: Path, project: str, req: dict, rerun: bool, state: dict,
                  state_path: Path, raw: str, raw_path: Path, prompt_hash: str,
                  observed_bounds: tuple[str, str], bank) -> tuple[bool, str | None, dict | None]:
    """The post-payment half of `_merge`, with spend banking made mandatory.

    Split out purely so `_merge`'s `finally` cannot be bypassed by an early
    `return` added here later.  `bank` must be called with the parsed response
    and delta as soon as both are known.
    """
    _preserve_raw(raw_path, raw)
    try:
        delta_text, response = _backend_response(raw)
        delta = json.loads(delta_text)
        if isinstance(delta, dict):
            if rerun:
                # A rerun refreshes the existing session watermark even when a
                # backend fixture (or stale cache) names the original pass.
                delta["session_id"] = req["session_id"]
            # Model timestamp echoes are provenance only.  The applied
            # watermark is what this worker observed before the call began.
            delta["started"], delta["ended"] = observed_bounds
        if not isinstance(delta, dict) or delta.get("session_id") != req["session_id"]:
            raise ValueError("invalid delta session_id")
        applied = copy.deepcopy(delta)
        revals = (applied.get("ledger_delta") or {}).pop("revaluations", []) or []
        probe = copy.deepcopy(state)
        apply_delta(probe, applied, rerun=rerun)
    except Exception as exc:
        return False, str(exc), None
    quarantine = {"count": len(revals), "paths": [str(raw_path)] if revals else []}
    cost_usd, tokens_in, tokens_out, cost_source = _cost_details(response, delta)
    # Bank here, with the real figures, before anything else can fail.
    bank(response, delta)
    apply_delta(state, applied, rerun=rerun)
    for session in state.get("sessions", []):
        if session.get("session_id") == req["session_id"]:
            provenance = _provenance("claude", prompt_hash, req["request_id"], raw, quarantine,
                                     cost_usd, tokens_in, tokens_out, cost_source)
            if rerun and session.get("extraction"):
                previous = session["extraction"]
                provenance["rerun"] = {"previous": previous}
            session["extraction"] = provenance
            break
    write_full(project, state, state_path.parent)
    return True, None, quarantine


def _render_status(home: Path, project: str) -> None:
    """Refresh the owner-visible narrative after a status transition."""
    state_path = home / "projects" / f"{project}.json"
    if not state_path.exists():
        return
    import renderer
    state = json.loads(state_path.read_text())
    md, _ = renderer.render_with_report(state)
    (home / "projects" / f"{project}.narrative.md").write_text(md)


def _work_snapshot(home: Path, project: str) -> tuple[dict, dict[str, tuple[Path, dict]], list[tuple[str, bool]]]:
    coverage = _coverage(project)
    request_by_sid: dict[str, tuple[Path, dict]] = {}
    for path in request_files(home, project):
        try:
            req = json.loads(path.read_text())
            request_by_sid.setdefault(req["session_id"], (path, req))
        except (OSError, json.JSONDecodeError, KeyError):
            continue
    work = [(Path(row["path"]).stem, False) for row in coverage.get("unprocessed_sorted", [])]
    work += [(row["session_id"], True) for row in coverage.get("stale", [])]
    known = {sid for sid, _ in work}
    work += [(sid, False) for sid in sorted(request_by_sid) if sid not in known]
    return coverage, request_by_sid, work


def _pending_counts(coverage: dict) -> tuple[int, int]:
    return int(coverage.get("unprocessed_count", 0)), len(coverage.get("stale", []))


def _process_project(home: Path, project: str) -> bool:
    try:
        with project_lock(home, project) as fd:
            previous_handle = os.environ.get("LLM_MEMORY_NARRATIVE_LOCK")
            os.environ["LLM_MEMORY_NARRATIVE_LOCK"] = f"{project}:{fd}"
            try:
                # Re-snapshot under the same lock.  Requests which arrive
                # during an extractor call are consumed by a following pass.
                handled: set[str] = set()
                latest_quarantine = status(home, project).get(
                    "quarantined_revaluations", {"count": 0, "paths": []}
                )
                last_attempt: str | None = None
                for _pass in range(10):
                    coverage, request_by_sid, work = _work_snapshot(home, project)
                    merged_sids = _merged_session_ids(home, project)
                    deferred = [entry for entry in work if entry[0] in handled and entry[0] in request_by_sid]
                    work = [entry for entry in work if entry[0] not in handled]
                    if not work:
                        if deferred:
                            unprocessed, stale = _pending_counts(coverage)
                            prior_state = status(home, project).get("state")
                            save_status(home, project, state="failed" if prior_state == "failed" else "waiting", unprocessed=unprocessed, stale=stale,
                                        request_ids=[req["request_id"] for _, req in request_by_sid.values()],
                                        error_summary="coverage still reports processed session(s)")
                            _render_status(home, project)
                            return True
                        unprocessed, stale = _pending_counts(coverage)
                        save_status(home, project, state="idle", unprocessed=unprocessed, stale=stale,
                                    last_attempt=last_attempt, last_success=iso(), request_ids=[], error_summary=None,
                                    quarantined_revaluations=latest_quarantine)
                        _render_status(home, project)
                        return True
                    seen = set()
                    for sid, rerun in work:
                        if sid in seen:
                            continue
                        seen.add(sid)
                        pair = request_by_sid.get(sid)
                        if pair is None:
                            transcript = next((x["path"] for x in coverage.get("unprocessed_sorted", []) if Path(x["path"]).stem == sid), str(home / "transcripts" / f"{sid}.jsonl"))
                            enqueue(home, project, sid, transcript, "timer")
                            _, request_by_sid, _ = _work_snapshot(home, project)
                            pair = request_by_sid.get(sid)
                        if pair is None:
                            continue
                        path, req = pair
                        # These must stay BEFORE _merge(): _merge calls the model
                        # before it can discover the work is impossible, so a
                        # later check still pays for the extraction.
                        #
                        # rerun is excluded: a stale session IS in sessions[] and
                        # is exactly the case that must be re-merged.
                        if not rerun and sid in merged_sids:
                            _retire_request(path, sid, "already merged into project state")
                            handled.add(sid)
                            continue
                        located = _transcript_for(home, req, sid)
                        if located is None:
                            _retire_request(path, sid, "transcript not found under the recorded path or the current root")
                            handled.add(sid)
                            continue
                        # Repair the record rather than re-deriving it every pass.
                        if str(located) != req.get("transcript_path"):
                            req["transcript_path"] = str(located)
                            atomic_json(path, req)
                        if _request_orphaned(home, project, req):
                            _retire_request(path, sid, f"project state gone; enqueued {req.get('enqueued_at')}")
                            handled.add(sid)
                            continue
                        last_attempt = iso()
                        ok, error, quarantine = _merge(home, project, req, rerun)
                        if not ok:
                            handled.add(sid)
                            req["attempts"] = int(req.get("attempts", 0)) + 1
                            atomic_json(path, req)
                            state = "waiting" if "cap" in (error or "") else ("failed" if req["attempts"] >= 2 else "waiting")
                            save_status(home, project, state=state, last_attempt=last_attempt,
                                        error_summary=error, request_ids=[req["request_id"]])
                            _render_status(home, project)
                            continue
                        after = _coverage(project)
                        remaining = {Path(x).stem for x in after.get("unprocessed", [])} | {x["session_id"] for x in after.get("stale", [])}
                        unprocessed, stale = _pending_counts(after)
                        if sid in remaining:
                            handled.add(sid)
                            save_status(home, project, state="waiting", unprocessed=unprocessed, stale=stale,
                                        last_attempt=last_attempt, request_ids=[req["request_id"]], error_summary=f"coverage still reports {sid}",
                                        quarantined_revaluations=quarantine or {"count": 0, "paths": []})
                            _render_status(home, project)
                            continue
                        latest_quarantine = quarantine or {"count": 0, "paths": []}
                        for duplicate in request_files(home, project):
                            try:
                                if json.loads(duplicate.read_text()).get("session_id") == sid:
                                    duplicate.unlink(missing_ok=True)
                            except (OSError, json.JSONDecodeError):
                                pass
                        handled.add(sid)
                    # Work may have arrived during this pass; loop and check.
                final = _coverage(project)
                unprocessed, stale = _pending_counts(final)
                save_status(home, project, state="waiting", unprocessed=unprocessed, stale=stale,
                            request_ids=[json.loads(p.read_text()).get("request_id") for p in request_files(home, project)],
                            error_summary="drain pass limit reached; requests remain pending")
                _render_status(home, project)
                return True
            finally:
                if previous_handle is None:
                    os.environ.pop("LLM_MEMORY_NARRATIVE_LOCK", None)
                else:
                    os.environ["LLM_MEMORY_NARRATIVE_LOCK"] = previous_handle
    except NarrativeLockBusy:
        print(f"LLM_MEMORY_WARN: narrative update already running for {project}; retry after it finishes")
        return False


def prune(home: Path) -> None:
    cutoff = now().timestamp() - 30 * 86400
    root = home / "runtime" / "extraction-results"
    for artifact in root.glob("*/*/*.json") if root.exists() else []:
        ack = artifact.with_suffix(artifact.suffix + ".ack")
        if ack.exists() and ack.stat().st_mtime <= cutoff:
            artifact.chmod(stat.S_IWUSR | stat.S_IRUSR)
            artifact.unlink(missing_ok=True); ack.unlink(missing_ok=True)


def refresh_status(home: Path, project: str, *, last_success: str | None = None) -> dict:
    """Rebuild *project*'s status from the ledger and queue, without the worker.

    The status sidecar used to be written only by the worker, so a /narrative
    merge -- or the worker being uninstalled -- left it frozen: projects whose
    ledgers were current kept reporting "failed, never succeeded" forever.
    """
    try:
        unprocessed, stale = _pending_counts(_coverage(project))
    except Exception:
        current = status(home, project)
        unprocessed, stale = int(current.get("unprocessed", 0)), int(current.get("stale", 0))
    remaining = request_files(home, project)
    updates = {"state": "waiting" if remaining or unprocessed or stale else "idle",
               "unprocessed": unprocessed, "stale": stale,
               "request_ids": [json.loads(p.read_text()).get("request_id") for p in remaining],
               "error_summary": None}
    if last_success:
        updates["last_success"] = last_success
    return save_status(home, project, **updates)


def record_merge(home: Path, project: str, session_id: str) -> None:
    """A session was merged outside the worker (e.g. /narrative): retire its
    requests and bring the status sidecar up to date."""
    for path in request_files(home, project):
        try:
            if json.loads(path.read_text()).get("session_id") == session_id:
                _retire_request(path, session_id, "merged by /narrative")
        except (OSError, json.JSONDecodeError):
            continue
    if status_path(home, project).exists():
        refresh_status(home, project, last_success=iso())


def reconcile(home: Path) -> None:
    """One-off repair of every status sidecar against its ledger."""
    for path in sorted((home / "projects").glob("*.extraction-status.json")):
        project = path.name[: -len(".extraction-status.json")]
        merged = _merged_session_ids(home, project)
        for req_path in request_files(home, project):
            try:
                sid = json.loads(req_path.read_text()).get("session_id")
            except (OSError, json.JSONDecodeError):
                continue
            if sid in merged:
                _retire_request(req_path, sid, "already merged into project state")
        last_success = status(home, project).get("last_success")
        if not last_success:
            try:
                last_success = json.loads((home / "projects" / f"{project}.json").read_text()).get("last_rebuilt_at")
            except (OSError, json.JSONDecodeError):
                last_success = None
        data = refresh_status(home, project, last_success=last_success)
        print(f"{project}: {data['state']} (unprocessed={data['unprocessed']}, stale={data['stale']}, "
              f"queued={len(data['request_ids'])}, last_success={data.get('last_success')})")


def mark_failed(home: Path, message: str) -> None:
    """Surface a unit-level failure for every project that still has work."""
    projects: set[str] = set()
    for path in request_files(home):
        try:
            project = json.loads(path.read_text()).get("project")
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(project, str):
            projects.add(project)
    for project in sorted(projects):
        try:
            coverage = _coverage(project)
            unprocessed, stale = _pending_counts(coverage)
        except Exception:
            unprocessed, stale = 0, 0
        save_status(home, project, state="failed", unprocessed=unprocessed, stale=stale,
                    last_attempt=iso(),
                    request_ids=[json.loads(path.read_text()).get("request_id")
                                 for path in request_files(home, project)],
                    error_summary=message)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    e = sub.add_parser("enqueue"); e.add_argument("--project", required=True); e.add_argument("--session-id", required=True); e.add_argument("--transcript", required=True); e.add_argument("--source", required=True, choices=("session_end", "timer", "manual"))
    r = sub.add_parser("run"); r.add_argument("--once", action="store_true"); r.add_argument("--project")
    s = sub.add_parser("status"); s.add_argument("--project", required=True)
    a = sub.add_parser("acknowledge"); a.add_argument("--project", required=True); a.add_argument("--session-id", required=True)
    f = sub.add_parser("mark-failed"); f.add_argument("--message", default="systemd extraction worker failed")
    sub.add_parser("prune")
    sub.add_parser("reconcile")
    args = parser.parse_args(argv); home = memory_root()
    if args.command == "enqueue": enqueue(home, args.project, args.session_id, args.transcript, args.source)
    elif args.command == "status": print(json.dumps(status(home, args.project)))
    elif args.command == "run":
        projects = [args.project] if args.project else sorted({json.loads(p.read_text()).get("project") for p in request_files(home)})
        ok = True
        for project in projects:
            if project:
                ok = _process_project(home, project) and ok
        return 0 if ok else 3
    elif args.command == "acknowledge":
        for p in (home / "runtime" / "extraction-results" / args.project / args.session_id).glob("*.json"):
            atomic_json(p.with_suffix(p.suffix + ".ack"), {"acknowledged_at": iso()})
    elif args.command == "mark-failed":
        mark_failed(home, args.message)
    elif args.command == "reconcile":
        reconcile(home)
    else: prune(home)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
