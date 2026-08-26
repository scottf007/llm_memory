#!/usr/bin/env python3
"""Automated drain of the open cascade-review backlog (§11, dispositions #16, #18).

This is the ONLY route from a fuzzy signal to a terminal archive once §7.1 is
live, which is why it ships in the same step as `lib/cascade.py` rather than
"later": post-step-6 a U2/U3/U4 hit opens a review and stops, so without a
resolver the cascade has an open mouth and no gate.

Two hard rules, both structural rather than advisory:

  * NEVER writes `{project}.json`. It emits a delta and hands it to
    `merger.py --rerun`, so every state change still goes through the one
    audited write path (§8.1, §8.4's atomic write) and shows up in that
    session's `ledger_delta_applied.resolutions`.
  * NEVER invents a session id. The delta is delivered against the most
    recent REAL session already in `state["sessions"]`; a fresh synthetic id
    would mint a phantom Source Transcript row in the narrative.

`tools/cascade_review.py` is the human override and produces the identical
shape by importing the builders below — a second PRODUCER of one consumed
shape, not a second writer.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lib import cascade, certify                       # noqa: E402
from tools.memory_config import memory_root            # noqa: E402
from tools.project_state import load_full               # noqa: E402

MERGER_PY = REPO_ROOT / "merger.py"

# The mandated mapping for anything that is not an unambiguous confirmation
# (§11). "unsure" is never a confirm: cascade is monotone and an archive has no
# recovery path, so ambiguity has to fall on the side that writes nothing.
UNSURE_REASON = "insufficient evidence for whole-claim identity"


# --- the specced core (§11) -------------------------------------------------

def _find(state: dict, kind: str, item_id: str) -> dict | None:
    return next((i for i in state.get(kind, []) if i.get("id") == item_id), None)


def _confirmation_prompt(child: dict, parent: dict, review: dict) -> str:
    """One bounded prompt per open review. Deliberately asks the question the
    review row actually poses, including the C5 partial-scope case, so a
    confirmation is never an answer to a differently-framed question (the N5
    failure mode — see lib/cascade.apply_review_resolutions)."""
    if review.get("proposed_test") == "U1_PARTIAL":
        framing = (
            "A previous pass recorded that this work item implements only PART "
            "of the decision below (a scope=='partial' edge). The question is "
            "whether that scoping was too narrow: does the work item in fact "
            "restate the decision's claim in WHOLE?"
        )
    else:
        framing = (
            "A textual signal suggests the work item below may restate the "
            "archived decision's claim in whole. The signal is fuzzy and is "
            "not by itself sufficient. Judge the claims, not the wording."
        )
    return f"""{framing}

Confirming archives the work item permanently. There is no undo. Rejecting is
also permanent for this exact pair. If the evidence does not clearly establish
that the two state the SAME claim, answer reject.

ARCHIVED DECISION ({parent.get('id')})
  text:            {parent.get('text', '')}
  rationale:       {parent.get('rationale', '')}
  archived_reason: {parent.get('archived_reason', '')}

WORK ITEM ({child.get('id')})
  text:      {child.get('text', '')}
  rationale: {child.get('rationale', '')}

SIGNAL
  test:        {review.get('proposed_test')}
  reason_code: {review.get('reason_code')}

