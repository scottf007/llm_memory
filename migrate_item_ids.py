"""Idempotent migration: rewrite integer-suffix item IDs in {project}.json
files to UUID-suffix IDs. Also updates ledger_delta_applied cross-refs on
session records so they keep resolving.

Run once per machine that has pre-UUID project JSONs. Safe to re-run.
"""
import json
import secrets
from pathlib import Path

from tools.memory_config import memory_root

LEDGER_KEYS = ("decisions", "goals", "suggestions", "learnings", "done")
PROJECTS_DIR = memory_root() / "projects"


def migrate_file(path: Path) -> int:
    state = json.loads(path.read_text())
    remap: dict[str, str] = {}
    for kind in LEDGER_KEYS:
        for item in state.get(kind, []) or []:
            old = item.get("id", "")
            if not old or "-" not in old:
                continue
            prefix, _, suffix = old.partition("-")
            # Skip already-migrated IDs (8-char hex suffix).
            if len(suffix) == 8 and all(c in "0123456789abcdef" for c in suffix):
                continue
            new = f"{prefix}-{secrets.token_hex(4)}"
            remap[old] = new
            item["id"] = new

    if not remap:
        return 0

    for s in state.get("sessions", []) or []:
        applied = s.get("ledger_delta_applied") or {}
        introduced = applied.get("introduced") or {}
        for kind, ids in list(introduced.items()):
            introduced[kind] = [remap.get(i, i) for i in ids]
        resolutions = applied.get("resolutions") or {}
        for bucket in ("closed", "archived", "rejected", "contradictions"):
            resolutions[bucket] = [remap.get(i, i) for i in resolutions.get(bucket, [])]

    path.write_text(json.dumps(state, indent=2))
    return len(remap)


def main() -> None:
    for path in sorted(PROJECTS_DIR.glob("*.json")):
        remapped = migrate_file(path)
        if remapped:
            print(f"{path.name}: {remapped} IDs remapped")


if __name__ == "__main__":
    main()
