"""The gated archive action (M3). Spec: SPEC-rev2-certification-cascade.md §7.

Cascade is the terminal, monotone counterpart to certify's reversible
quarantine (§0). It mints a `decision_links` edge and archives an active
`done` child, but only on U1-exact evidence or an explicit human/automated
confirmation (§7.2) — never on a fuzzy U2/U3/U4 signal alone (disposition C1).

Action order is load-bearing (disposition #21, extended per C5) and is the
whole point of this module:

    1. an existing scope=="whole" edge      -> archive, matcher never called
    2. an existing scope=="partial" edge    -> open a review, never fall
                                               through to the prose U1 scan
    3. U1 exact                             -> mint the edge, then archive
    4. U2/U3/U4 fuzzy                       -> open a review only

Both loops iterate in sorted id order (disposition #27) so two cascade-class
parents citing the same child resolve deterministically to the lower id. A
rejected pair is permanent; an invalidated pair may be freshly re-proposed
(disposition C4).
"""
from __future__ import annotations

import hashlib
import json

import merger

from lib import archive_class
from lib import certify
from lib import claim_match


# --- fingerprints and ordinals (§7.1) ---------------------------------------

def item_fingerprint(item: dict) -> str:
    """Covers the child's id and its normalized claim surface only.

    It deliberately does NOT cover `decision_links` — see the N5 note on
    `_write_decision_link`, which is where that blind spot is compensated for.
    """
    return "sha256:" + hashlib.sha256(
        (item.get("id", "") + "\x00" + claim_match.normalize_text(item.get("text"))
         + "\x00" + claim_match.normalize_text(item.get("rationale"))).encode()
    ).hexdigest()


def _effective_class(item: dict) -> str:
    """Compute-if-absent (disposition #28), identical in rule to
    `certify._effective_class`: an explicit `archive_class` always wins, a
    missing one is derived from the reason text. Read-only."""
    return item.get("archive_class") or archive_class.classify_archive_reason(
        item.get("archived_reason"))


def parent_set_fingerprint(parents: list[dict]) -> str:
    """DEVIATION FROM THE §7.1 LITERAL, deliberate — raised as an objection.

    §7.1 shows the canonical tuple sourcing the class as `p.get("archive_class")`,
    i.e. the raw stored field. That makes the fingerprint change when M1's
    backfill stamps a class that was previously absent — and `archive_class.
    backfill` runs on every merge, immediately before this module (§8.1). On the
    first merge after this slice ships, backfill stamps all 397 archived rows,
    so under the literal reading every open review in the ledger would
    simultaneously invalidate against a parent pool that is semantically
    unchanged. An idempotent normalization must not invalidate reviews.

    So the class is sourced through `_effective_class` — the same compute-if-
    absent rule §6.3/disposition #28 already applies everywhere else a class is
    read. Pre-backfill and post-backfill parent sets fingerprint identically,
    and every genuine change §7.2 cares about (a new cascade-class decision
    appearing, a named parent's own text/reason/status changing, an explicit
    class override) still moves the hash.
    """
    canon = sorted(
        (p["id"], p.get("status"), _effective_class(p),
         p.get("text"), p.get("rationale"), p.get("archived_reason"))
        for p in parents
    )
    return "sha256:" + hashlib.sha256(json.dumps(canon, sort_keys=True).encode()).hexdigest()


def render_ordinal(state: dict) -> int:
    sessions = state.get("sessions", [])
    return len(sessions) + sum(len(s.get("reruns", [])) for s in sessions)


# --- edge writing (§7.1, disposition C5-a, N5) -------------------------------

def _link_for(child: dict, parent_id: str, scope: str) -> dict | None:
    for link in child.get("decision_links") or []:
        if (link.get("decision_id") == parent_id
                and link.get("relation") == "implements_current_claim"
                and link.get("scope") == scope):
            return link
    return None