Answer with JSON only: {{"decision": "confirm"|"reject", "reason": "<one sentence>"}}
"""


def resolve_open_reviews(state: dict, extractor_call) -> dict:
    """extractor_call(prompt) -> {"decision": "confirm"|"reject", "reason": str}.

    ONE bounded LLM call per open review; the only LLM-judgment surface in this
    design. A returned decision that is not exactly "confirm" — including
    "unsure" — maps to reject with UNSURE_REASON per §11, never to confirm.

    A RAISED extractor_call, however, is NOT a reject. §11 says "unsure maps to
    reject", and an unsure verdict is a judgment the model actually made; an
    exception is the absence of any judgment (endpoint down, timeout, reply that
    is not JSON). Rejecting on it would be unsafe out of proportion to the
    cause: a rejected pair is permanent (§7.1), nothing can re-resolve a
    non-open review (§7.2), and cascade_review.py cannot un-reject — so one
    unreachable endpoint would permanently destroy the entire open backlog in a
    single pass, with no route back. Such a row is instead LEFT OPEN and
    reported on stderr, so the next drain retries it. Divergence in
    interpretation, not from the text: §11 does not speak to transport failure.

    Returns the delta fragment only. Assembling and delivering it is
    `build_review_delta` / `deliver` below, so this function stays pure and
    testable with a stub extractor.
    """
    reviews = [r for r in state.get("cascade_reviews", []) if r.get("status") == "open"]
    confirm, reject, unanswered = [], [], []
    for r in reviews:
        candidates = r.get("candidate_parents") or []
        if not candidates:
            continue        # malformed row; the resolver is not its repair path
        child = _find(state, "done", r["child"])
        parent = _find(state, "decisions", candidates[0])
        row = {"child": r["child"], "parent": candidates[0]}
        if child is None or parent is None:
            # The pair no longer resolves. Writing nothing is right, and
            # apply_review_resolutions would reject the whole delta over it.
            continue
        try:
            verdict = extractor_call(_confirmation_prompt(child, parent, r))
        except Exception as exc:                        # noqa: BLE001
            unanswered.append((row, exc))
            continue
        decision = verdict.get("decision") if isinstance(verdict, dict) else None
        if decision == "confirm":
            confirm.append({**row, "reason": verdict.get("reason", "")})
        elif decision == "reject":
            # An explicit reject keeps its own reasoning; that is the useful
            # record of why this pair is now permanently blocked.
            reject.append({**row, "reason": verdict.get("reason", "") or UNSURE_REASON})
        else:
            # Anything else the model returned — "unsure" above all — is a
            # reject carrying §11's mandated reason, not the model's wording.
            reject.append({**row, "reason": UNSURE_REASON})
    for row, exc in unanswered:
        print(f"resolve_cascade_reviews: LEFT OPEN {row['child']} <- {row['parent']}: "
              f"the resolver never answered ({type(exc).__name__}: {exc}). "
              f"Not rejected — rejection is permanent, so an unanswered row is "
              f"retried on the next drain.", file=sys.stderr)
    return {"resolutions": {"cascade_confirm": confirm, "cascade_reject": reject}}


# --- delta assembly and delivery, shared with cascade_review.py (§11) -------

def most_recent_real_session(state: dict) -> dict:
    """The last real session in `sessions[]`. Every record there was put there
    by a genuine merge (this tool never appends one), so "most recent real" is
    simply the last — with `agent-` prefixed ids skipped, matching the
    /narrative skill's own main-session filter."""
    sessions = [s for s in state.get("sessions", [])
                if not str(s.get("session_id", "")).startswith("agent-")]
    if not sessions:
        raise SystemExit(
            "resolve_cascade_reviews: no real session in sessions[] to deliver "
            "against. A review delta must ride an existing session id — minting "
            "a synthetic one would create a phantom Source Transcript row.")
    return sessions[-1]


def build_review_delta(state: dict, confirm: list[dict], reject: list[dict]) -> dict:
    """The one consumed shape. `resolutions` sits at the TOP level of the delta,
    which is where §8.1 reads it from (`delta.get("resolutions")`) — not under
    `ledger_delta`, where the older closed/rejected/drift resolutions live."""
    session = most_recent_real_session(state)
    return {
        "session_id": session["session_id"],
        "started": session.get("started"),
        "ended": session.get("ended"),
        "closure_status": session.get("closure_status"),
        "ledger_delta": {"introduced": {}},
        "resolutions": {"cascade_confirm": confirm, "cascade_reject": reject},
    }


def delta_path_for(project_path: Path, state: dict, deltas_dir: Path | None = None) -> Path:
    """`<project>.cascade-review.<parent-set-hash>.delta.json` (§11). The hash
    is the live cascade parent-set fingerprint, so two drains taken against
    different parent pools cannot overwrite each other's audit trail."""
    parents = sorted(certify.parent_set_cascade(state), key=lambda p: p["id"])
    digest = cascade.parent_set_fingerprint(parents).split(":", 1)[1][:12]
    target_dir = deltas_dir or (memory_root() / "deltas")
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / f"{project_path.stem}.cascade-review.{digest}.delta.json"


def deliver(project_path: Path, delta: dict, delta_file: Path) -> int:
    """Write the delta, then `merger.py --rerun`. --rerun is required, not
    optional: the session id is already in `sessions[]` by construction, so
    without it the merger correctly refuses the delta as already-merged."""
    delta_file.write_text(json.dumps(delta, indent=2))
    cmd = [sys.executable, str(MERGER_PY), "--rerun",
           str(project_path), str(delta_file)]
    print("resolve_cascade_reviews: " + " ".join(cmd), file=sys.stderr)
    return subprocess.call(cmd)


