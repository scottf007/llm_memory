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


def _provenance(backend: str, prompt_hash: str, request_id: str, raw: str, quarantined: dict) -> dict:
    finished = iso()
    return {"backend": backend, "model": "sonnet", "prompt_hash": prompt_hash,
            "input_hash": prompt_hash, "attempted_at": finished, "completed_at": finished,
            "duration_s": 0.0, "tokens_in": None, "tokens_out": None, "cost_usd": None,
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
    raw, prompt_hash = _call_claude(state, req["transcript_path"])
    raw_path = _result_path(home, project, req["session_id"], req["request_id"])
    _preserve_raw(raw_path, raw)
    try:
        delta = json.loads(raw)
        if rerun and isinstance(delta, dict):
            # A rerun refreshes the existing session watermark even when a
            # backend fixture (or stale cache) names the original pass.
            delta["session_id"] = req["session_id"]
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
    apply_delta(state, applied, rerun=rerun)
    for session in state.get("sessions", []):
        if session.get("session_id") == req["session_id"]:
            provenance = _provenance("claude", prompt_hash, req["request_id"], raw, quarantine)
            if rerun and session.get("extraction"):
                previous = session["extraction"]
                provenance["rerun"] = {"previous": previous}
            session["extraction"] = provenance
            break
    write_full(project, state, state_path.parent)
    return True, None, quarantine


def _process_project(home: Path, project: str) -> None:
    try:
        with project_lock(home, project):
            # Requests are only a wake signal; coverage is authoritative.
            coverage = _coverage(project)
            request_by_sid = {}
            for p in request_files(home, project):
                try:
                    req = json.loads(p.read_text()); request_by_sid.setdefault(req["session_id"], (p, req))
                except (OSError, json.JSONDecodeError, KeyError):
                    continue
            work = [(Path(row["path"]).stem, False) for row in coverage.get("unprocessed_sorted", [])]
            work += [(row["session_id"], True) for row in coverage.get("stale", [])]
            # A fresh SessionEnd request may precede conversation frontmatter,
            # so coverage cannot yet attribute its archived JSONL.  It is still
            # durable work; add it after the authoritative ordered sweep.
            known = {sid for sid, _ in work}
            work += [(sid, False) for sid in sorted(request_by_sid) if sid not in known]
            seen = set()
            for sid, rerun in work:
                if sid in seen: continue
                seen.add(sid)
                pair = request_by_sid.get(sid)
                if pair is None:
                    transcript = next((x["path"] for x in coverage.get("unprocessed_sorted", []) if Path(x["path"]).stem == sid), str(home / "transcripts" / f"{sid}.jsonl"))
                    enqueue(home, project, sid, transcript, "timer")
                    pair = next(( (p, json.loads(p.read_text())) for p in request_files(home, project) if json.loads(p.read_text()).get("session_id") == sid), None)
                if pair is None: continue
                path, req = pair
                save_status(home, project, state="running", last_attempt=iso(), request_ids=[req["request_id"]])
                ok, error, quarantine = _merge(home, project, req, rerun)
                if not ok:
                    req["attempts"] = int(req.get("attempts", 0)) + 1
                    atomic_json(path, req)
                    state = "waiting" if "cap" in (error or "") else ("failed" if req["attempts"] >= 2 else "waiting")
                    save_status(home, project, state=state, error_summary=error, request_ids=[req["request_id"]])
                    continue
                after = _coverage(project)
                remaining = {Path(x).stem for x in after.get("unprocessed", [])} | {x["session_id"] for x in after.get("stale", [])}
                # A successful merge is followed by the authoritative check
                # above.  The request is a wake record, so coalesced copies
                # for that now-merged session may retire together.
                for duplicate in request_files(home, project):
                    try:
                        if json.loads(duplicate.read_text()).get("session_id") == sid:
                            duplicate.unlink(missing_ok=True)
                    except (OSError, json.JSONDecodeError):
                        pass
                    save_status(home, project, state="idle", unprocessed=after.get("unprocessed_count", 0), stale=len(after.get("stale", [])), last_success=iso(), backend="claude", request_ids=[], error_summary=None, quarantined_revaluations=quarantine or {"count": 0, "paths": []})
    except NarrativeLockBusy:
        return


def prune(home: Path) -> None:
    cutoff = now().timestamp() - 30 * 86400
    root = home / "runtime" / "extraction-results"
    for artifact in root.glob("*/*/*.json") if root.exists() else []:
        ack = artifact.with_suffix(artifact.suffix + ".ack")
        if ack.exists() and ack.stat().st_mtime <= cutoff:
            artifact.chmod(stat.S_IWUSR | stat.S_IRUSR)
            artifact.unlink(missing_ok=True); ack.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    e = sub.add_parser("enqueue"); e.add_argument("--project", required=True); e.add_argument("--session-id", required=True); e.add_argument("--transcript", required=True); e.add_argument("--source", required=True, choices=("session_end", "timer", "manual"))
    r = sub.add_parser("run"); r.add_argument("--once", action="store_true"); r.add_argument("--project")
    s = sub.add_parser("status"); s.add_argument("--project", required=True)
    a = sub.add_parser("acknowledge"); a.add_argument("--project", required=True); a.add_argument("--session-id", required=True)
    sub.add_parser("prune")
    args = parser.parse_args(argv); home = memory_root()
    if args.command == "enqueue": enqueue(home, args.project, args.session_id, args.transcript, args.source)
    elif args.command == "status": print(json.dumps(status(home, args.project)))
    elif args.command == "run":
        projects = [args.project] if args.project else sorted({json.loads(p.read_text()).get("project") for p in request_files(home)})
        for project in projects:
            if project: _process_project(home, project)
    elif args.command == "acknowledge":
        for p in (home / "runtime" / "extraction-results" / args.project / args.session_id).glob("*.json"):
            atomic_json(p.with_suffix(p.suffix + ".ack"), {"acknowledged_at": iso()})
    else: prune(home)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