def _write_decision_link(child: dict, parent: dict, evidence_source: str,
                         session_id: str, proposed_test: str | None = None) -> None:
    """CORRECTED, disposition C5-a (VERDICT-rev1 §5.3). The prior draft's
    idempotency guard was keyed on decision id alone, so a confirmed
    U1_PARTIAL review — which by definition already has a scope=="partial"
    entry for this exact parent — matched that guard and returned with NO
    scope=="whole" edge ever written. The child then never archived and
    cascade.apply re-proposed a fresh open review every pass — unbounded
    growth, which also reset the C6 backlog alarm's clock forever. Fixed: a
    confirmed review against an existing PARTIAL edge for this parent is a
    promotion, updated in place, not a duplicate no-op.

    N5 RESOLVED — the promotion guard also requires proposed_test ==
    "U1_PARTIAL" (VERDICT-rev2 §6). Rationale in the module-level decision
    note in `apply_review_resolutions`.
    """
    links = child.setdefault("decision_links", [])
    # F-2: match on relation as well as decision_id. Every other reader in
    # the system (`_link_for`, `claim_match.u1_id_link`, inbox_merge's
    # validator) qualifies on relation; this one did not. A link with any
    # other relation to the same parent would satisfy the idempotency guard
    # and return WITHOUT writing the edge, while `_archive_cascaded_child`
    # archived the child anyway — an archive with no edge recording it.
    # Unreachable today only because the guard that makes it so lives in a
    # different file from the code depending on it.
    existing = next((l for l in links
                     if l.get("decision_id") == parent["id"]
                     and l.get("relation") == "implements_current_claim"), None)
    if existing is not None:
        if (evidence_source == "review_confirmed"
                and existing.get("scope") == "partial"
                and proposed_test == "U1_PARTIAL"):
            existing["scope"] = "whole"
            existing["evidence_source"] = "review_confirmed"
            existing["written_in"] = session_id
            # Copied verbatim from the review row (disposition #20) — never
            # derived by splitting reason_code text.
            existing["proposed_test"] = proposed_test
        return
    entry = {
        "decision_id": parent["id"], "relation": "implements_current_claim",
        "scope": "whole", "evidence_source": evidence_source, "written_in": session_id,
    }
    if evidence_source == "review_confirmed":
        entry["proposed_test"] = proposed_test
    links.append(entry)


def _archive_cascaded_child(state: dict, child: dict, parent: dict,
                            session_id: str, ts: str, evidence_source: str) -> None:
    """ORDER IS LOAD-BEARING (VERDICT-rev2 item 15). `merger._archive_item`
    recomputes `archive_class` from the reason text unconditionally, and the
    leading-clause classifier cannot produce "cascade" by contract (§4) — it
    reads a generated "cascade from ..." reason as `unclassified`. The explicit
    copy MUST therefore follow the archive call. Swapping these two lines
    silently reverts every cascaded child to unclassified.
    """
    reason = f"cascade from archived decision {parent['id']} via {evidence_source}"
    merger._archive_item(state, "done", child["id"], session_id, ts, reason)
    child["archive_class"] = "cascade"      # explicit override, disposition #4


# --- the pass (§7.1) --------------------------------------------------------

def _pair_of(review: dict) -> tuple[str, str] | None:
    candidates = review.get("candidate_parents") or []
    if not review.get("child") or not candidates:
        return None
    return (review["child"], candidates[0])


def _pairs_with_status(reviews: list[dict], status: str) -> set[tuple[str, str]]:
    out = set()
    for r in reviews:
        if r.get("status") != status:
            continue
        pair = _pair_of(r)
        if pair is not None:
            out.add(pair)
    return out


