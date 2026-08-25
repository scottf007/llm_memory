"""Tests for lib/claim_match.py — the shared U1-U4 matcher. Spec:
SPEC-rev2-certification-cascade.md §5, §14.

Matcher functions are pure and status-blind (disposition #11) — they never
look at `status`, only text/rationale/decision_links. U1 is exact tier
(certify + cascade authority); U2-U4 are fuzzy tier (certify-only, never
mint an edge, never archive — disposition C1 in §0). SHINGLE is a named,
deferred gap (disposition #13, §16.2), not implemented here.
"""

from tests.fixtures.certification.live_ledger import load_live_state, find
from tests.fixtures.certification.replay_oracle import build_oracle

from lib import claim_match


def _parent(**over):
    p = {"id": "dec-parent01", "text": "", "rationale": "", "archived_reason": ""}
    p.update(over)
    return p


def _child(**over):
    c = {"id": "work-child001", "text": "", "rationale": "", "decision_links": []}
    c.update(over)
    return c


# --- U1 -----------------------------------------------------------------

def test_u1_existing_whole_edge():
    parent = _parent(id="dec-aaaa1111")
    child = _child(decision_links=[{
        "decision_id": "dec-aaaa1111", "relation": "implements_current_claim", "scope": "whole",
    }])
    r = claim_match.u1_id_link(parent, child)
    assert r is not None and r.test == "U1" and r.tier == "exact"

    child_partial = _child(decision_links=[{
        "decision_id": "dec-aaaa1111", "relation": "implements_current_claim", "scope": "partial",
    }])
    assert claim_match.u1_id_link(parent, child_partial) is None


def test_u1_whole_token_text():
    parent = _parent(id="dec-06ca4291")
    hit = _child(text="This restates dec-06ca4291 directly.")
    assert claim_match.u1_id_link(parent, hit) is not None

    miss = _child(text="See dec-06ca4291x for the follow-up.")
    assert claim_match.u1_id_link(parent, miss) is None


def test_u1_citation_matcher_positive():
    state = load_live_state()
    parent = find(state, "decisions", "dec-06ca4291")
    child = find(state, "learnings", "lrn-18ac47d5")
    r = claim_match.u1_id_link(parent, child)
    assert r is not None
    assert r.test == "U1" and r.tier == "exact"
    assert r.parent_id == "dec-06ca4291" and r.child_id == "lrn-18ac47d5"


# --- U2 -----------------------------------------------------------------

def test_u2_founding_span():
    """Via the replay oracle (§13): dec-7bf964c0's retired-claim span (>=6
    tok) matches work-363365bf's text. dec-7bf964c0's own replacement-path
    span (also >=6 tok, quoting the *new* decision's title, not the old
    claim) matches no live item."""
    import merger

    oracle = build_oracle(merger)

    parent = find(oracle, "decisions", "dec-7bf964c0")
    child = find(oracle, "done", "work-363365bf")
    r = claim_match.u2_quoted_old_claim(parent, child)
    assert r is not None
    assert r.test == "U2" and r.tier == "fuzzy"
    assert r.score == 6

    # A 5-token span (below QUOTE_MIN_TOKENS) never fires, even if it would
    # otherwise be found in the child text.
    short_span_parent = _parent(archived_reason="reversed -- 'one two three four five'")
    short_span_child = _child(text="one two three four five is still here")
    assert claim_match.u2_quoted_old_claim(short_span_parent, short_span_child) is None

    # dec-7bf964c0's own SECOND quoted span (the replacement-path span,
    # naming dec-b4a371d1's claim rather than restating the retired one)
    # is also >=6 tokens, but matches no live item -- isolate it from the
    # first (matching) span so the assertion actually exercises it, since
    # u2_quoted_old_claim returns on the first span that matches.
    replacement_span_only = dict(
        parent,
        archived_reason="reversed -- 'the unconditional load_bearing-always-renders exemption is gone'",
    )
    assert claim_match.u2_quoted_old_claim(replacement_span_only, child) is None


