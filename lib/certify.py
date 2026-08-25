"""Render-time certification. Spec: SPEC-rev2-certification-cascade.md §6.

Certification is REVERSIBLE and mutates nothing (§0). `evaluate` never writes
to `state`; `copy_with_status_override` is a true copy-on-write; and
`quarantine_set`/`suspect_callouts` are plain accessors over an already-frozen
`Certificate`, never re-derivations (disposition #19).

Severity is capped by two independent scope rules:
  * an unclassified-class parent is SUSPECT-only, forever (disposition #14) —
    reversible quarantine is a one-sided recovery, archive is monotone, so
    archive authority stays gated on the mechanical classifier alone;
  * CONTRADICTION additionally requires `child.kind == "done"` (disposition
    #7) — the certification-layer generalization of "U1 is scope-safe, not
    categorically safe" (disposition #6).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from lib import archive_class
from lib import claim_match

# §6.3 sources these from the owning modules, where §3's shared-constants block
# assigns them. cert-claude read them through a getattr fallback because neither
# symbol existed when step 3 landed; both are now defined at their owning module
# (fix-up A, step 6), so the fallback is gone and a future divergence between the
# two values is a loud AttributeError rather than a silent default.
CLASSIFIER_VERSION = archive_class.CLASSIFIER_VERSION
MATCHER_VERSION = claim_match.MATCHER_VERSION

LEDGER_KEYS = ("decisions", "goals", "suggestions", "learnings", "done")

FUSE_MIN = 3
FUSE_FRACTION = 0.02


# --- parent-set construction (§6.1, open question 7, disposition #14) --------

def _effective_class(item: dict) -> str:
    """Compute-if-absent (disposition #28): an explicit `archive_class` always
    wins and is NEVER recomputed or overwritten; only a missing one is derived
    from the reason text. Read-only — the value is returned, never stamped
    onto the item, because `evaluate` must not mutate `state` (§0)."""
    return item.get("archive_class") or archive_class.classify_archive_reason(
        item.get("archived_reason"))


def _archived_by_class(state: dict) -> dict[str, list[dict]]:
    out = {"cascade": [], "regrade": [], "lifecycle": [], "unclassified": []}
    for d in state.get("decisions", []):
        if d.get("archived_in") or d.get("status") == "archived":
            out[_effective_class(d)].append(d)
    return out


def parent_set_certification(state: dict) -> list[dict]:
    """Adopted, open question 7: cascade + unclassified. regrade/lifecycle
    excluded before any match. Unclassified parents may drive SUSPECT only
    (never CONTRADICTION, never quarantine_set, never a cascade parent) —
    see `_severity` below."""
    by_class = _archived_by_class(state)
    return by_class["cascade"] + by_class["unclassified"]


def parent_set_cascade(state: dict) -> list[dict]:
    """archive_class == 'cascade' only. Unaffected by open question 7."""
    return _archived_by_class(state)["cascade"]


# --- severity (§6.2, disposition #7, C3) ------------------------------------

# §6.2 writes this as r"\bdec-[0-9a-f]{8}\b". That hex-only body cannot match
# the synthetic reversal ids the frozen §14 fixtures are built from
# ("dec-fusereplace", "dec-exitrev1", ...), so it is widened to the id-token
# boundary convention claim_match.whole_token_id already uses (disposition #5).
# This changes nothing on live data: every real ledger id is dec- + 8 hex, and
# the result is intersected with active decision ids regardless, so the two
# regexes return an identical set for the live corpus — verified against
# dec-06ca4291, whose reason contains no "dec-" substring at all, keeping the
# C3-corrected `live_reversal == []` fact intact.
_ID_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_-])dec-[A-Za-z0-9_]+(?![A-Za-z0-9_-])")


def _named_live_replacement(parent: dict, state: dict) -> list[str]:
    """dec- tokens in archived_reason that currently resolve to an active
    decision. Scope restricted to dec- only (disposition #12)."""
    active_decision_ids = {d["id"] for d in state.get("decisions", [])
                           if d.get("status") == "active"}
    found = _ID_TOKEN_RE.findall(parent.get("archived_reason") or "")
    return sorted(set(found) & active_decision_ids)


def _severity(parent: dict, child_kind: str, live_reversal: list[str]) -> str:
    if _effective_class(parent) != "cascade":
        return "SUSPECT"          # unclassified parents: capped, disposition #14
    if child_kind == "done" and live_reversal:
        return "CONTRADICTION"    # disposition #7 — done-only
    return "SUSPECT"


# --- certificate (§6.3) ------------------------------------------------------

@dataclass(frozen=True)
class Certificate:
    project: str
    certified_at: str
    classifier_version: str
    matcher_version: str
    verdict: str              # NO_KNOWN_FALSEHOOD | SUSPECT | CONTRADICTION | UNCERTIFIED
    counts: dict
    findings: tuple[dict, ...]
    quarantine_ids: frozenset[str]     # frozen at evaluate() time — disposition #19
    not_checked: tuple[dict, ...]
    uncertified_regions: tuple[dict, ...]
    resolution_backlog: dict
    fuse_reason: str | None = None

    def to_dict(self) -> dict:
        """Fully JSON-serializable — this is what renderer.main writes to the
        `.certificate.json` sidecar (§9.3), so no frozensets or tuples survive."""
        return {
            "project": self.project,
            "certified_at": self.certified_at,
            "classifier_version": self.classifier_version,
            "matcher_version": self.matcher_version,
            "verdict": self.verdict,
            "counts": dict(self.counts),
            "findings": [dict(f) for f in self.findings],
            "quarantine_ids": sorted(self.quarantine_ids),
            "not_checked": [dict(n) for n in self.not_checked],
            "uncertified_regions": [dict(r) for r in self.uncertified_regions],
            "resolution_backlog": dict(self.resolution_backlog),
            "fuse_reason": self.fuse_reason,
        }


def eligible_item_ids(state: dict) -> set[str]:
    return {i["id"] for k in LEDGER_KEYS for i in state.get(k, [])
            if i.get("status") == "active" and i.get("id")}


def _kind_of(state: dict, item_id: str) -> str:
    for kind in LEDGER_KEYS:
        if any(i.get("id") == item_id for i in state.get(kind, [])):
            return kind
    return "unknown"


def evaluate(state: dict, eligible_ids: set[str], *,
             now: datetime | None = None) -> Certificate:
    now = now or datetime.now(timezone.utc)
    parents = parent_set_certification(state)
    children = [i for k in LEDGER_KEYS for i in state.get(k, [])
                if i.get("id") in eligible_ids]

    findings = []
    for parent in sorted(parents, key=lambda p: p["id"]):          # stable order
        for child in sorted(children, key=lambda c: c["id"]):
            r = claim_match.match_one(parent, child)
            if r is None:
                continue
            child_kind = _kind_of(state, child["id"])
            live_reversal = _named_live_replacement(parent, state)
            severity = _severity(parent, child_kind, live_reversal)
            findings.append({
                "severity": severity, "child": child["id"], "child_kind": child_kind,
                "parent": parent["id"], "parent_class": _effective_class(parent),
                "tier": r.tier, "test": r.test, "reason_code": r.reason_code,
                "live_reversal": live_reversal,
                "load_bearing": child.get("importance") == "load_bearing",  # disposition C3
                "action": "quarantined" if severity == "CONTRADICTION" else "flagged",
            })

    provisional_quarantine = {f["child"] for f in findings
                              if f["severity"] == "CONTRADICTION"}
    fuse_bound = max(FUSE_MIN, FUSE_FRACTION * len(eligible_ids))
    fuse_tripped = len(provisional_quarantine) > fuse_bound

    if fuse_tripped:
        quarantine_ids = frozenset()
        verdict = "UNCERTIFIED"
        fuse_reason = "fuse_tripped"
        for f in findings:
            if f["severity"] == "CONTRADICTION":
                f["action"] = "rendered_fuse_open"
    else:
        quarantine_ids = frozenset(provisional_quarantine)
        fuse_reason = None
        if provisional_quarantine:
            verdict = "CONTRADICTION"
        elif any(f["severity"] == "SUSPECT" for f in findings):
            verdict = "SUSPECT"
        else:
            verdict = "NO_KNOWN_FALSEHOOD"

    return Certificate(
        project=state.get("project", ""),
        certified_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        classifier_version=CLASSIFIER_VERSION,
        matcher_version=MATCHER_VERSION,
        verdict=verdict,
        counts={
            "rendered_eligible": len(eligible_ids),
            "contradiction": sum(1 for f in findings if f["severity"] == "CONTRADICTION"),
            "suspect": sum(1 for f in findings if f["severity"] == "SUSPECT"),
            "quarantined": len(quarantine_ids),
            "abstained_parents": len(_archived_by_class(state)["unclassified"]),
        },
        findings=tuple(findings),
        quarantine_ids=quarantine_ids,
        not_checked=_not_checked_block(state),
        uncertified_regions=(
            {"region": "summary", "chars": len(str(state.get("summary", ""))),
             "reason": "free-text, no item identity"},
            {"region": "operations", "rows": len(state.get("operations", [])),
             "reason": "rows carry no id/status"},
        ),
        resolution_backlog=_resolution_backlog(state),
        fuse_reason=fuse_reason,
    )


def quarantine_set(cert: Certificate) -> set[str]:
    """Plain accessor — never a re-derivation from findings (disposition #19).
    Freezing the set at evaluate() time makes 'still withholding rows after the
    fuse already cleared them' structurally impossible."""
    return set(cert.quarantine_ids)


def suspect_callouts(cert: Certificate) -> dict[str, str]:
    """Disposition C3. Plain accessor, same freezing discipline as
    `quarantine_set`: builds the exact `{child_id: callout_text}` map the
    renderer's copy-on-write pass consumes, from the already-frozen
    `cert.findings` — never re-walks state, never re-runs match_one.

    Filtered to `load_bearing` children only: a standard/minor SUSPECT still
    appears in `cert.findings` and in the sidecar, just never inline."""
    return {
        f["child"]: f" [⚠ SUSPECT: matches archived decision {f['parent']} via {f['test']}.]"
        for f in cert.findings
        if f["severity"] == "SUSPECT" and f.get("load_bearing")
    }


def _not_checked_block(state: dict) -> tuple[dict, ...]:
    trio = sorted(d["id"] for d in _archived_by_class(state)["unclassified"])
    return (
        {"class": "active_vs_active_contradiction",
         "example": ["work-24bf11b3", "dec-4348e10f"], "tracked_as": "Job S"},
        {"class": "unclassified_parents", "count": len(trio), "ids": trio,
         "checked_as": "suspect_only", "not_checked_for": ["contradiction", "archive"]},
        {"class": "uncertified_regions", "regions": ["summary", "operations"]},
    )


def _render_ordinal(state: dict) -> int:
    """§7's `cascade.render_ordinal`, resolved lazily.

    §15's dependency graph makes step 3 (this module + the renderer wire)
    shippable on its own — `1 -> {2,3} -> wired certification` — while
    `lib/cascade.py` is step 6. A module-scope `from lib import cascade` would
    contradict that by making certification uncollectable until cascade lands.
    So: prefer cascade's definition whenever it exists (single source of truth
    once step 6 is in), and fall back to §7's exact formula until then.
    """
    try:
        from lib import cascade
    except ImportError:
        sessions = state.get("sessions", []) or []
        return len(sessions) + sum(len(s.get("reruns", []) or []) for s in sessions)
    return cascade.render_ordinal(state)


def _resolution_backlog(state: dict) -> dict:
    reviews = [r for r in state.get("cascade_reviews", []) if r.get("status") == "open"]
    ordinal = _render_ordinal(state)
    oldest_age = max(
        (ordinal - r.get("first_seen_render", ordinal) for r in reviews), default=0)
    return {"open_reviews": len(reviews), "oldest_render_age": oldest_age}


def copy_with_status_override(state: dict, quarantine_ids: set[str],
                              suspect_by_child: dict[str, str] | None = None) -> dict:
    """True copy-on-write: the top-level dict, every affected ledger list, and
    only the item dicts actually being overridden are copied. Untouched items
    keep object identity. Source `state` is never mutated. Named
    'copy_with_status_override', not 'shallow_copy_with_status_override' — a
    real shallow copy would share the mutated item dicts too.

    `suspect_by_child` (disposition C3) comes from `suspect_callouts(cert)`.
    Any id in it gets a render-local, ephemeral `_suspect_note` key stamped
    onto its COPY — never persisted (fan_out_items serializes each item from
    `state`, never from this function's return value, §8), never mutated on
    the source. This is the exact data path the five `_render_*` formatter
    closures read from (§9.1) — no `_render_*` signature changes (§3)."""
    touched_ids = set(quarantine_ids) | set(suspect_by_child or {})
    if not touched_ids:
        return state
    suspect_by_child = suspect_by_child or {}
    new_state = dict(state)
    for kind in LEDGER_KEYS:
        items = state.get(kind, [])
        if not any(i.get("id") in touched_ids for i in items):
            continue

        def _override(i: dict) -> dict:
            if i.get("id") not in touched_ids:
                return i
            patch = {}
            if i.get("id") in quarantine_ids:
                patch["status"] = "quarantined"
            if i.get("id") in suspect_by_child:
                patch["_suspect_note"] = suspect_by_child[i["id"]]
            return dict(i, **patch)

        new_state[kind] = [_override(i) for i in items]
    return new_state
