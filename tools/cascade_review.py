#!/usr/bin/env python3
"""Human override for one cascade review (§11, dispositions #16, #18).

Usage:
  cascade_review.py confirm|reject <project.json> --child ID --parent ID [--reason TEXT]
  cascade_review.py list <project.json>

A second PRODUCER of the one consumed shape, not a second writer: the delta is
assembled and delivered by the same `build_review_delta` / `delta_path_for` /
`deliver` helpers `resolve_cascade_reviews.py` uses, so the two paths cannot
drift apart. Raw JSON edits of `cascade_reviews` are not a supported interface —
they bypass §7.2's fingerprint staleness check, which is the only thing standing
between a moved claim and a terminal archive.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.resolve_cascade_reviews import (          # noqa: E402
    build_review_delta, deliver, delta_path_for, open_reviews,
)


def _print_open(state: dict) -> int:
    pending = open_reviews(state)
    if not pending:
        print("no open cascade reviews.")
        return 0
    print(f"{len(pending)} open cascade review(s):")
    for r in pending:
        candidates = r.get("candidate_parents") or ["?"]
        print(f"  {r.get('child')} <- {candidates[0]}  "
              f"{r.get('proposed_test')} ({r.get('reason_code')})  "
              f"first_seen_render={r.get('first_seen_render')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Resolve one cascade review by hand.")
    ap.add_argument("decision", choices=("confirm", "reject", "list"))
    ap.add_argument("project_json", type=Path)
    ap.add_argument("--child")
    ap.add_argument("--parent")
    ap.add_argument("--reason", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--deltas-dir", type=Path, default=None)
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    state = json.loads(args.project_json.read_text())
    if args.decision == "list":
        return _print_open(state)

    if not args.child or not args.parent:
        ap.error("--child and --parent are required for confirm/reject")

    row = {"child": args.child, "parent": args.parent, "reason": args.reason}
    confirm = [row] if args.decision == "confirm" else []
    reject = [row] if args.decision == "reject" else []

    # Fail here rather than let merger.py raise mid-delta: apply_review_
    # resolutions rejects the whole delta for an unknown or non-open review,
    # and a typo'd id is much easier to read about now than in a traceback.
    pair = (args.child, args.parent)
    if pair not in {(r.get("child"), (r.get("candidate_parents") or [None])[0])
                    for r in open_reviews(state)}:
        print(f"cascade_review: {pair} is not an OPEN review in "
              f"{args.project_json}. Run `cascade_review.py list "
              f"{args.project_json}` to see what is open. A confirmed, "
              f"rejected or invalidated row cannot be re-resolved (§7.2).",
              file=sys.stderr)
        return 1

    delta = build_review_delta(state, confirm, reject)
    print(f"cascade_review: {args.decision} {args.child} <- {args.parent}, "
          f"riding session {delta['session_id']}")
    if args.dry_run:
        print(json.dumps(delta, indent=2))
        return 0
    return deliver(args.project_json, delta,
                   delta_path_for(args.project_json, state, args.deltas_dir))


if __name__ == "__main__":
    raise SystemExit(main())