def test_u2_replacement_fp_is_fuzzy_only():
    """The dec-06ca4291 -> dec-88414901 possessive-apostrophe false
    positive is a pinned regression fixture (disposition #8) — kept
    deliberately, never fixed in this slice. It can only ever land on the
    fuzzy tier, never exact."""
    state = load_live_state()
    parent = find(state, "decisions", "dec-06ca4291")
    child = find(state, "decisions", "dec-88414901")
    r = claim_match.u2_quoted_old_claim(parent, child)
    assert r is not None
    assert r.tier == "fuzzy"
    assert r.test == "U2"

    r_u1 = claim_match.match_one(parent, child)
    # match_one tries U1 first; U1 does not fire on this pair (no
    # decision_links entry, no whole-token id citation), so U2's fuzzy hit
    # is what surfaces -- never "exact".
    assert r_u1 is not None and r_u1.tier == "fuzzy"


# --- U3 -----------------------------------------------------------------

def test_u3_lcs_is_substring_not_subsequence():
    """Synthetic discriminator (kept per disposition #10): a substring
    reading returns a short run; a subsequence reading of the same pair
    would return a much longer count. Only the substring reading is
    correct."""
    a = "the quick brown fox jumps over the lazy dog while everyone watches"
    b = "xtheq quick brownxx fox yjumps zoverz thex lazy dogx watching"
    substring_len = claim_match.longest_common_substring_len(a, b)
    assert substring_len <= 20

    # A subsequence-based reading of this same pair would be far longer --
    # confirming the discriminator actually distinguishes the two
    # algorithms rather than coincidentally agreeing on this input.
    def _lcs_subsequence_len(x, y):
        dp = [[0] * (len(y) + 1) for _ in range(len(x) + 1)]
        for i in range(1, len(x) + 1):
            for j in range(1, len(y) + 1):
                if x[i - 1] == y[j - 1]:
                    dp[i][j] = dp[i - 1][j - 1] + 1
                else:
                    dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
        return dp[-1][-1]

    assert _lcs_subsequence_len(a, b) >= 40


def test_u3_live_corpus_substring_facts():
    state = load_live_state()
    import re

    CASCADE = ("contradicted by new decision", "reversed", "no longer current", "superseded")
    SPLIT_RE = re.compile(r"\s+--\s+|\s+—\s+|: ")

    def leading(reason):
        return SPLIT_RE.split((reason or "").strip(), maxsplit=1)[0].strip().casefold()

    def is_cascade(reason):
        lc = leading(reason)
        return any(lc == v or (lc.startswith(v) and (lc[len(v):len(v) + 1] == "" or not lc[len(v):len(v) + 1].isalnum()))
                    for v in CASCADE)

    parents = [d for d in state["decisions"]
               if d.get("status") == "archived" and is_cascade(d.get("archived_reason"))]
    children = [w for w in state["done"] if w.get("status") == "active"]
    assert len(parents) == 33
    assert len(children) == 90

    scores = []
    for p in parents:
        for c in children:
            n = claim_match.longest_common_substring_len(
                claim_match.normalize_text(c.get("text")), claim_match.normalize_text(p.get("text")))
            scores.append(n)
    assert sum(1 for n in scores if n >= 80) == 0
    assert max(scores) == 59

    founding_parent = find(state, "decisions", "dec-16995f45")
    founding_child = find(state, "done", "work-d64ee7e8")
    n = claim_match.longest_common_substring_len(
        claim_match.normalize_text(founding_child.get("text")),
        claim_match.normalize_text(founding_parent.get("text")))
    assert n == 59
    # No subsequence assertion anywhere in this test (disposition #9).


def test_u3_79_80_boundary():
    """Guard chars ('A'/'Q' vs 'B'/'Z') on either side of the shared run so
    no accidental common prefix/suffix text extends the substring length
    past the exact boundary being tested."""
    shared = "x" * 80
    a = "A" + shared + "Q"
    b = "B" + shared + "Z"
    assert claim_match.longest_common_substring_len(a, b) == 80
    r = claim_match.u3_long_restatement(_parent(text=b), _child(text=a))
    assert r is not None and r.test == "U3"

    shared_short = "x" * 79
    a2 = "A" + shared_short + "Q"
    b2 = "B" + shared_short + "Z"
    assert claim_match.longest_common_substring_len(a2, b2) == 79
    r2 = claim_match.u3_long_restatement(_parent(text=b2), _child(text=a2))
    assert r2 is None


