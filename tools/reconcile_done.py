#!/usr/bin/env python3
"""Prepare and validate a conservative full-population ``done[]`` audit.

The semantic judgement belongs to ``agents/done-reconciler.md``.  This tool
puts hard mechanical rails around it:

* every active done item is included in a stable, fingerprinted census;
* only archived decisions classified by the existing cascade vocabulary are
  eligible parents; and
* the resulting delta may archive active done items only, with reasons that
  ``merger.apply_delta`` will itself classify as ``archive_class=cascade``.

It deliberately does not mutate project state.  After validation, the normal
``merger.py PROJECT DELTA`` command is the sole write path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

try:
    from lib import archive_class
    from tools.project_state import load_full
except ModuleNotFoundError:  # Direct ``python tools/reconcile_done.py`` use.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from lib import archive_class
    from tools.project_state import load_full


SCHEMA_VERSION = 1
_DECISION_FIELDS = (
    "id", "text", "rationale", "status", "archived_in", "archived_reason",
    "archive_class", "introduced_in", "last_touched_in",
)
_DONE_FIELDS = (
    "id", "text", "rationale", "commit", "importance", "status",
    "introduced_in", "last_touched_in", "decision_links",
)
_EMPTY_RESOLUTION_KEYS = (
    "closed", "rejected", "contradictions", "drift", "cascade_confirm",
    "cascade_reject",
)


class ReconciliationError(ValueError):
    """The prepared census or proposed delta violates a safety invariant."""


def _is_archived(item: dict) -> bool:
    return item.get("status") == "archived" or bool(item.get("archived_in"))


def _effective_archive_class(item: dict) -> str:
    return item.get("archive_class") or archive_class.classify_archive_reason(
        item.get("archived_reason")
    )


def _project_item(item: dict, fields: tuple[str, ...]) -> dict:
    return {field: item.get(field) for field in fields if field in item}


def _fingerprint_payload(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def build_reconciliation_input(state: dict, project: str | None = None) -> dict:
    """Return the complete, deterministic judgement input for one ledger.

    ``excluded_archived_decisions`` is intentionally broader than re-grades:
    lifecycle and unclassified parents are excluded too.  Cascade is terminal,
    so uncertainty must resolve to no action.
    """
    decisions = [row for row in state.get("decisions", []) if isinstance(row, dict)]
    active_done = [
        row for row in state.get("done", [])
        if isinstance(row, dict) and row.get("status") == "active"
    ]

    cascade_candidates = []
    excluded = []
    active_decisions = []
    for decision in decisions:
        projected = _project_item(decision, _DECISION_FIELDS)
        if not _is_archived(decision):
            active_decisions.append(projected)
            continue
        projected["archive_class"] = _effective_archive_class(decision)
        if projected["archive_class"] == "cascade":
            cascade_candidates.append(projected)
        else:
            excluded.append(projected)

    core = {
        "schema_version": SCHEMA_VERSION,
        "classifier_version": archive_class.CLASSIFIER_VERSION,
        "project": project or state.get("project"),
        "cascade_candidates": sorted(cascade_candidates, key=lambda row: row.get("id", "")),
        "excluded_archived_decisions": sorted(excluded, key=lambda row: row.get("id", "")),
        "active_decisions": sorted(active_decisions, key=lambda row: row.get("id", "")),
        "active_done": sorted(
            (_project_item(row, _DONE_FIELDS) for row in active_done),
            key=lambda row: row.get("id", ""),
        ),
    }
    return {**core, "input_fingerprint": _fingerprint_payload(core)}


def _require_empty(value: object, label: str) -> None:
    if value not in (None, [], {}):
        raise ReconciliationError(f"{label} must be empty")


def _normalised_contains(haystack: str, needle: str) -> bool:
    normalise = lambda value: " ".join(value.split()).casefold()
    return bool(needle.strip()) and normalise(needle) in normalise(haystack)


def validate_reconciliation_delta(
    state: dict, prepared: dict, delta: dict, project: str | None = None,
) -> dict:
    """Validate a reconciler delta and return a compact audit summary.

    This is deliberately stricter than ``merger.apply_delta``.  The merger is
    tolerant of extractor slop; a terminal population archive must instead
    fail closed on stale input, a non-cascade parent, or incomplete evidence.
    """
    expected = build_reconciliation_input(state, project)
    supplied_core = {key: value for key, value in prepared.items()
                     if key != "input_fingerprint"}
    supplied_fp = prepared.get("input_fingerprint")
    if supplied_fp != _fingerprint_payload(supplied_core):
        raise ReconciliationError("prepared input fingerprint is internally invalid")
    if supplied_fp != expected["input_fingerprint"]:
        raise ReconciliationError("prepared input is stale for the current project state")

    if not isinstance(delta, dict) or not delta.get("session_id"):
        raise ReconciliationError("delta requires a non-empty session_id")
    for key in ("summary_delta", "operations_delta", "resolutions"):
        _require_empty(delta.get(key), key)

    ledger = delta.get("ledger_delta")
    if not isinstance(ledger, dict):
        raise ReconciliationError("delta requires ledger_delta")
    unknown_ledger = set(ledger) - {"introduced", "resolutions"}
    if unknown_ledger:
        raise ReconciliationError(
            f"unsupported ledger_delta keys: {sorted(unknown_ledger)}"
        )
    introduced = ledger.get("introduced") or {}
    if not isinstance(introduced, dict):
        raise ReconciliationError("ledger_delta.introduced must be an object")
    for kind, rows in introduced.items():
        _require_empty(rows, f"ledger_delta.introduced.{kind}")

    resolutions = ledger.get("resolutions")
    if not isinstance(resolutions, dict):
        raise ReconciliationError("delta requires ledger_delta.resolutions")
    unknown_resolutions = set(resolutions) - {"archived", *_EMPTY_RESOLUTION_KEYS}
    if unknown_resolutions:
        raise ReconciliationError(
            f"unsupported resolution keys: {sorted(unknown_resolutions)}"
        )
    for key in _EMPTY_RESOLUTION_KEYS:
        _require_empty(resolutions.get(key), f"ledger_delta.resolutions.{key}")

    report = delta.get("reconciliation")
    if not isinstance(report, dict):
        raise ReconciliationError("delta requires a reconciliation audit block")
    if report.get("input_fingerprint") != supplied_fp:
        raise ReconciliationError("delta does not reference the prepared input fingerprint")
    expected_counts = {
        "active_done": len(prepared["active_done"]),
        "cascade_candidates": len(prepared["cascade_candidates"]),
        "excluded_archived_decisions": len(prepared["excluded_archived_decisions"]),
    }
    if report.get("examined_counts") != expected_counts:
        raise ReconciliationError(
            f"examined_counts must equal the prepared census: {expected_counts}"
        )

    active_done = {row["id"]: row for row in prepared["active_done"]}
    parents = {row["id"]: row for row in prepared["cascade_candidates"]}
    excluded_ids = {row["id"] for row in prepared["excluded_archived_decisions"]}
    archives = resolutions.get("archived") or []
    if not isinstance(archives, list):
        raise ReconciliationError("ledger_delta.resolutions.archived must be a list")

    seen = set()
    for row in archives:
        if not isinstance(row, dict):
            raise ReconciliationError("each archive proposal must be an object")
        item_id = row.get("id")
        parent_id = row.get("parent")
        if item_id not in active_done:
            raise ReconciliationError(f"archive target {item_id!r} is not active done")
        if item_id in seen:
            raise ReconciliationError(f"duplicate archive target {item_id!r}")
        seen.add(item_id)
        if parent_id in excluded_ids:
            raise ReconciliationError(
                f"parent {parent_id!r} is explicitly excluded from cascade"
            )
        if parent_id not in parents:
            raise ReconciliationError(
                f"parent {parent_id!r} is not an eligible cascade decision"
            )

        reason = row.get("reason") or ""
        quote = row.get("parent_reason_quote") or ""
        wrong_belief = row.get("wrong_belief") or ""
        if archive_class.classify_archive_reason(reason) != "cascade":
            raise ReconciliationError(
                f"archive reason for {item_id!r} does not use cascade leading-clause vocabulary"
            )
        if parent_id not in reason:
            raise ReconciliationError(
                f"archive reason for {item_id!r} must name parent {parent_id!r}"
            )
        if not _normalised_contains(parents[parent_id].get("archived_reason") or "", quote):
            raise ReconciliationError(
                f"parent_reason_quote for {item_id!r} is not in the parent's archived_reason"
            )
        if not _normalised_contains(reason, quote):
            raise ReconciliationError(
                f"archive reason for {item_id!r} must preserve parent_reason_quote"
            )
        if not _normalised_contains(reason, wrong_belief):
            raise ReconciliationError(
                f"archive reason for {item_id!r} must preserve wrong_belief"
            )

    return {
        "input_fingerprint": supplied_fp,
        "active_done_examined": len(active_done),
        "cascade_candidates_examined": len(parents),
        "archive_proposals": len(archives),
        "archive_ids": sorted(seen),
    }


def _load_state(project_path: Path) -> tuple[str, dict]:
    project = project_path.stem
    return project, load_full(project, project_path.parent)


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare or validate a full-population done[] reconciliation."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("project_json", type=Path)
    prepare.add_argument("output_json", type=Path)
    validate = subparsers.add_parser("validate")
    validate.add_argument("project_json", type=Path)
    validate.add_argument("input_json", type=Path)
    validate.add_argument("delta_json", type=Path)
    args = parser.parse_args(argv)

    try:
        project, state = _load_state(args.project_json)
        if args.command == "prepare":
            prepared = build_reconciliation_input(state, project)
            _write_json(args.output_json, prepared)
            print(json.dumps({
                "input_fingerprint": prepared["input_fingerprint"],
                "active_done": len(prepared["active_done"]),
                "cascade_candidates": len(prepared["cascade_candidates"]),
                "excluded_archived_decisions": len(
                    prepared["excluded_archived_decisions"]
                ),
            }, sort_keys=True))
            return 0

        prepared = json.loads(args.input_json.read_text(encoding="utf-8"))
        delta = json.loads(args.delta_json.read_text(encoding="utf-8"))
        print(json.dumps(
            validate_reconciliation_delta(state, prepared, delta, project),
            sort_keys=True,
        ))
        return 0
    except (OSError, json.JSONDecodeError, ReconciliationError) as exc:
        print(f"reconcile_done: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
