"""Shared U1-U4 matcher. Spec: SPEC-rev2-certification-cascade.md §5.

Matcher functions are pure and status-blind (disposition #11) — they never
look at `status`, only text/rationale/decision_links. U1 is exact tier
(certify + cascade authority); U2-U4 are fuzzy tier (certify-only, never
mint an edge, never archive — disposition C1 in §0). SHINGLE is a named,
deferred gap (disposition #13, §16.2), not implemented here.
"""
from __future__ import annotations
from dataclasses import dataclass
from functools import lru_cache
import re

# §3 shared constant, exact. §6.3 reads this as `claim_match.MATCHER_VERSION`.
# Bump it alongside any change to the U1-U4 semantics or their thresholds.
MATCHER_VERSION = "claim-match-v1"

EXACT_TESTS = ("U1",)
FUZZY_TESTS = ("U2", "U3", "U4")

LCS_MIN_CHARS = 80
QUOTE_MIN_TOKENS = 6
DEAD_SUBSTRATE_MIN_TOKENS = 2


@dataclass(frozen=True)
class MatchResult:
    test: str
    tier: str               # "exact" | "fuzzy"
    parent_id: str
    child_id: str
    score: int | None
    reason_code: str


def normalize_text(value: str | None) -> str:
    return _normalize_cached(value or "")


@lru_cache(maxsize=8192)
def _normalize_cached(value: str) -> str:
    return " ".join(value.split()).casefold()


_ID_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]+")


def whole_token_id(haystack: str, item_id: str) -> bool:
    """Word-boundary match — corrected per disposition #5 to use a
    lookaround, not a permissive word-char class (B's original class
    included '.', so it would also match 'dec-06ca4291.json')."""
    if not item_id:
        return False
    pattern = re.compile(r"(?<![A-Za-z0-9_-])" + re.escape(item_id) + r"(?![A-Za-z0-9_-])")
    return bool(pattern.search(haystack or ""))


def u1_id_link(parent: dict, child: dict) -> MatchResult | None:
    """SCOPE-SAFE, NOT CATEGORICALLY SAFE (disposition #6). An identifier
    can be a citation, not a restatement — dec-06ca4291 -> lrn-18ac47d5 is
    the pinned live example (§14). Safe for direct archive only because
    cascade.py never scopes children beyond active `done` (§7). Do not
    widen U1's child scope without a citation-vs-restatement rule.
    NOTE (disposition C5, §7.1): this function scans child text/rationale
    independently of any recorded decision_links entry — it has no way to
    know a `scope=="partial"` edge for this exact parent already exists.
    Callers with partial-edge context (cascade.apply) MUST check for one
    before calling match_one; this function does not and should not do
    that check itself, since certify.evaluate calls it with no cascade
    authority context at all (certification is read-only, §0)."""
    pid = parent.get("id", "")
    if not pid:
        return None
    links = child.get("decision_links") or []
    for link in links:
        if (link.get("decision_id") == pid
                and link.get("relation") == "implements_current_claim"
                and link.get("scope") == "whole"):
            return MatchResult("U1", "exact", pid, child.get("id", ""), None, "id_link")
    haystack = f"{child.get('text','')} {child.get('rationale','')}"
    if whole_token_id(haystack, pid):
        return MatchResult("U1", "exact", pid, child.get("id", ""), None, "id_link")
    return None


_QUOTE_RE = re.compile(r"'([^']{1,500})'|\"([^\"]{1,500})\"")


def u2_quoted_old_claim(parent: dict, child: dict) -> MatchResult | None:
    """Fuzzy tier (C1): certifies, never archives. ASCII delimiter scan,
    kept deliberately (disposition #8) — the dec-06ca4291 -> dec-88414901
    possessive-apostrophe false positive is a pinned regression fixture,
    not a bug to fix in this slice; it can never mutate decision_links or
    status regardless."""
    reason = parent.get("archived_reason") or ""
    child_norm = normalize_text(child.get("text"))
    for m in _QUOTE_RE.finditer(reason):
        span = m.group(1) or m.group(2)
        span_norm = normalize_text(span)
        if len(span_norm.split()) < QUOTE_MIN_TOKENS:
            continue
        if span_norm and span_norm in child_norm:
            ntok = len(span_norm.split())
            return MatchResult("U2", "fuzzy", parent.get("id", ""),
                                child.get("id", ""), ntok, f"quoted_span_{ntok}tok")
    return None