# --- U4 -------------------------------------------------------------------

def test_u4_two_token_minimum():
    state = load_live_state()
    parent1 = find(state, "decisions", "dec-00245540")
    parent2 = find(state, "decisions", "dec-16a907b5")
    child = find(state, "done", "work-92a4c91c")
    for parent in (parent1, parent2):
        r = claim_match.u4_dead_substrate(parent, child)
        assert r is not None, parent["id"]
        assert r.test == "U4" and r.tier == "fuzzy"
        assert r.score == 2

    # Corrected (N3): two synthetic, mutually non-nesting DEAD_SUBSTRATE_TOKENS
    # members, only ONE of which is present on both sides, does not fire --
    # this replaces the prior "1 shared token" framing, which (using the
    # live nested "narrative-updater.md"/"agents/narrative-updater.md" pair)
    # could not actually distinguish one real occurrence from two
    # independent ones.
    single_token_parent = _parent(text="", rationale="",
                                    archived_reason="retires records/ from the design")
    single_token_child = _child(text="still touches records/ directly")
    assert claim_match.u4_dead_substrate(single_token_parent, single_token_child) is None


def test_u4_requires_parent_scoped_tokens():
    """Regression guard for the pre-C2 unscoped bug (matched all 33 live
    cascade parents against this one child)."""
    state = load_live_state()
    non_parent = find(state, "decisions", "dec-51544bf2")
    child = find(state, "done", "work-92a4c91c")
    parent_surface = " ".join([
        non_parent.get("text") or "", non_parent.get("rationale") or "",
        non_parent.get("archived_reason") or "",
    ])
    assert "narrative-updater.md" not in parent_surface
    assert "agents/narrative-updater.md" not in parent_surface
    assert claim_match.u4_dead_substrate(non_parent, child) is None
    # ...even though the child still globally contains both tokens.
    assert "narrative-updater.md" in child["text"]


def test_u4_current_substrate_allow_ignore():
    parent = _parent(text="", rationale="",
                      archived_reason="retires memory_store and records/ from the design")
    child = _child(text="items/{project}/{kind}/{id}.json is the new substrate")
    assert claim_match.u4_dead_substrate(parent, child) is None


# --- ordering / word-boundary regression -----------------------------------

def test_match_order_first_wins():
    parent = _parent(id="dec-order01",
                      text="x" * 80,
                      archived_reason="reversed -- 'six token span right here now'")
    child = _child(
        text=("x" * 80) + " six token span right here now dec-order01",
        decision_links=[{"decision_id": "dec-order01",
                          "relation": "implements_current_claim", "scope": "whole"}],
    )
    r = claim_match.match_one(parent, child)
    assert r is not None and r.test == "U1"


def test_u1_word_boundary_excludes_suffix():
    """Regression guard (disposition #5): the id must be a standalone
    token, not a prefix of a longer identifier-shaped run. NOTE: the
    §14 table's own literal illustration ("dec-06ca4291.json does not
    whole-token match") was independently re-verified against the exact
    pinned regex in §5 and does NOT hold -- "." is not in the
    [A-Za-z0-9_-] continuation class, so a trailing ".json" *is* a
    boundary, and the pinned regex correctly treats "dec-06ca4291.json"
    as containing the whole token "dec-06ca4291" (this is by design: "."
    behaves like a real delimiter, same as a space or a comma). The
    genuine exclusion this disposition guards -- a suffix that IS an
    identifier-continuation character -- is asserted below instead, so
    this test exercises real, code-verified behavior rather than an
    unverified inherited example."""
    assert claim_match.whole_token_id("see dec-06ca4291 for detail", "dec-06ca4291") is True
    assert claim_match.whole_token_id("see dec-06ca4291x for detail", "dec-06ca4291") is False
    assert claim_match.whole_token_id("see dec-06ca4291_v2 for detail", "dec-06ca4291") is False
