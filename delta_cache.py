"""Delta cache: decide when a cached delta is good enough vs needing re-extract.

The /narrative skill ran the delta-extractor agent once per unprocessed
session, overwriting any existing delta file each time. That wastes an LLM
call when the extractor prompt hasn't changed. Now the skill calls `check`
before spawning the agent — a cache hit skips straight to merge.

Cache policy:
  - Current extractor_hash (sha256[:12] of ~/.claude/agents/delta-extractor.md)
    is computed at call time.
  - If the delta file carries that same hash -> always reuse.
  - If the delta carries a different hash (or 'legacy-pre-stamping' from the
    backfill) -> decide by decay: p_reextract = exp(-age_days / half_life).
    Deterministic: RNG seeded by sha256(session_id + cached_hash), so the
    same session reaches the same verdict across runs.
  - If no delta file exists -> must extract.

Half-life default = 14d. Recent sessions almost always refresh when the
prompt improves; anything older than ~2 months reuses the cache almost
deterministically.

Usage:
  python3 delta_cache.py hash
  python3 delta_cache.py check <session_id> <session_started_iso>
  python3 delta_cache.py stamp  <delta_path>
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

from tools.memory_config import memory_root

AGENT_PATH = Path.home() / ".claude" / "agents" / "delta-extractor.md"
DELTAS_DIR = memory_root() / "deltas"
DEFAULT_HALF_LIFE_DAYS = 14.0
LEGACY_HASH = "legacy-pre-stamping"


def current_extractor_hash() -> str:
    """sha256[:12] of the installed delta-extractor agent file."""
    if not AGENT_PATH.exists():
        return "unknown"
    return hashlib.sha256(AGENT_PATH.read_bytes()).hexdigest()[:12]


def _age_days(iso_ts: str) -> float:
    t = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - t).total_seconds() / 86400.0


def _decay_seed(session_id: str, cached_hash: str) -> int:
    raw = f"{session_id}|{cached_hash}".encode()
    return int(hashlib.sha256(raw).hexdigest(), 16) % (2**32)


def should_reextract(
    session_id: str,
    session_started_at: str,
    current_hash: str | None = None,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
) -> tuple[bool, str]:
    """Return (reextract?, reason)."""
    delta_path = DELTAS_DIR / f"{session_id}.delta.json"
    if not delta_path.exists():
        return True, "no cached delta"

    try:
        cached = json.loads(delta_path.read_text())
    except (OSError, json.JSONDecodeError):
        return True, "cached delta unreadable"

    cached_hash = cached.get("extractor_hash") or LEGACY_HASH
    cur = current_hash or current_extractor_hash()
    if cached_hash == cur:
        return False, f"cache hit ({cur})"

    age = _age_days(session_started_at)
    p = math.exp(-age / half_life_days)
    rng = random.Random(_decay_seed(session_id, cached_hash))
    roll = rng.random()
    if roll < p:
        return True, (
            f"hash mismatch (cached={cached_hash} current={cur}), "
            f"age={age:.1f}d, p={p:.3f}, roll={roll:.3f} -> re-extract"
        )
    return False, (
        f"hash mismatch (cached={cached_hash} current={cur}), "
        f"age={age:.1f}d, p={p:.3f}, roll={roll:.3f} -> keep cache"
    )


def stamp_delta(delta_path: Path, current_hash: str | None = None) -> None:
    """Write extractor_hash into an existing delta JSON."""
    cur = current_hash or current_extractor_hash()
    doc = json.loads(delta_path.read_text())
    doc["extractor_hash"] = cur
    delta_path.write_text(json.dumps(doc, indent=2))


def _cli() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == "hash":
        print(current_extractor_hash())
        return 0
    if cmd == "check":
        if len(sys.argv) != 4:
            print("usage: check <session_id> <session_started_iso>", file=sys.stderr)
            return 2
        reextract, reason = should_reextract(sys.argv[2], sys.argv[3])
        print("reextract" if reextract else "use_cache")
        print(reason, file=sys.stderr)
        return 0
    if cmd == "stamp":
        if len(sys.argv) != 3:
            print("usage: stamp <delta_path>", file=sys.stderr)
            return 2
        stamp_delta(Path(sys.argv[2]))
        return 0
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_cli())
