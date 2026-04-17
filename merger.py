"""
Merger — applies a delta JSON (from the delta-extractor agent) into a
project state JSON, assigning stable IDs and updating ledger statuses.

Usage:
    python merger.py <project_json_path> <delta_json_path>

Mutates the project JSON in place. Idempotent per delta file (won't
re-append if the session_id is already present).
"""

import json
import sys
from pathlib import Path
from datetime import datetime, timezone


LEDGER_KEYS = ("decisions", "goals", "suggestions", "learnings", "done")
ID_PREFIXES = {
    "decisions": "dec",
    "goals": "goal",
    "suggestions": "sug",
    "learnings": "lrn",
    "done": "work",
}


def _next_id(state: dict, kind: str) -> str:
    existing = state.get(kind, [])
    nums = []
    prefix = ID_PREFIXES[kind]
    for item in existing:
        _id = item.get("id", "")
        if _id.startswith(prefix + "-"):
            try:
                nums.append(int(_id.split("-", 1)[1]))
            except ValueError:
                continue
    n = (max(nums) + 1) if nums else 1
    return f"{prefix}-{n:03d}"


def _mark_touched(state: dict, kind: str, item_id: str, session_id: str) -> None:
    for item in state.get(kind, []):
        if item.get("id") == item_id:
            item["last_touched_in"] = session_id
            return


def _archive_item(state: dict, kind: str, item_id: str, session_id: str, reason: str) -> None:
    for item in state.get(kind, []):
        if item.get("id") == item_id:
            item["status"] = "archived"
            item["archived_in"] = session_id
            item["archived_reason"] = reason
            return


def apply_delta(state: dict, delta: dict) -> dict:
    session_id = delta["session_id"]

    # Idempotency — skip if already merged.
    if any(s.get("session_id") == session_id for s in state.get("sessions", [])):
        return state

    # Introduced items — mint new IDs, append.
    introduced = delta.get("ledger_delta", {}).get("introduced", {}) or {}
    intro_ids = {k: [] for k in LEDGER_KEYS}
    for kind in LEDGER_KEYS:
        state.setdefault(kind, [])
        for new_item in introduced.get(kind, []) or []:
            item = dict(new_item)
            item["id"] = _next_id(state, kind)
            item["status"] = "active"
            item["introduced_in"] = session_id
            item["last_touched_in"] = session_id
            item["archived_in"] = None
            item["archived_reason"] = None
            # Initialize cycles_pending on items that can stay open.
            if kind in ("suggestions", "goals"):
                item["cycles_pending"] = 0
            state[kind].append(item)
            intro_ids[kind].append(item["id"])

    # Resolutions — update existing items. Falls back to text-matching
    # against just-introduced items when the agent referenced a synthetic
    # id (e.g., same-session rejection of a newly-introduced suggestion).
    resolutions = delta.get("ledger_delta", {}).get("resolutions", {}) or {}
    res_summary = {"closed": [], "archived": [], "rejected": [], "contradictions": [], "drift": []}

    def _resolve_id(item_id: str, text_hint: str, kinds_to_search: tuple) -> str | None:
        """Return a real item id. If item_id already matches a prefix/number
        pattern and exists, use it. Otherwise text-match against items just
        introduced this session."""
        if not item_id:
            item_id = ""
        # Direct id lookup first.
        for kind in kinds_to_search:
            for item in state.get(kind, []):
                if item.get("id") == item_id:
                    return item_id
        # Fallback: text-match against just-introduced items of these kinds.
        for kind in kinds_to_search:
            for new_id in intro_ids.get(kind, []):
                for item in state.get(kind, []):
                    if item.get("id") == new_id:
                        if text_hint and text_hint.lower() in item.get("text", "").lower():
                            return new_id
                        # Or synthetic id that contains part of the text:
                        if item_id and any(tok in item.get("text", "").lower() for tok in item_id.lower().split("-") if len(tok) > 3):
                            return new_id
        return None

    for closed in resolutions.get("closed", []) or []:
        resolved = _resolve_id(closed.get("id", ""), closed.get("text", ""), ("goals", "suggestions"))
        if resolved:
            for kind in ("goals", "suggestions"):
                for item in state.get(kind, []):
                    if item.get("id") == resolved:
                        item["status"] = "archived"
                        item["archived_in"] = session_id
                        item["archived_reason"] = f"closed: {closed.get('evidence', '')}"
                        res_summary["closed"].append(resolved)

    for archived in resolutions.get("archived", []) or []:
        resolved = _resolve_id(archived.get("id", ""), archived.get("text", ""), LEDGER_KEYS)
        if resolved:
            for kind in LEDGER_KEYS:
                _archive_item(state, kind, resolved, session_id, archived.get("reason", ""))
            res_summary["archived"].append(resolved)

    for rejected in resolutions.get("rejected", []) or []:
        resolved = _resolve_id(rejected.get("id", ""), rejected.get("text", ""), ("suggestions", "goals"))
        if resolved:
            for kind in ("suggestions", "goals"):
                _archive_item(state, kind, resolved, session_id, f"rejected: {rejected.get('reason', '')}")
            res_summary["rejected"].append(resolved)

    for contradiction in resolutions.get("contradictions", []) or []:
        resolved = _resolve_id(contradiction.get("id", ""), contradiction.get("text", ""), LEDGER_KEYS)
        if resolved:
            for kind in LEDGER_KEYS:
                _archive_item(state, kind, resolved, session_id, f"contradicted by new decision: {contradiction.get('by_decision_text', '')}")
            res_summary["contradictions"].append(resolved)

    for drift in resolutions.get("drift", []) or []:
        note = drift.get("note", "") if isinstance(drift, dict) else str(drift)
        res_summary["drift"].append(note)

    # Append session record.
    session_record = {
        "session_id": session_id,
        "started": delta.get("started"),
        "ended": delta.get("ended"),
        "topic": delta.get("topic", ""),
        "jsonl": f"~/.claude/memory/transcripts/{session_id}.jsonl",
        "conversation_md": f"~/.claude/memory/conversations/{session_id}.md",
        "status": "active",
        "closure_status": delta.get("closure_status"),
        "journal": delta.get("journal", ""),
        "resume_excerpt_lines": delta.get("resume_excerpt_lines", 0),
        "ledger_delta_applied": {
            "introduced": intro_ids,
            "resolutions": res_summary,
        },
    }
    state.setdefault("sessions", []).append(session_record)

    # Clear resume_excerpt on older sessions (only newest active session keeps it).
    for s in state["sessions"][:-1]:
        if "resume_excerpt" in s:
            s.pop("resume_excerpt", None)

    # Increment cycles_pending on every active suggestion/goal that survived this
    # session without being introduced or resolved. Items introduced this session
    # stay at 0 (just initialized above); items resolved are no longer active.
    for kind in ("suggestions", "goals"):
        for item in state.get(kind, []):
            if item.get("status") != "active":
                continue
            if item.get("introduced_in") == session_id:
                # Just introduced — leave at 0.
                continue
            item["cycles_pending"] = (item.get("cycles_pending") or 0) + 1

    state["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return state


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: merger.py <project_json_path> <delta_json_path>", file=sys.stderr)
        sys.exit(1)

    project_path = Path(sys.argv[1])
    delta_path = Path(sys.argv[2])

    state = json.loads(project_path.read_text()) if project_path.exists() else {}
    delta = json.loads(delta_path.read_text())

    state = apply_delta(state, delta)
    project_path.write_text(json.dumps(state, indent=2))
    print(f"Merged {delta['session_id']} into {project_path}")


if __name__ == "__main__":
    main()