@lru_cache(maxsize=4096)
def _suffix_automaton(s: str) -> tuple[list[int], list[int], list[dict]]:
    """Online suffix automaton of `s`. O(len(s)) states, built once per
    distinct string and reused across every pair that string appears in —
    which is the whole point: a parent's text is compared against every
    eligible child, and was previously re-walked from scratch each time."""
    sa_len = [0]
    sa_link = [-1]
    sa_next: list[dict] = [{}]
    last = 0
    for ch in s:
        cur = len(sa_len)
        sa_len.append(sa_len[last] + 1)
        sa_link.append(-1)
        sa_next.append({})
        p = last
        while p != -1 and ch not in sa_next[p]:
            sa_next[p][ch] = cur
            p = sa_link[p]
        if p == -1:
            sa_link[cur] = 0
        else:
            q = sa_next[p][ch]
            if sa_len[p] + 1 == sa_len[q]:
                sa_link[cur] = q
            else:
                clone = len(sa_len)
                sa_len.append(sa_len[p] + 1)
                sa_link.append(sa_link[q])
                sa_next.append(dict(sa_next[q]))
                while p != -1 and sa_next[p].get(ch) == q:
                    sa_next[p][ch] = clone
                    p = sa_link[p]
                sa_link[q] = clone
                sa_link[cur] = clone
        last = cur
    return sa_len, sa_link, sa_next


def longest_common_substring_len(a: str, b: str) -> int:
    """Contiguous run only (C2) — never a subsequence. EXACT: returns the
    same value for every input as the original O(len(a)*len(b)) DP, verified
    pair-for-pair over the live corpus. Suffix-automaton walk, O(len(a)+len(b))
    with the automaton memoized per distinct string."""
    if not a or not b:
        return 0
    if len(a) > len(b):          # build on the shorter side — fewer states
        a, b = b, a
    sa_len, sa_link, sa_next = _suffix_automaton(a)
    v = 0
    length = 0
    best = 0
    for ch in b:
        nxt = sa_next[v]
        if ch in nxt:
            v = nxt[ch]
            length += 1
        else:
            while v != -1 and ch not in sa_next[v]:
                v = sa_link[v]
            if v == -1:
                v = 0
                length = 0
            else:
                length = sa_len[v] + 1
                v = sa_next[v][ch]
        if length > best:
            best = length
    return best


def u3_long_restatement(parent: dict, child: dict) -> MatchResult | None:
    a = normalize_text(child.get("text"))
    b = normalize_text(parent.get("text"))
    n = longest_common_substring_len(a, b)
    if n >= LCS_MIN_CHARS:
        return MatchResult("U3", "fuzzy", parent.get("id", ""),
                            child.get("id", ""), n, f"lcs_{n}")
    return None


# Retired-substrate vocabulary. Frozen; extend only in a future slice.
DEAD_SUBSTRATE_TOKENS = frozenset({
    "records/", "memories.uuid", "connections.from_uuid", "connections.to_uuid",
    "memory_store", "agents/narrative-updater.md", "narrative-updater.md",
})
CURRENT_SUBSTRATE_ALLOW_IGNORE = frozenset({
    "items/", "memory.db", "items_fts", "{project}.json",
    "items/{project}/{kind}/{id}.json",
})


def u4_dead_substrate(parent: dict, child: dict) -> MatchResult | None:
    """PARENT-SCOPED (disposition C2, corrected — VERDICT §4.1). The prior
    draft counted retired-substrate tokens against the CHILD ONLY, with no
    parent check, so it matched every one of the 33 live cascade-class
    parents against a single child that happens to mention two dead
    tokens (verified against the live ledger: `work-92a4c91c` scores 2
    child-side hits regardless of which parent it's being compared to,
    which silently invalidated the passed design's roughly-two-row
    exposure claim into a 33-row one). U4 requires the SAME >=2 tokens to
    appear on BOTH sides: the parent's own claim surface
    (text/rationale/archived_reason — a retired decision's own words for
    what it retired) and the child's text. This restores the passed
    design's semantics: "at least two retired-substrate tokens from the
    parent appearing in the child," not "the child mentions two retired
    tokens, from any parent."""
    child_text = child.get("text") or ""
    parent_surface = " ".join([
        parent.get("text") or "", parent.get("rationale") or "",
        parent.get("archived_reason") or "",
    ])
    hits = [t for t in DEAD_SUBSTRATE_TOKENS
            if t in child_text and t in parent_surface
            and t not in CURRENT_SUBSTRATE_ALLOW_IGNORE]
    if len(hits) >= DEAD_SUBSTRATE_MIN_TOKENS:
        return MatchResult("U4", "fuzzy", parent.get("id", ""),
                            child.get("id", ""), len(hits),
                            f"dead_substrate_{len(hits)}tok")
    return None


def match_one(parent: dict, child: dict) -> MatchResult | None:
    """U1 -> U2 -> U3 -> U4, first match wins. SHINGLE is a documented gap,
    not implemented (disposition #13)."""
    for fn in (u1_id_link, u2_quoted_old_claim, u3_long_restatement, u4_dead_substrate):
        r = fn(parent, child)
        if r is not None:
            return r
    return None
