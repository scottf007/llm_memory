"""Shared live-ledger loader for certification/cascade tests.

Several §14 test rows in SPEC-rev2-certification-cascade.md assert facts
measured directly against the live ~/.claude/memory/projects/llm_memory.json
(the 33/37/3 archive-class split, the U3/U4 fixture pairs, the U1/U2
citation pairs) rather than a synthetic fixture — the spec pins this file's
sha256 as of 2026-08-24 and every design/spec/judge round re-verified
against it independently. Asserting the hash here turns a silent live-data
drift into one loud, explained failure instead of a handful of mysterious
numeric mismatches scattered across files.
"""
import hashlib
import json
from pathlib import Path

import pytest

LIVE_LEDGER_PATH = Path.home() / ".claude" / "memory" / "projects" / "llm_memory.json"
LIVE_LEDGER_SHA256 = "f3d6e0b80f4cf61b30b566b3b54f8db3dc134a84fb117164027a3b268e66ac00"

LIVE_LEDGER_ABSENT = (
    "pinned live ledger missing: {path} "
    "(owner personal memory ledger; never committed as fixtures). "
    "This test runs in full when the file is present."
)


def load_live_state() -> dict:
    if not LIVE_LEDGER_PATH.is_file():
        pytest.skip(LIVE_LEDGER_ABSENT.format(path=LIVE_LEDGER_PATH))
    raw = LIVE_LEDGER_PATH.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    assert actual == LIVE_LEDGER_SHA256, (
        f"live ledger has drifted since the spec's pinned evidence pack "
        f"(expected {LIVE_LEDGER_SHA256}, got {actual}) -- the live-corpus "
        f"facts asserted against it (33/37/3 split, U3/U4 fixture pairs, "
        f"U1/U2 citation pairs) need independent re-verification before "
        f"this suite can be trusted again, not a blind re-pin."
    )
    return json.loads(raw)


def find(state: dict, kind: str, item_id: str) -> dict:
    return next(i for i in state.get(kind, []) if i.get("id") == item_id)