def apply(state: dict, session_id: str, ts: str) -> dict:
    """Mutates state in place, matching apply_delta's existing style. Runs
    once; a child archived this pass is not revisited as a parent in the same
    call (no recursion). Children: active `done` only. Parents: archive_class
    == 'cascade' only — open question 7's certification extension (which also
    admits `unclassified` parents) does not reach here.
    """
    parents = sorted(certify.parent_set_cascade(state), key=lambda p: p["id"])
    children = [d for d in state.get("done", []) if d.get("status") == "active"]
    reviews = state.setdefault("cascade_reviews", [])
    rejected_pairs = _pairs_with_status(reviews, "rejected")
    open_pairs = _pairs_with_status(reviews, "open")

    parent_fp = parent_set_fingerprint(parents)
    next_ordinal = render_ordinal(state) + 1

    archived, proposed, rejected = [], [], []

    def _open_review(child, parent, reason_code, proposed_test):
        reviews.append({
            "child": child["id"], "candidate_parents": [parent["id"]],
            "reason_code": reason_code, "proposed_test": proposed_test,
            "status": "open", "first_seen_render": next_ordinal,
            "item_fingerprint": item_fingerprint(child),
            "parent_set_fingerprint": parent_fp,
            "resolved_in": None, "resolution_reason": None,
        })
        proposed.append({"child": child["id"], "parent": parent["id"],
                         "test": proposed_test})
        open_pairs.add((child["id"], parent["id"]))

    for child in sorted(children, key=lambda c: c["id"]):
        if child.get("status") != "active":
            continue        # a same-pass cascade may have already archived it
        for parent in parents:                  # sorted — determinism, #27
            pair = (child["id"], parent["id"])
            if pair in rejected_pairs:
                continue                        # permanent, checked before any matcher
            if child["id"] == parent["id"]:
                rejected.append({"child": child["id"], "parent": parent["id"],
                                 "reason_code": "cycle_or_self"})
                continue

            # 1. Existing whole edge (extractor / id_link / review_confirmed),
            #    checked BEFORE the matcher — disposition #21, B's blocking
            #    defect #1. A recorded whole edge is already an authoritative
            #    determination; re-deriving it is both wasted work and a chance
            #    to disagree with the record.
            existing_whole = _link_for(child, parent["id"], "whole")
            if existing_whole is not None:
                # §7.1 defaults this to "extractor" for the archive reason but
                # reports the raw value in the result row, so an edge with no
                # evidence_source archives as "via extractor" while the audit
                # row says None. Resolved once, used for both.
                evidence_source = existing_whole.get("evidence_source") or "extractor"
                _archive_cascaded_child(state, child, parent, session_id, ts,
                                        evidence_source)
                archived.append({"child": child["id"], "parent": parent["id"],
                                 "evidence_source": evidence_source})
                break       # this child is archived; stop trying other parents

            # 2. Existing partial edge (disposition C5). An explicit,
            #    authoritative determination that this claim is NOT a whole
            #    restatement. It must never fall through to step 3's prose
            #    scan, which would re-mint an exact match from the very
            #    citation text the partial edge already scoped down. Route it
            #    to the one confirm/reject mechanism (§7.2) instead, never
            #    silently.
            if _link_for(child, parent["id"], "partial") is not None:
                if pair in open_pairs:
                    continue
                _open_review(child, parent, "partial_scope_edge", "U1_PARTIAL")
                continue        # never reaches claim_match for this pair

            r = claim_match.match_one(parent, child)
            if r is None:
                continue        # 5. no match: no action

            # 3. U1 exact — mint the edge, then archive, same pass.
            if r.tier == "exact":
                _write_decision_link(child, parent, evidence_source="id_link",
                                     session_id=session_id)
                _archive_cascaded_child(state, child, parent, session_id, ts,
                                        "id_link")
                archived.append({"child": child["id"], "parent": parent["id"],
                                 "evidence_source": "id_link"})
                break

            # 4. Fuzzy (U2/U3/U4) — open a review only. Never archives, never
            #    mints an edge (disposition C1).
            if pair in open_pairs:
                continue
            _open_review(child, parent, r.reason_code, r.test)

    return {"archived": archived, "proposed": proposed, "rejected": rejected}


# --- review resolution (§7.2, dispositions #16, #18, #20, C4, N5) -----------

def _find(state: dict, kind: str, item_id: str) -> dict | None:
    return next((i for i in state.get(kind, []) if i.get("id") == item_id), None)


def _validate_resolution_rows(state: dict, resolutions: dict,
                              by_pair: dict) -> list[tuple[str, dict, dict]]:
    """The malformed-input class, checked for EVERY row BEFORE any write.

    §7.2's prose is explicit that a missing, duplicated, unknown or
    invalid-enum row "rejects the WHOLE delta before any write", and
    test_missing_or_duplicate_review_rejects_whole_delta asserts exactly that.
    §7.2's shown loop raises on the second of a duplicate pair, which is after
    the first has already written an edge — so validation is hoisted into its
    own pass here rather than left inline. Staleness is deliberately NOT
    checked here: it is the other failure class, per-row and non-fatal.

    Returns the flattened, validated work list as (kind, row, review) triples.
    """
    work, seen = [], set()
    for kind in ("cascade_confirm", "cascade_reject"):
        for row in resolutions.get(kind, []) or []:
            if not isinstance(row, dict) or not row.get("child") or not row.get("parent"):
                raise ValueError(f"malformed cascade resolution row in {kind}: {row!r}")
            key = (row["child"], row["parent"])
            if key in seen:
                raise ValueError(
                    f"duplicate cascade resolution for {key} — rejecting the whole delta")
            seen.add(key)
            review = by_pair.get(key)
            if review is None or review.get("status") != "open":
                raise ValueError(f"{kind} for unknown/non-open review {key}")
            if kind == "cascade_confirm" and (
                    _find(state, "done", row["child"]) is None
                    or _find(state, "decisions", row["parent"]) is None):
                raise ValueError(f"cascade_confirm references missing item {key}")
            work.append((kind, row, review))
    return work


