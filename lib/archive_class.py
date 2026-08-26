"""Leading-clause archive classifier (M1). Spec: SPEC-rev2-certification-cascade.md §4.

`classify_archive_reason` only ever recognizes B's leading-clause vocabulary
(disposition #2); it never treats "cascade"/"lifecycle" as parser outputs
(disposition C1) — those are call-site overrides written directly by
merger.py's cascade and closed/rejected paths.
"""
import re

# §3 shared constant, exact. §6.3 reads this as `archive_class.CLASSIFIER_VERSION`
# to stamp the certificate with the classifier that produced its verdicts, so
# that a certificate can be re-audited against the vocabulary in force when it
# was written. Bump it only alongside a change to the clause vocabulary below.
CLASSIFIER_VERSION = "leading-clause-v1"

CASCADE_LEADING_CLAUSES = (
    "contradicted by new decision",
    "reversed",
    "no longer current",
    "superseded",
)
REGRADE_LEADING_CLAUSES = (
    "not design-shaping",
    "obvious",
    "plumbing",
    "plumbing/ops",
    "convention",
    "belongs in operations",
)
_CLAUSE_SPLIT_RE = re.compile(r"\s+--\s+|\s+—\s+|: ")

LEDGER_KEYS = ("decisions", "goals", "suggestions", "learnings", "done")


def _leading_clause(reason: str | None) -> str:
    reason = (reason or "").strip()
    return _CLAUSE_SPLIT_RE.split(reason, maxsplit=1)[0].strip().casefold()


def _starts_with_clause(leading: str, vocab_term: str) -> bool:
    if leading == vocab_term:
        return True
    if leading.startswith(vocab_term):
        nxt = leading[len(vocab_term):len(vocab_term) + 1]
        return nxt == "" or not nxt.isalnum()
    return False


def classify_archive_reason(reason: str | None) -> str:
    """'cascade' | 'regrade' | 'lifecycle' | 'unclassified'. Caller is
    responsible for the lifecycle-prefix (closed:/rejected:) and
    explicit-cascade-copy overrides — see merger.py call sites, §8.
    IMPORTANT (disposition C1, §8.3): this function's contract is that
    'cascade' and 'lifecycle' are CALL-SITE OVERRIDES, never parser
    outputs — it cannot and does not recognize generated cascade reasons
    or closed:/rejected: prefixes on its own. A caller that recomputes
    archive_class from this function alone, on an item whose class was
    already set by a call-site override, will silently downgrade it to
    'unclassified'. inbox_merge (§8.3) is the one call site outside
    merger.apply_delta that must respect this."""
    leading = _leading_clause(reason)
    for v in CASCADE_LEADING_CLAUSES:
        if _starts_with_clause(leading, v):
            return "cascade"
    for v in REGRADE_LEADING_CLAUSES:
        if _starts_with_clause(leading, v):
            return "regrade"
    return "unclassified"


def backfill(state: dict) -> int:
    """Idempotent: sets archive_class on every archived item across all
    LEDGER_KEYS that lacks it. Never overwrites an existing value. Returns
    the count added. Caller (merger.apply_delta) invokes this once per call,
    before cascade — cascade.apply's parent-set construction depends on it."""
    added = 0
    for kind in LEDGER_KEYS:
        for item in state.get(kind, []):
            if not item.get("archived_in") and item.get("status") != "archived":
                continue
            if item.get("archive_class"):
                continue
            item["archive_class"] = classify_archive_reason(item.get("archived_reason"))
            added += 1
    return added
