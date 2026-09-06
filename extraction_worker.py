#!/usr/bin/env python3
"""Durable, hermetic worker for automatic narrative extraction."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import secrets
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
    if data.get("state") == "idle" and request_files(home, project):
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


def _call_claude(state: dict, transcript: str) -> tuple[str, str]:
    command = os.environ.get("LLM_MEMORY_CLAUDE_CMD", "claude")
    prompt = ("Return one JSON delta only.\nProject active state:\n" +
              json.dumps(state, sort_keys=True) + "\nTranscript: " + transcript)
    command_argv = ["bash", command] if command.endswith(".sh") else [command]
    result = subprocess.run(command_argv + ["-p", "--model", "sonnet"], input=prompt,
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


def _reported_cost(delta: dict) -> float | None:
    """Return a backend-reported USD cost when it is explicit and usable.

    Claude's text mode normally cannot provide a billable amount, so unknown
    is deliberately represented as ``None`` rather than guessed.  Fixtures or
    a future backend may attach a cost either at the delta top level or in a
    usage envelope.
    """
    usage = delta.get("usage") if isinstance(delta.get("usage"), dict) else {}
    values = (delta.get("cost_usd"), usage.get("cost_usd"), usage.get("cost"))
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number >= 0:
            return number
    return None


def _record_spend(home: Path, request_id: str, session_id: str, backend: str,
                  cost_usd: float | None) -> None:
    """Append durable extraction usage without fabricating unknown cost."""
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
    entry = {"request_id": request_id, "session_id": session_id,
             "backend": backend, "cost_usd": cost_usd, "recorded_at": iso()}
    entries.append(entry)
    if cost_usd is not None:
        data["total_usd"] = float(data.get("total_usd", 0.0)) + cost_usd
    data["unknown_cost_count"] = sum(
        1 for item in entries if isinstance(item, dict) and item.get("cost_usd") is None
    )
    atomic_json(path, data)


def _provenance(backend: str, prompt_hash: str, request_id: str, raw: str,
                quarantined: dict, cost_usd: float | None) -> dict:
    finished = iso()
    return {"backend": backend, "model": "sonnet", "prompt_hash": prompt_hash,
            "input_hash": prompt_hash, "attempted_at": finished, "completed_at": finished,
            "duration_s": 0.0, "tokens_in": None, "tokens_out": None, "cost_usd": cost_usd,
            "validator_version": "1", "request_id": request_id,
            "quarantined_revaluations": quarantined}


def _merge(home: Path, project: str, req: dict, rerun: bool) -> tuple[bool, str | None, dict | None]:
    cap = float(os.environ.get("LLM_MEMORY_EXTRACT_DAY_CAP_USD", "3"))
    spend_path = home / "runtime" / "extraction-spend.json"
    if spend_path.exists():
        try:
            spend = json.loads(spend_path.read_text())
            if spend.get("day") == iso()[:10] and float(spend.get("total_usd", 0)) >= cap:
                return False, "day extraction spend cap reached", None
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    state_path = home / "projects" / f"{project}.json"
    if not state_path.exists():
        return False, "project state missing", None
    state = load_full(project, state_path.parent)
    session_cap = float(os.environ.get("LLM_MEMORY_EXTRACT_SESSION_CAP_USD", "0.50"))
    for session in state.get("sessions", []):
        if session.get("session_id") == req["session_id"]:
            prior = session.get("extraction") or {}
            if isinstance(prior, dict) and prior.get("cost_usd") is not None and float(prior["cost_usd"]) >= session_cap:
                return False, "session extraction spend cap reached", None
    raw, prompt_hash = _call_claude(state, req["transcript_path"])
    raw_path = _result_path(home, project, req["session_id"], req["request_id"])
    _preserve_raw(raw_path, raw)
    try:
        delta = json.loads(raw)
        if isinstance(delta, dict):
            if rerun:
                # A rerun refreshes the existing session watermark even when a
                # backend fixture (or stale cache) names the original pass.
                delta["session_id"] = req["session_id"]
            # The watermark represents how far the extractor read, not a
            # model-supplied historical timestamp.  Record this completed
            # attempt so coverage does not immediately reclassify it stale.
            delta["ended"] = iso()
        if not isinstance(delta, dict) or delta.get("session_id") != req["session_id"]:
            raise ValueError("invalid delta session_id")
        applied = copy.deepcopy(delta)
        revals = (applied.get("ledger_delta") or {}).pop("revaluations", []) or []
        probe = copy.deepcopy(state)
        apply_delta(probe, applied, rerun=rerun)
    except Exception as exc:
        return False, str(exc), None
    quarantine = {"count": len(revals), "paths": [str(raw_path)] if revals else []}
    cost_usd = _reported_cost(delta)
    apply_delta(state, applied, rerun=rerun)
    for session in state.get("sessions", []):
        if session.get("session_id") == req["session_id"]:
            provenance = _provenance("claude", prompt_hash, req["request_id"], raw, quarantine, cost_usd)
            if rerun and session.get("extraction"):
                previous = session["extraction"]
                provenance["rerun"] = {"previous": previous}
            session["extraction"] = provenance
            break
    write_full(project, state, state_path.parent)
    _record_spend(home, req["request_id"], req["session_id"], "claude", cost_usd)
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
    else: prune(home)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