def apply_review_resolutions(state: dict, resolutions: dict,
                             session_id: str, ts: str) -> dict:
    """Consumes resolutions['cascade_confirm'] / ['cascade_reject']. Called
    from apply_delta AFTER the drift loop and BEFORE cascade.apply (§8.1), so
    a confirmation's freshly-written edge is seen by that same call's step 1
    and archives in the same merge.

    Two distinct failure classes (disposition C4 — one policy, code, docstring
    and test agreeing):

      - Missing, duplicated, unknown review key, or an invalid enum value
        -> ValueError, rejecting the WHOLE delta before any write.
      - `item_fingerprint` OR `parent_set_fingerprint` mismatch on an
        otherwise-valid row -> mark that ONE review 'invalidated', write no
        edge for it, and carry on with the rest of the delta. A single
        resolutions delta can carry the whole drained backlog (§11);
        discarding every other still-valid confirmation over one stale row
        would buy no safety, since no edge is written against stale evidence
        either way. A fresh 'open' row may re-propose the pair on a later
        cascade.apply pass — an invalidation is not a reject.

    `parent_set_fingerprint` is recomputed here from the CURRENT cascade parent
    set, not the set captured at proposal time, so a review invalidates when
    the cascade-relevant pool moved underneath it and not only when its own
    child changed. Deliberately conservative: an unrelated new cascade-class
    decision appearing is enough. Re-proposals are cheap and automatic; a
    wrong terminal archive is not.

    N5 DECIDED — ADOPTED, with the outcome made visible (VERDICT-rev2 §6).
    The promotion of a partial edge to whole now requires the review's own
    `proposed_test == "U1_PARTIAL"`, so a U2/U3/U4 confirmation can no longer
    promote a partial edge that appeared between proposal and resolution.
    Reasoning: `item_fingerprint` structurally cannot see `decision_links` —
    the one field C5's whole-authority model rests on — so the staleness guard
    is blind to a partial edge arriving mid-flight. Without the clause, an
    explicit "this is only a partial restatement" determination is overridden
    by a confirmation whose prompt never mentioned it, and the result is a
    monotone, unreversible archive. Rejecting the promotion costs one extra
    review round-trip on a rare race; accepting it costs a wrongly archived
    item with no recovery path. Given cascade is terminal and the queue runs
    at roughly two rows a day, that trade is not close.
    Rather than let this fall through as a silent no-op write (which would
    still report the row in `confirmed` while writing nothing — the exact
    audit gap C4's `cascade_invalidated` key exists to close), the case is
    detected here and reported as `invalidated`: the evidence the review was
    proposed against genuinely did drift, in a field the fingerprint cannot
    cover. The next cascade.apply then re-asks the question explicitly as a
    fresh U1_PARTIAL review — rev1's safe outcome, without rev1's bug.
    """
    reviews = state.setdefault("cascade_reviews", [])
    by_pair = {}
    for r in reviews:
        pair = _pair_of(r)
        if pair is not None:
            by_pair[pair] = r

    work = _validate_resolution_rows(state, resolutions, by_pair)

    live_parents = sorted(certify.parent_set_cascade(state), key=lambda p: p["id"])
    live_parent_fp = parent_set_fingerprint(live_parents)

    confirmed, rejected_out, invalidated_out = [], [], []
    for kind, row, review in work:
        if kind == "cascade_reject":
            review["status"] = "rejected"
            review["resolved_in"] = session_id
            review["resolution_reason"] = row.get("reason", "")
            rejected_out.append(row["child"])
            continue

        child = _find(state, "done", row["child"])
        parent = _find(state, "decisions", row["parent"])
        proposed_test = review.get("proposed_test")

        stale = (item_fingerprint(child) != review.get("item_fingerprint")
                 or live_parent_fp != review.get("parent_set_fingerprint"))
        # N5: a partial edge for this exact pair that this review was not asked
        # about. Invisible to item_fingerprint by construction.
        unasked_partial = (proposed_test != "U1_PARTIAL"
                           and _link_for(child, parent["id"], "partial") is not None)

        if stale or unasked_partial:
            review["status"] = "invalidated"
            review["resolved_in"] = session_id
            invalidated_out.append(row["child"])
            continue

        _write_decision_link(child, parent, evidence_source="review_confirmed",
                             session_id=session_id, proposed_test=proposed_test)
        review["status"] = "confirmed"
        review["resolved_in"] = session_id
        confirmed.append(row["child"])

    return {"confirmed": confirmed, "rejected": rejected_out,
            "invalidated": invalidated_out}
