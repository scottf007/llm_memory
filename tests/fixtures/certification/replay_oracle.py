"""Replay-oracle construction (spec §13, disposition #22/C7).

Builds `oracle = merger.apply_delta(copy.deepcopy(snapshot), audit_delta)`
from the two pinned, hash-verified source files, and the exact
`pair_invariant` object §13 defines for hashing. `apply_delta` stamps
`last_updated` with wall-clock, so the whole resulting file is never
hashed — only this narrow, stable pair invariant is.
"""
import copy
import hashlib
import json
from pathlib import Path

import pytest

SNAPSHOT_PATH = (Path.home() / ".claude" / "memory" / "snapshots-pm-2026-08-24"
                  / "llm_memory.json.before")
SNAPSHOT_SHA256 = "57510144a88704003d229ebd6bec3822cca51d49755cf62d2bc06598acfc03b3"

DELTA_PATH = Path.home() / ".claude" / "memory" / "deltas" / "llm_memory.audit.delta.json"
DELTA_SHA256 = "4b6c344372d9e7deab155de9c8afc2a11214914b0ff72c80a021b47ff6954d7b"

REPLAY_SOURCE_ABSENT = (
    "pinned replay-oracle source missing: {path} "
    "(owner personal snapshot/delta; never committed as fixtures). "
    "This test runs in full when the file is present."
)

AUDIT_DELTA_SESSION_ID = "audit-20260824-llm_memory"

PAIR_INVARIANT_SHA256 = "994d597de7ecc34e79c08ca42a49e6032772723cd9fceb2271db93e6293febd2"


def _verify(path: Path, expected: str) -> bytes:
    if not path.is_file():
        pytest.skip(REPLAY_SOURCE_ABSENT.format(path=path))
    raw = path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    assert actual == expected, (
        f"{path} has drifted from the spec's pinned evidence pack "
        f"(expected {expected}, got {actual})"
    )
    return raw


def load_snapshot() -> dict:
    return json.loads(_verify(SNAPSHOT_PATH, SNAPSHOT_SHA256))


def load_audit_delta() -> dict:
    return json.loads(_verify(DELTA_PATH, DELTA_SHA256))


def build_oracle(merger_module) -> dict:
    snapshot = load_snapshot()
    delta = load_audit_delta()
    return merger_module.apply_delta(copy.deepcopy(snapshot), delta)


def pair_invariant(oracle: dict) -> dict:
    parent = next(d for d in oracle["decisions"] if d["id"] == "dec-7bf964c0")
    child = next(w for w in oracle["done"] if w["id"] == "work-363365bf")
    return {
        "parent_status": parent["status"], "parent_archived_in": parent["archived_in"],
        "parent_reason_prefix": (parent["archived_reason"] or "")[:9],
        "parent_reason_contains": "load_bearing items always render in full" in (parent["archived_reason"] or ""),
        "child_status": child["status"], "child_archived_reason": child["archived_reason"],
        "child_text_contains": "load_bearing items always render in full" in (child["text"] or ""),
    }


def pair_invariant_hash(oracle: dict) -> str:
    return hashlib.sha256(
        json.dumps(pair_invariant(oracle), sort_keys=True).encode()
    ).hexdigest()
