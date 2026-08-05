"""Tests for renderer.py — decay ranking and token-budget enforcement.

The property that matters here is boundedness: the narrative is injected at
every session start, so its size must be a function of the budget, not of
project age. Before the budget existed, `load_bearing` items bypassed
filtering entirely and goals/suggestions had no filter at all, so a mature
project's narrative grew without limit (finance_nexus reached ~33k tokens).
"""

from datetime import datetime, timedelta, timezone

import pytest

import renderer


NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)


def _ts(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _item(text: str, importance: str = "standard", days_ago: float = 0.0, **extra) -> dict:
    item = {
        "id": f"x-{abs(hash(text)) % 10**8:08x}",
        "text": text,
        "status": "active",
        "importance": importance,
        "last_touched_at": _ts(days_ago),
    }
    item.update(extra)
    return item


def _state(**kinds) -> dict:
    state = {
        "project": "testproj",
        "summary": {"what": "A test project."},
        "operations": [],
        "decisions": [],
        "goals": [],
        "suggestions": [],
        "learnings": [],
        "done": [],
        "sessions": [],
    }
    state.update(kinds)
    return state


def _tokens(md: str) -> int:
    return len(md) // renderer.CHARS_PER_TOKEN


# --- scoring -------------------------------------------------------------

def test_load_bearing_score_is_floored_not_exempt():
    """A very old load-bearing item keeps a nonzero score (so it ranks above
    aged standard items) but is still a finite, comparable number."""
    old = _item("ancient", "load_bearing", days_ago=3650)
    fresh = _item("new", "load_bearing", days_ago=0)
    assert renderer._score(old, NOW) == pytest.approx(
        renderer.IMPORTANCE_FLOORS["load_bearing"]
    )
    assert renderer._score(fresh, NOW) > renderer._score(old, NOW)


def test_load_bearing_outranks_aged_standard():
    lb = _item("lb", "load_bearing", days_ago=3650)
    std = _item("std", "standard", days_ago=25)
    assert renderer._score(lb, NOW) > renderer._score(std, NOW)


def test_raw_score_ignores_floor_so_stale_callout_still_fires():
    """The stale callout exists to surface load-bearing items whose real
    relevance has decayed — the floor must not hide them."""
    old = _item("ancient", "load_bearing", days_ago=3650)
    assert renderer._raw_score(old, NOW) < renderer.STALE_SCORE_THRESHOLD
    assert renderer._score(old, NOW) >= renderer.STALE_SCORE_THRESHOLD


def test_aged_standard_dissolves_and_minor_never_renders():
    items = [
        _item("recent", "standard", days_ago=1),
        _item("aged", "standard", days_ago=90),
        _item("trivial", "minor", days_ago=0),
    ]
    primary, secondary, dissolved = renderer._partition_by_score(items, NOW)
    assert [i["text"] for i in secondary] == ["recent"]
    assert primary == []
    assert dissolved == 2


# --- value float ---------------------------------------------------------

def test_value_orders_within_a_tier():
    """The defect this exists to fix: without `value`, ranking inside
    load_bearing is pure recency, so the budget drops the oldest — which are
    often the most foundational items."""
    old_foundational = _item("foundational", "load_bearing", days_ago=900, value=1.0)
    recent_marginal = _item("marginal", "load_bearing", days_ago=1, value=0.0)
    assert renderer._score(old_foundational, NOW) > renderer._score(recent_marginal, NOW)


def test_value_does_not_cross_tiers():
    """A top-valued minor item must not outrank a bottom-valued load-bearing
    one — the bucket is still the coarse class."""
    best_minor = _item("minor", "minor", value=1.0)
    worst_lb = _item("lb", "load_bearing", value=0.0)
    assert renderer._score(worst_lb, NOW) > renderer._score(best_minor, NOW)


@pytest.mark.parametrize("bad", [None, "high", True, float("nan")])
def test_absent_or_malformed_value_is_neutral(bad):
    """Existing items have no `value`; they must rank exactly as before."""
    graded = _item("x", "load_bearing")
    graded["value"] = bad
    ungraded = _item("y", "load_bearing")
    ungraded.pop("value", None)
    assert renderer._score(graded, NOW) == pytest.approx(renderer._score(ungraded, NOW))


def test_value_is_clamped():
    for v, expected in ((5.0, 1.0), (-3.0, 0.0)):
        assert renderer._value_multiplier({"value": v}) == pytest.approx(
            1.0 + (expected - renderer.VALUE_NEUTRAL) * renderer.VALUE_SPREAD
        )


# --- budget --------------------------------------------------------------

def test_trim_to_budget_respects_hard_ceiling():
    items = [_item(f"item {i} " + "x" * 200, "load_bearing") for i in range(300)]
    (kept,), dropped = renderer._trim_to_budget(
        [items], lambda i: i["text"], "approach", NOW
    )
    hard = (renderer.SECTION_TOKEN_BUDGETS["approach"]
            * renderer.CHARS_PER_TOKEN * renderer.HARD_BUDGET_MULTIPLIER)
    assert sum(len(i["text"]) + 1 for i in kept) <= hard
    assert len(kept) + dropped == len(items)
    assert dropped > 0


def test_low_value_items_stop_at_the_soft_budget():
    """Filler cannot claim the overflow allowance."""
    items = [_item(f"item {i} " + "x" * 200, "minor", value=0.0) for i in range(100)]
    (kept,), dropped = renderer._trim_to_budget(
        [items], lambda i: i["text"], "approach", NOW
    )
    soft = renderer.SECTION_TOKEN_BUDGETS["approach"] * renderer.CHARS_PER_TOKEN
    assert sum(len(i["text"]) + 1 for i in kept) <= soft
    assert dropped > 0


def test_high_value_items_may_use_the_overflow_allowance():
    """A section of genuinely load-bearing material is allowed to run over."""
    def fill(importance, value):
        items = [_item(f"i{i} " + "x" * 200, importance, value=value) for i in range(100)]
        (kept,), _ = renderer._trim_to_budget(
            [items], lambda i: i["text"], "approach", NOW
        )
        return sum(len(i["text"]) + 1 for i in kept)

    soft = renderer.SECTION_TOKEN_BUDGETS["approach"] * renderer.CHARS_PER_TOKEN
    assert fill("load_bearing", 1.0) > soft
    assert fill("minor", 0.0) <= soft


def test_trim_to_budget_keeps_rank_prefix():
    """What renders is a clean prefix of the ranking — once an item overflows,
    everything below it stops, even a short item that would have fit."""
    budget_chars = renderer.SECTION_TOKEN_BUDGETS["suggestions"] * renderer.CHARS_PER_TOKEN
    items = [_item("A" * 100), _item("B" * budget_chars), _item("C")]
    (kept,), dropped = renderer._trim_to_budget([items], lambda i: i["text"], "suggestions")
    assert [i["text"] for i in kept] == ["A" * 100]
    assert dropped == 2


def test_trim_to_budget_always_keeps_one_item():
    """An over-long top item must not blank the section."""
    items = [_item("Z" * 100_000)]
    (kept,), dropped = renderer._trim_to_budget([items], lambda i: i["text"], "done")
    assert len(kept) == 1
    assert dropped == 0


def test_trim_to_budget_preserves_group_arity():
    primary = [_item(f"lb{i}", "load_bearing") for i in range(3)]
    secondary = [_item(f"std{i}") for i in range(3)]
    groups, _ = renderer._trim_to_budget(
        [primary, secondary], lambda i: i["text"], "approach"
    )
    assert len(groups) == 2


def test_unbudgeted_section_is_untouched():
    """Operations has no entry in SECTION_TOKEN_BUDGETS and is spec-exempt."""
    items = [_item(f"op{i}") for i in range(500)]
    (kept,), dropped = renderer._trim_to_budget([items], lambda i: i["text"], "operations")
    assert len(kept) == 500
    assert dropped == 0


# --- end-to-end boundedness ---------------------------------------------

def test_narrative_is_bounded_by_project_size():
    """The regression this whole change exists to prevent: a project with a
    huge all-load-bearing ledger must not produce an unbounded narrative."""
    big = _state(
        decisions=[_item(f"decision {i} " + "d" * 300, "load_bearing", days_ago=i)
                   for i in range(400)],
        done=[_item(f"work {i} " + "w" * 300, "load_bearing", days_ago=i)
              for i in range(400)],
        learnings=[_item(f"learning {i} " + "l" * 300, "load_bearing", days_ago=i)
                   for i in range(400)],
        goals=[_item(f"goal {i} " + "g" * 300, days_ago=i) for i in range(400)],
        suggestions=[_item(f"suggestion {i} " + "s" * 300, days_ago=0)
                     for i in range(400)],
    )
    md = renderer.render(big)
    ceiling = (sum(renderer.SECTION_TOKEN_BUDGETS.values())
               * renderer.HARD_BUDGET_MULTIPLIER)
    # Budgeted sections dominate; the fixed sections add a small constant.
    assert _tokens(md) < ceiling * 1.2


def test_narrative_growth_saturates():
    """Ten times the ledger must not produce ten times the narrative."""
    def build(n):
        return renderer.render(_state(
            decisions=[_item(f"d{i} " + "x" * 300, "load_bearing", days_ago=i % 60)
                       for i in range(n)],
            done=[_item(f"w{i} " + "x" * 300, "load_bearing", days_ago=i % 60)
                  for i in range(n)],
        ))

    # 10x the ledger. Output may grow — the soft budget has an overflow
    # allowance — but it must saturate, not scale.
    small, large = build(40), build(400)
    assert _tokens(large) < _tokens(small) * 2.5


def test_small_project_is_unaffected_by_budget():
    """Projects under budget render in full — the cap must not cost anything
    for the common case."""
    state = _state(
        decisions=[_item(f"decision {i}", "load_bearing") for i in range(5)],
        done=[_item(f"work {i}") for i in range(5)],
    )
    md = renderer.render(state)
    for i in range(5):
        assert f"decision {i}" in md
        assert f"work {i}" in md
    assert "dissolved" not in md.split("## What's Done")[1].split("##")[0]


# --- nothing silently disappears ----------------------------------------

@pytest.mark.parametrize("kind,section", [
    ("decisions", "## Approach"),
    ("done", "## What's Done"),
    ("learnings", "## What We've Learnt"),
    ("suggestions", "## Suggested Work"),
])
def test_over_budget_items_are_reported_not_silently_dropped(kind, section):
    state = _state(**{kind: [_item(f"{kind} {i} " + "y" * 400, "load_bearing")
                             for i in range(200)]})
    body = renderer.render(state).split(section)[1].split("\n## ")[0]
    assert "dissolved" in body
    assert "project_lookup" in body


def test_goals_over_budget_are_reported():
    state = _state(goals=[_item(f"goal {i} " + "y" * 400) for i in range(200)])
    body = renderer.render(state).split("## What We Want To Do")[1].split("\n## ")[0]
    assert "over section budget" in body
    assert "project_lookup" in body


def test_goals_are_not_decayed_away():
    """The format spec exempts goals from decay — only the budget backstop
    may drop them, never age alone."""
    state = _state(goals=[_item("very old goal", days_ago=3650)])
    body = renderer.render(state).split("## What We Want To Do")[1].split("\n## ")[0]
    assert "very old goal" in body


def test_suggestions_do_decay():
    """Suggestions are the one section the spec wants to dissolve by age."""
    state = _state(suggestions=[
        _item("fresh idea", days_ago=1),
        _item("forgotten idea", days_ago=120),
    ])
    body = renderer.render(state).split("## Suggested Work")[1].split("\n## ")[0]
    assert "fresh idea" in body
    assert "forgotten idea" not in body
    assert "dissolved" in body


def test_archived_items_never_render():
    state = _state(decisions=[
        _item("live one", "load_bearing"),
        dict(_item("dead one", "load_bearing"), status="archived"),
    ])
    md = renderer.render(state)
    assert "live one" in md
    assert "dead one" not in md


# --- contested report (input to re-valuation) ---------------------------

def test_no_report_when_nothing_is_cut():
    """Presence of the report is the signal that re-valuation has work."""
    _, report = renderer.render_with_report(_state(
        decisions=[_item(f"d{i}", "load_bearing") for i in range(3)],
    ))
    assert report["sections"] == {}


def test_report_names_items_either_side_of_the_cut():
    state = _state(decisions=[
        _item(f"decision {i} " + "z" * 400, "load_bearing", days_ago=i)
        for i in range(120)
    ])
    _, report = renderer.render_with_report(state)
    approach = report["sections"]["approach"]
    assert approach["dropped"] > 0
    outcomes = {c["outcome"] for c in approach["contested"]}
    assert outcomes == {"kept", "dropped"}
    assert len(approach["contested"]) <= renderer.CONTESTED_MAX
    # Every contested entry carries what the extractor needs to re-grade it.
    for c in approach["contested"]:
        assert c["id"] and c["text"] and c["importance"]
        assert isinstance(c["score"], float)


def test_contested_captures_the_whole_tie_band():
    """The case this exists for: an unvalued ledger where most load-bearing
    items sit at the same floored score, so which side of the cut they land on
    is arbitrary. All of them are contested, not just eight either side."""
    state = _state(decisions=[
        _item(f"decision {i} " + "z" * 400, "load_bearing", days_ago=400)
        for i in range(60)
    ])
    _, report = renderer.render_with_report(state)
    contested = report["sections"]["approach"]["contested"]
    # Every item is tied at the floor, so the band is far wider than a window.
    assert len(contested) > renderer.CONTESTED_WINDOW * 2
    assert len(contested) <= renderer.CONTESTED_MAX
    assert len({c["score"] for c in contested}) == 1


def test_contested_band_shrinks_once_values_separate_items():
    """As `value` populates, scores stop tying and the contested set collapses
    back to the neighbourhood of the cut — the pass self-limits."""
    state = _state(decisions=[
        dict(_item(f"decision {i} " + "z" * 400, "load_bearing", days_ago=400),
             value=i / 60)
        for i in range(60)
    ])
    _, report = renderer.render_with_report(state)
    contested = report["sections"]["approach"]["contested"]
    assert len(contested) <= renderer.CONTESTED_WINDOW * 2


def test_report_contested_straddles_the_boundary():
    """The kept side should be the weakest survivors and the dropped side the
    strongest casualties — that's what makes re-grading them worthwhile."""
    state = _state(decisions=[
        _item(f"decision {i} " + "z" * 400, "load_bearing", days_ago=i)
        for i in range(120)
    ])
    _, report = renderer.render_with_report(state)
    contested = report["sections"]["approach"]["contested"]
    kept = [c["score"] for c in contested if c["outcome"] == "kept"]
    dropped = [c["score"] for c in contested if c["outcome"] == "dropped"]
    assert min(kept) >= max(dropped)


def test_all_required_sections_present():
    md = renderer.render(_state())
    for section in ("## The Idea", "## Approach", "## Operations", "## What's Done",
                    "## What We've Learnt", "## What We Want To Do",
                    "## Suggested Work", "## Resuming", "## Source Transcripts"):
        assert section in md