# --- extractor backends -----------------------------------------------------

def local_llm_extractor_call(prompt: str, model: str = "27b",
                             endpoint: str = "http://127.0.0.1:8080/v1") -> dict:
    """The always-on local OpenAI-compatible server. No metered spend.

    Any transport, HTTP, JSON or shape failure raises, and `resolve_open_reviews`
    converts a raise into a reject — so a resolver that cannot reach its model
    writes no edges rather than guessing.
    """
    import urllib.request

    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(f"{endpoint}/chat/completions", data=body,
                                headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        payload = json.loads(resp.read())
    text = payload["choices"][0]["message"]["content"].strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"no JSON object in resolver reply: {text[:200]!r}")
    verdict = json.loads(text[start:end + 1])
    if verdict.get("decision") not in ("confirm", "reject"):
        raise ValueError(f"decision not in confirm|reject: {verdict!r}")
    return verdict


def resolve_by_decisions(state: dict, decisions: list[dict]) -> dict:
    """The `--decisions` path, expressed against the same output shape."""
    # Walk the same rows resolve_open_reviews would, but answer from the file.
    # An open review with no matching entry rejects: a row nobody answered must
    # never be read as a confirmation.
    by_pair = {(d.get("child"), d.get("parent")): d for d in decisions}
    confirm, reject = [], []
    for r in state.get("cascade_reviews", []):
        if r.get("status") != "open":
            continue
        candidates = r.get("candidate_parents") or []
        if not candidates:
            continue
        pair = (r["child"], candidates[0])
        row = {"child": pair[0], "parent": pair[1]}
        answer = by_pair.get(pair)
        if answer and answer.get("decision") == "confirm":
            confirm.append({**row, "reason": answer.get("reason", "")})
        else:
            reject.append({**row, "reason": (answer or {}).get("reason") or UNSURE_REASON})
    return {"resolutions": {"cascade_confirm": confirm, "cascade_reject": reject}}


# --- CLI --------------------------------------------------------------------

def open_reviews(state: dict) -> list[dict]:
    return [r for r in state.get("cascade_reviews", []) if r.get("status") == "open"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Drain the open cascade-review backlog into a merger delta.")
    ap.add_argument("project_json", type=Path)
    ap.add_argument("--emit-prompts", action="store_true",
                    help="print the open reviews and their prompts as JSON, then stop")
    ap.add_argument("--decisions", type=Path,
                    help="JSON list of {child,parent,decision,reason} to apply "
                         "instead of calling the local model")
    ap.add_argument("--model", default="27b")
    ap.add_argument("--dry-run", action="store_true",
                    help="build and print the delta; do not write or merge")
    ap.add_argument("--deltas-dir", type=Path, default=None)
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    state = load_full(args.project_json.stem, args.project_json.parent)
    pending = open_reviews(state)

    if args.emit_prompts:
        out = []
        for r in pending:
            candidates = r.get("candidate_parents") or []
            child = _find(state, "done", r.get("child"))
            parent = _find(state, "decisions", candidates[0]) if candidates else None
            if child is None or parent is None:
                continue
            out.append({"child": r["child"], "parent": candidates[0],
                        "proposed_test": r.get("proposed_test"),
                        "reason_code": r.get("reason_code"),
                        "prompt": _confirmation_prompt(child, parent, r)})
        print(json.dumps(out, indent=2))
        return 0

    if not pending:
        print("resolve_cascade_reviews: no open reviews; nothing to resolve.")
        return 0

    if args.decisions:
        fragment = resolve_by_decisions(state, json.loads(args.decisions.read_text()))
    else:
        fragment = resolve_open_reviews(
            state, lambda p: local_llm_extractor_call(p, model=args.model))

    res = fragment["resolutions"]
    if not res["cascade_confirm"] and not res["cascade_reject"]:
        print("resolve_cascade_reviews: no resolvable rows; nothing delivered.")
        return 0

    delta = build_review_delta(state, res["cascade_confirm"], res["cascade_reject"])
    print(f"resolve_cascade_reviews: {len(res['cascade_confirm'])} confirm, "
          f"{len(res['cascade_reject'])} reject, riding session "
          f"{delta['session_id']}")
    if args.dry_run:
        print(json.dumps(delta, indent=2))
        return 0
    return deliver(args.project_json, delta,
                   delta_path_for(args.project_json, state, args.deltas_dir))


if __name__ == "__main__":
    raise SystemExit(main())
