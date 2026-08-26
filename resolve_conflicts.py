"""Resolve Syncthing sync-conflict files under ~/.claude/memory/items/.

Syncthing creates files like `{id}.sync-conflict-20260419-143000-XYZ.json`
when two machines wrote the same per-item file before sync converged.

Resolution rules:
  1. If the conflict file's content is byte-identical to the primary → drop it.
  2. If one side is archived and the other isn't → archived wins.
  3. Otherwise → keep the side with the later `last_touched_at`; append the
     losing side's text/rationale/quote snapshot to a `history[]` list on the
     winning record.

The resolver never loses data — archived-but-still-editable items retain a
history of divergent text edits. Safe to run on schedule (idempotent: files
with no conflicts are untouched).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from tools.memory_config import memory_root

ITEMS_ROOT = memory_root() / "items"
CONFLICT_SUFFIX_RE = re.compile(r"^(?P<stem>.+?)\.sync-conflict-[^.]+\.json$")


def _snapshot(data: dict, source: str) -> dict:
    return {
        "source": source,
        "text": data.get("text", ""),
        "rationale": data.get("rationale", ""),
        "quote": data.get("quote", ""),
        "status": data.get("status"),
        "last_touched_at": data.get("last_touched_at"),
    }


def _resolve_pair(primary: Path, conflict: Path) -> dict | None:
    """Return the resolved payload (or None if primary should stay as-is)."""
    try:
        pri = json.loads(primary.read_text())
        con = json.loads(conflict.read_text())
    except (OSError, json.JSONDecodeError):
        return None

    # Rule 1: identical content → drop conflict.
    if json.dumps(pri, sort_keys=True) == json.dumps(con, sort_keys=True):
        return pri

    pri_archived = pri.get("status") == "archived"
    con_archived = con.get("status") == "archived"

    # Rule 2: archived wins.
    if con_archived and not pri_archived:
        history = pri.get("history", [])
        history.append(_snapshot(pri, source="pre-archive"))
        con["history"] = history
        return con
    if pri_archived and not con_archived:
        history = pri.get("history", [])
        history.append(_snapshot(con, source="rejected-active"))
        pri["history"] = history
        return pri

    # Rule 3: newer last_touched_at wins; loser snapshot appended.
    pri_ts = pri.get("last_touched_at") or ""
    con_ts = con.get("last_touched_at") or ""
    winner, loser, loser_source = (
        (pri, con, "conflict") if pri_ts >= con_ts else (con, pri, "primary")
    )
    history = winner.get("history", [])
    history.append(_snapshot(loser, source=loser_source))
    winner["history"] = history
    return winner


def resolve_all(items_root: Path = ITEMS_ROOT) -> tuple[int, int]:
    """Walk items_root, merge every sync-conflict file into its primary.

    Returns (resolved, scanned) counts.
    """
    if not items_root.exists():
        return 0, 0
    resolved = 0
    scanned = 0
    for conflict in items_root.rglob("*.sync-conflict-*.json"):
        scanned += 1
        m = CONFLICT_SUFFIX_RE.match(conflict.name)
        if not m:
            continue
        primary = conflict.parent / f"{m.group('stem')}.json"
        if not primary.exists():
            # Orphan conflict — promote it to primary.
            conflict.rename(primary)
            resolved += 1
            continue
        merged = _resolve_pair(primary, conflict)
        if merged is None:
            continue
        primary.write_text(json.dumps(merged, indent=2))
        conflict.unlink()
        resolved += 1
    return resolved, scanned


def main() -> None:
    resolved, scanned = resolve_all()
    print(f"Scanned {scanned} conflict file(s); resolved {resolved}.")


if __name__ == "__main__":
    main()
