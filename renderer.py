"""
Renderer — takes a project state JSON and produces the 9-section narrative
markdown. Pure code, no LLM. Deterministic view over status=active items.

Usage:
    python renderer.py <project_json_path> <output_md_path>

The resume_excerpt field on the most recent session is derived from the
tail of its conversation.md at render time (not stored in the JSON).

Rendering uses a decay-score filter: each item's relevance is
importance_weight × exp(-age_in_days / half_life), floored per importance.
Load-bearing items have a score floor so they sort to the top and stay
render-eligible indefinitely; standard items render while recent; minor
items never render individually.

Size is an input, not an emergent output. Every elastic section has a token
budget: items render in score order until the budget is spent, and the
remainder collapses into a "N dissolved — use project_lookup" pointer. This
is what bounds the document. The decay score decides *what* survives the
budget; the budget decides *how much* does. Without the second half, a
category that never decays (load_bearing) grows without limit.
"""

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import adapters
from lib import certify
from tools.memory_config import memory_root
from narrative_lock import NarrativeLockBusy, project_lock

HOME = Path.home()

# How many lines of dialogue to include as the resume_excerpt on the most
# recent active session. Pulled fresh from the conversation.md file.
DEFAULT_RESUME_LINES = 150

# Decay parameters. Tunables; see architecture-redesign-2026-04-18.md.
HALF_LIFE_DAYS = 30.0
IMPORTANCE_WEIGHTS = {"load_bearing": 3.0, "standard": 1.0, "minor": 0.3}
# A score floor keeps load-bearing items ranked above aged standard items
# without exempting them from the budget. Previously load_bearing bypassed
# filtering entirely, which made the rendered document unbounded in project
# age — the single largest cause of narrative bloat.
IMPORTANCE_FLOORS = {"load_bearing": 1.0, "standard": 0.0, "minor": 0.0}
RENDER_THRESHOLD = 0.5  # standard/minor below this dissolve.
# Renders an open cascade review must survive before the integrity footer
# announces it as a backlog (§3 shared constants).
REVIEW_TTL_RENDERS = 5
STALE_SCORE_THRESHOLD = 0.3  # load_bearing items below this get a stale callout.

# Optional per-item `value` (0.0-1.0) from the delta-extractor, ordering items
# *within* an importance tier. The bucket is the coarse class an LLM can grade
# reliably; the float is the fine ordering it can only give relatively. Without
# it, ranking inside a tier falls back to pure recency — which is backwards for
# load-bearing items, where the oldest are often the most foundational.
# Neutral default reproduces pre-value behaviour exactly, so absent values on
# existing items need no migration.
VALUE_NEUTRAL = 0.5
VALUE_SPREAD = 1.0  # multiplier ranges over [1-VALUE_SPREAD/2, 1+VALUE_SPREAD/2]

# Token budgets per elastic section. Rough char→token ratio is fine here:
# the budget is a ceiling, not an accounting record.
CHARS_PER_TOKEN = 4
# The soft budget is a target, not a wall. Between soft and hard, only items
# scoring above SOFT_OVERFLOW_SCORE get in — so a section carrying unusually
# valuable material can run over, and a section full of filler cannot.
HARD_BUDGET_MULTIPLIER = 1.5
SOFT_OVERFLOW_SCORE = 1.5
# Minimum items either side of the cut line to hand back for re-valuation, and
# the overall cap on that list so the extractor's input stays bounded.
CONTESTED_WINDOW = 8
CONTESTED_MAX = 40
TIE_EPSILON = 1e-6
SECTION_TOKEN_BUDGETS = {
    "approach": 4500,
    "done": 2000,
    "learnings": 2000,
    # The format spec says never trim goals — closure discipline is meant to
    # bound them. This is a generous backstop for when it doesn't.
    "goals": 2500,
    "suggestions": 1500,
}
# "Operations" is deliberately absent: the format spec exempts it from budget
# pressure, because operational facts change only when infrastructure does.


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        # Handle "Z" suffix and naive isoformat variants.
        s = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _age_days(ts: str | None, now: datetime) -> float | None:
    dt = _parse_ts(ts)
    if dt is None:
        return None
    return max(0.0, (now - dt).total_seconds() / 86400.0)


def _value_multiplier(item: dict) -> float:
    """Scale from the optional per-item `value` float. Absent or malformed
    values are neutral (1.0), so ungraded items rank exactly as they did
    before `value` existed."""
    v = item.get("value")
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return 1.0
    v = float(v)
    # NaN survives isinstance and then poisons every comparison downstream,
    # silently reordering the whole section. Treat it as ungraded.
    if not math.isfinite(v):
        return 1.0
    v = min(1.0, max(0.0, v))
    return 1.0 + (v - VALUE_NEUTRAL) * VALUE_SPREAD


def _score(item: dict, now: datetime) -> float:
    imp = item.get("importance") or "standard"
    mult = _value_multiplier(item)
    weight = IMPORTANCE_WEIGHTS.get(imp, 1.0) * mult
    # The floor scales too — otherwise every aged load-bearing item pins to
    # the same value and ordering inside the tier collapses back to a tie.
    floor = IMPORTANCE_FLOORS.get(imp, 0.0) * mult
    age = _age_days(item.get("last_touched_at"), now)
    if age is None:
        # No timestamp — assume fresh-ish so new items don't dissolve on a
        # state that hasn't been migrated yet.
        return max(weight, floor)
    return max(weight * math.exp(-age / HALF_LIFE_DAYS), floor)


def _raw_score(item: dict, now: datetime) -> float:
    """Decay score ignoring the importance floor. Used by the stale callout,
    which exists precisely to surface load-bearing items whose real
    (unfloored) relevance has decayed."""
    imp = item.get("importance") or "standard"
    weight = IMPORTANCE_WEIGHTS.get(imp, 1.0)
    age = _age_days(item.get("last_touched_at"), now)
    if age is None:
        return weight
    return weight * math.exp(-age / HALF_LIFE_DAYS)


def _active(items: list) -> list:
    return [i for i in items if i.get("status") == "active"]


def _ordered_sessions(state: dict) -> list:
    sessions = [s for s in state.get("sessions", []) if s.get("status") == "active"]
    return sorted(sessions, key=lambda s: s.get("started", ""))


def _partition_by_score(items: list, now: datetime) -> tuple[list, list, int]:
    """Return (primary, secondary, dissolved_count).
    - primary: load_bearing items (score-floored, so always above threshold)
    - secondary: standard items with score >= threshold
    - dissolved_count: aged-out standard items + all minor items

    Eligibility only. The caller still has to fit the result inside a token
    budget via _trim_to_budget — being load_bearing buys rank, not exemption.
    """
    primary = []
    secondary = []
    dissolved = 0
    for item in items:
        imp = item.get("importance") or "standard"
        s = _score(item, now)
        if imp == "load_bearing":
            primary.append((item, s))
        elif imp == "standard" and s >= RENDER_THRESHOLD:
            secondary.append((item, s))
        else:
            dissolved += 1
    # Sort each group by score descending so most-relevant shows first.
    primary.sort(key=lambda x: -x[1])
    secondary.sort(key=lambda x: -x[1])
    return [i for i, _ in primary], [i for i, _ in secondary], dissolved


def _trim_to_budget(groups: list[list], fmt, section: str,
                    now: datetime | None = None,
                    report: dict | None = None) -> tuple[list[list], int]:
    """Fit already-ranked groups of items into the section's token budget.

    `groups` is a list of item lists, consumed in order (e.g. load-bearing
    first, then standard). Returns (kept_groups, dropped_count) with the same
    group arity, so callers keep their subsection headings.

    The budget is soft. Up to the soft target anything ranked in gets in; from
    there to the hard ceiling only items scoring above SOFT_OVERFLOW_SCORE do.
    A section carrying unusually valuable material can run over; a section of
    filler cannot. Nothing crosses the hard ceiling.

    Walks in rank order and stops at the first item that won't fit — it does
    not skip ahead to squeeze in a shorter lower-ranked item, so what renders
    is always a clean prefix of the ranking. Since groups arrive sorted by
    descending score, an item rejected on score means every item after it
    would be too. Always keeps at least one item, so an over-long top item
    can't blank the section.

    When `report` is passed, records the items straddling the cut line under
    `report[section]` — that's the input to the contested-item re-valuation
    pass, which asks the extractor to re-grade only what the budget actually
    had to decide between.
    """
    soft = SECTION_TOKEN_BUDGETS.get(section, 0) * CHARS_PER_TOKEN
    if not soft:
        return groups, 0
    hard = int(soft * HARD_BUDGET_MULTIPLIER)
    now = now or datetime.now(timezone.utc)

    kept: list[list] = []
    kept_flat: list[dict] = []
    dropped_flat: list[dict] = []
    spent = 0
    overflowed = False
    for group in groups:
        kept_group = []
        for item in group:
            if overflowed:
                dropped_flat.append(item)
                continue
            cost = len(fmt(item)) + 1
            fits = (
                not kept_flat                                   # always keep one
                or spent + cost <= soft                         # inside soft target
                or (spent + cost <= hard                        # soft overflow, if
                    and _score(item, now) >= SOFT_OVERFLOW_SCORE)  # it earns it
            )
            if not fits:
                overflowed = True
                dropped_flat.append(item)
                continue
            kept_group.append(item)
            kept_flat.append(item)
            spent += cost
        kept.append(kept_group)

    if report is not None and dropped_flat:
        report[section] = {
            "kept": len(kept_flat),
            "dropped": len(dropped_flat),
            "spent_tokens": spent // CHARS_PER_TOKEN,
            "soft_tokens": soft // CHARS_PER_TOKEN,
            "contested": _contested_slice(kept_flat, dropped_flat, now),
        }

    return kept, len(dropped_flat)


def _contested_slice(kept_flat: list, dropped_flat: list, now: datetime) -> list[dict]:
    """The items the ranking could not actually separate at the cut line.

    A fixed window either side would miss the real pathology: when many items
    share the same floored score, the ranking carries no information and which
    side of the line they landed on is arbitrary. So take the whole tie band
    at the boundary — usually a handful, but on a ledger that has never been
    valued it's the entire over-graded tier, which is exactly what needs
    grading. It shrinks on its own as values populate.

    A minimum window either side is always included, so the extractor sees the
    neighbours it's ranking against even when the band is one-sided.
    """
    if not dropped_flat:
        return []
    boundary = _score(kept_flat[-1], now) if kept_flat else _score(dropped_flat[0], now)
    half = CONTESTED_MAX // 2

    def select(items: list, window: list) -> list:
        tied = [i for i in items if abs(_score(i, now) - boundary) <= TIE_EPSILON]
        seen, out = set(), []
        for item in tied + window:
            key = id(item) if item.get("id") is None else item["id"]
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out[:half]

    kept_sel = select(kept_flat, kept_flat[-CONTESTED_WINDOW:])
    drop_sel = select(dropped_flat, dropped_flat[:CONTESTED_WINDOW])
    return ([_contested_entry(i, now, "kept") for i in kept_sel]
            + [_contested_entry(i, now, "dropped") for i in drop_sel])


def _contested_entry(item: dict, now: datetime, outcome: str) -> dict:
    return {
        "id": item.get("id"),
        "text": (item.get("text") or "")[:200],
        "importance": item.get("importance") or "standard",
        "value": item.get("value"),
        "score": round(_score(item, now), 3),
        "outcome": outcome,
    }


def _tail_lines(path: Path, n: int) -> str:
    if not path.exists():
        return f"(conversation file not found at {path})"
    text = path.read_text(errors="replace")
    lines = text.splitlines()
    return "\n".join(lines[-n:])


def _render_summary(s: dict) -> str:
    parts = ["## The Idea\n"]
    if s.get("what"):
        parts.append(s["what"])
    if s.get("why"):
        parts.append(s["why"])
    if s.get("stack"):
        parts.append(f"**Stack.** {s['stack']}")
    if s.get("scope"):
        parts.append(f"**Scope.** {s['scope']}")
    return "\n\n".join(parts) + "\n"


def _render_approach(decisions: list, state: dict | None = None, now: datetime | None = None,
                     report: dict | None = None) -> str:
    out = ["## Approach\n"]
    active = _active(decisions)
    if not active:
        out.append("_No active decisions yet._")
        return "\n".join(out) + "\n"

    state = state or {}
    now = now or datetime.now(timezone.utc)

    def _fmt_row(d: dict) -> str:
        text = d.get("text", "").replace("|", "\\|").replace("\n", " ")
        rationale = d.get("rationale", "").replace("|", "\\|").replace("\n", " ")
        if d.get("quote"):
            q = d["quote"].replace("|", "\\|").replace("\n", " ")
            rationale = f"{rationale} Scott: \"{q}\""
        if d.get("_suspect_note"):  # NEW, disposition C3 — ephemeral, copy-on-write only (§9.1)
            rationale = f"{rationale}{d['_suspect_note']}"
        return f"| {text} | {rationale} |"

    primary, secondary, dissolved = _partition_by_score(active, now)
    (primary, secondary), over_budget = _trim_to_budget(
        [primary, secondary], _fmt_row, "approach", now, report
    )
    dissolved += over_budget

    if primary or secondary:
        out.append("| Decision | Rationale |")
        out.append("|----------|-----------|")
        for d in primary + secondary:
            out.append(_fmt_row(d))
    else:
        out.append("_No load-bearing or currently-active decisions._")

    if dissolved:
        project = state.get("project", "project")
        out.append("")
        out.append(
            f"_{dissolved} older decision(s) and minor conventions "
            f"dissolved — use `project_lookup(project='{project}')` for "
            f"drill-down._"
        )

    return "\n".join(out) + "\n"


def _render_operations(ops: list) -> str:
    out = ["## Operations\n"]
    if not ops:
        out.append("_No operations info yet._")
        return "\n".join(out) + "\n"
    out.append("| Item | Detail |")
    out.append("|------|--------|")
    for row in ops:
        item = row.get("item", "").replace("|", "\\|")
        detail = row.get("detail", "").replace("|", "\\|").replace("\n", " ")
        out.append(f"| {item} | {detail} |")
    return "\n".join(out) + "\n"


def _render_done(done: list, state: dict | None = None, now: datetime | None = None,
                 report: dict | None = None) -> str:
    out = ["## What's Done\n"]
    active = _active(done)
    state = state or {}
    if not active:
        # "No active items" is not "nothing shipped" — an audit sweep can
        # archive an entire build log. Point at the archive instead of
        # asserting the project has produced nothing.
        #
        # Three-way, not count-and-subtract (§9.2, disposition #15): a
        # render-time quarantine is reversible and must never be reported as
        # an archive. `len(done) - len(active)` conflated the two, so a
        # withheld item would have been announced as permanently archived.
        archived = sum(1 for d in done if d.get("status") == "archived")
        quarantined = sum(1 for d in done if d.get("status") == "quarantined")
        project = state.get("project", "project")
        if archived:
            out.append(
                f"_{archived} work item(s) archived — use "
                f"`project_lookup(project='{project}')` for drill-down._"
            )
        elif quarantined:
            out.append(
                f"_{quarantined} work item(s) withheld pending review — see "
                f"the render certificate (`{project}.certificate.json`)._"
            )
        else:
            out.append("_No work items recorded yet._")
        return "\n".join(out) + "\n"

    now = now or datetime.now(timezone.utc)

    def _fmt_line(w: dict) -> str:
        text = w.get("text", "")
        commit = w.get("commit")
        line = f"- {text}"
        if commit:
            line += f" (`{commit}`)"
        if w.get("_suspect_note"):  # NEW, disposition C3 — ephemeral, copy-on-write only (§9.1)
            line += w["_suspect_note"]
        return line

    primary, secondary, dissolved = _partition_by_score(active, now)
    (primary, secondary), over_budget = _trim_to_budget(
        [primary, secondary], _fmt_line, "done", now, report
    )
    dissolved += over_budget

    if primary:
        out.append("### Foundations & current state")
        out.append("")
        for w in primary:
            out.append(_fmt_line(w))
        out.append("")

    if secondary:
        out.append("### Recent work")
        out.append("")
        for w in secondary:
            out.append(_fmt_line(w))
        out.append("")

    if dissolved:
        project = state.get("project", "project")
        out.append(
            f"_{dissolved} earlier work item(s) dissolved — use "
            f"`project_lookup(project='{project}')` for drill-down._"
        )

    return "\n".join(out) + "\n"


def _render_learnings(learnings: list, state: dict | None = None, now: datetime | None = None,
                      report: dict | None = None) -> str:
    out = ["## What We've Learnt\n"]
    active = _active(learnings)
    if not active:
        out.append("_No learnings captured yet._")
        return "\n".join(out) + "\n"

    state = state or {}
    now = now or datetime.now(timezone.utc)

    def _fmt(l: dict) -> str:
        text = l.get("text", "")
        line = f"- **{text}**"
        if l.get("evidence"):
            ev = l["evidence"] if isinstance(l["evidence"], str) else "; ".join(l["evidence"])
            line += f" — {ev}"
        if l.get("_suspect_note"):  # NEW, disposition C3 — ephemeral, copy-on-write only (§9.1)
            line += l["_suspect_note"]
        return line

    primary, secondary, dissolved = _partition_by_score(active, now)
    (primary, secondary), over_budget = _trim_to_budget(
        [primary, secondary], _fmt, "learnings", now, report
    )
    dissolved += over_budget

    for l in primary + secondary:
        out.append(_fmt(l))

    if dissolved:
        project = state.get("project", "project")
        out.append("")
        out.append(
            f"_{dissolved} older learning(s) dissolved — use "
            f"`project_lookup(project='{project}')` "
            f"for drill-down._"
        )

    return "\n".join(out) + "\n"


def _stale_callout(items: list, label: str, now: datetime | None = None) -> list[str]:
    """Return lines for a stale callout over load_bearing items whose
    decay score has fallen below STALE_SCORE_THRESHOLD — they're flagged
    as still-rendering-but-probably-worth-review."""
    now = now or datetime.now(timezone.utc)
    stale = []
    for i in items:
        if (i.get("importance") or "standard") != "load_bearing":
            continue
        if _raw_score(i, now) >= STALE_SCORE_THRESHOLD:
            continue
        stale.append(i)
    if not stale:
        return []
    lines = [""]
    lines.append(
        f"> ⚠ **Stale callout:** {len(stale)} load-bearing {label}"
        f"{'s' if len(stale) != 1 else ''} haven't been touched in a while — "
        f"review: still valid (leave), obsolete (archive), or already done "
        f"elsewhere (archive)?"
    )
    lines.append(">")
    for i in stale:
        age = _age_days(i.get("last_touched_at"), now)
        age_str = f"{age:.0f}d" if age is not None else "unknown age"
        lines.append(f"> - `{i.get('id')}` ({age_str}): {i.get('text', '')[:100]}")
    return lines


def _render_goals(goals: list, state: dict | None = None, now: datetime | None = None,
                  report: dict | None = None) -> str:
    out = ["## What We Want To Do\n"]
    active = _active(goals)
    if not active:
        out.append("_No open goals._")
        return "\n".join(out) + "\n"

    state = state or {}
    now = now or datetime.now(timezone.utc)

    def _fmt(g: dict) -> str:
        line = g.get("text", "")
        if g.get("progress"):
            line += f" — _{g['progress']}_"
        if g.get("_suspect_note"):  # NEW, disposition C3 — ephemeral, copy-on-write only (§9.1)
            line += g["_suspect_note"]
        return line

    # The format spec exempts goals from decay — closure discipline is meant
    # to bound them. They're ranked anyway so that if the budget backstop
    # does fire, it drops the least-relevant goals rather than arbitrary ones.
    ranked = sorted(active, key=lambda g: -_score(g, now))
    (kept,), over_budget = _trim_to_budget([ranked], _fmt, "goals", now, report)

    for i, g in enumerate(kept, 1):
        out.append(f"{i}. {_fmt(g)}")

    if over_budget:
        project = state.get("project", "project")
        out.append("")
        out.append(
            f"_{over_budget} further open goal(s) over section budget — see "
            f"`project_lookup(project='{project}')`. Goals aren't "
            f"meant to dissolve; this many open suggests closure is lagging._"
        )

    out.extend(_stale_callout(kept, "goal", now))
    return "\n".join(out) + "\n"


def _render_suggestions(suggestions: list, state: dict | None = None, now: datetime | None = None,
                        report: dict | None = None) -> str:
    out = ["## Suggested Work\n"]
    active = _active(suggestions)
    if not active:
        out.append("_No pending suggestions._")
        return "\n".join(out) + "\n"

    state = state or {}
    now = now or datetime.now(timezone.utc)

    def _fmt(s: dict) -> str:
        who = s.get("originator", "claude")
        line = f"- ({who}) {s.get('text', '')}"
        if s.get("_suspect_note"):  # NEW, disposition C3 — ephemeral, copy-on-write only (§9.1)
            line += s["_suspect_note"]
        return line

    # Suggestions are the one section the spec explicitly wants to dissolve
    # ("after 3 narrative cycles if not acted on"), so decay applies here.
    primary, secondary, dissolved = _partition_by_score(active, now)
    (primary, secondary), over_budget = _trim_to_budget(
        [primary, secondary], _fmt, "suggestions", now, report
    )
    dissolved += over_budget

    kept = primary + secondary
    for s in kept:
        out.append(_fmt(s))

    if dissolved:
        project = state.get("project", "project")
        out.append("")
        out.append(
            f"_{dissolved} unacted suggestion(s) dissolved — use "
            f"`project_lookup(project='{project}')`._"
        )

    out.extend(_stale_callout(kept, "suggestion", now))
    return "\n".join(out) + "\n"


def _display_session_id(session_id: str) -> str:
    """Short display form of a session id.

    Bare `session_id[:8]` collides for a prefixed client: every codex thread
    id begins `019…` after the `codex-` prefix, so every codex session
    rendered as `codex-01` — indistinguishable in the two places a human
    reads session ids, while claude's unprefixed ids stayed distinct. Strip
    the known prefix, truncate what remains, and show the client separately
    — keeps ids distinct without losing the provenance the prefix carried.
    """
    sid = session_id or ""
    lowered = sid.lower()
    for prefix, client in adapters.prefixes().items():
        if lowered.startswith(prefix.lower()):
            return f"{client}-{sid[len(prefix):][:8]}"
    return sid[:8]


def _render_resuming(state: dict) -> str:
    """Lean pointer only. Full resume content (journal + conversation tail)
    is demand-loaded via the `resume` MCP tool — keeps the narrative tight
    and lets agents/subagents skip loading it."""
    out = ["## Resuming\n"]
    sessions = [s for s in _ordered_sessions(state)
                if not (s.get("session_id") or "").startswith(("audit-", "agent-"))]
    if not sessions:
        out.append("_No sessions yet._")
        return "\n".join(out) + "\n"
    last = sessions[-1]

    explicit_status = last.get("closure_status")
    if explicit_status in ("complete", "interrupted"):
        status = explicit_status
    else:
        applied = last.get("ledger_delta_applied", {}).get("introduced", {})
        last_intro_ids = set(applied.get("goals", []) or []) | set(applied.get("suggestions", []) or [])
        active_goal_ids = {g.get("id") for g in _active(state.get("goals", []))}
        active_sug_ids = {s.get("id") for s in _active(state.get("suggestions", []))}
        any_open = any(
            gid in active_goal_ids or gid in active_sug_ids
            for gid in last_intro_ids
        )
        status = "interrupted" if any_open else "complete"

    sid = last.get("session_id", "")
    ended = (last.get("ended") or "")[:10] or "?"
    project = state.get("project", "?")
    out.append(
        f"Last real session `{_display_session_id(sid)}` ended {ended} — status: `{status}`. "
        f"To pick up where it left off, call "
        f"`resume(project=\"{project}\")` — returns the session's journal "
        f"and conversation tail on demand. Not loaded by default."
    )
    return "\n".join(out) + "\n"


def _render_source_transcripts(state: dict) -> str:
    out = ["## Source Transcripts\n"]
    sessions = _ordered_sessions(state)
    if not sessions:
        out.append("_No sessions._")
        return "\n".join(out) + "\n"
    # Last 10 annotated, older summarised.
    show_last = 10
    recent = sessions[-show_last:]
    older_count = len(sessions) - len(recent)

    out.append("| Date | Session | Topic |")
    out.append("|------|---------|-------|")
    for s in recent:
        started = (s.get("started") or "")[:10]
        sid = _display_session_id(s.get("session_id", ""))
        topic = (s.get("topic") or "").replace("|", "\\|")
        out.append(f"| {started} | `{sid}` | {topic} |")

    if older_count > 0:
        project = state.get("project", "?")
        project_path = memory_root() / "projects" / f"{project}.json"
        out.append("")
        out.append(f"_{older_count} earlier session(s) dissolved into the ledger above._ "
                   f"Full list and drill-down available in `{project_path}` "
                   f"or via `narrative_coverage(project='{project}')`.")
    return "\n".join(out) + "\n"


def render_with_report(state: dict, now: datetime | None = None) -> tuple[str, dict]:
    """Render the narrative and report which sections hit their budget.

    The report is the input to the contested-item re-valuation pass: it names
    the items the budget actually had to choose between, so the next
    /narrative run can ask the extractor to re-grade just those rather than
    re-auditing the whole ledger.
    """
    now = now or datetime.now(timezone.utc)
    # --- certification (§9.1) -------------------------------------------
    # Reversible and read-only: `state` is never mutated. Everything below
    # renders from `state_r`, a copy-on-write view carrying the quarantine
    # status overrides and the ephemeral `_suspect_note` annotations.
    eligible = certify.eligible_item_ids(state)
    cert = certify.evaluate(state, eligible, now=now)
    qset = certify.quarantine_set(cert)
    suspect_by_child = certify.suspect_callouts(cert)
    state_r = certify.copy_with_status_override(state, qset, suspect_by_child)
    # ---------------------------------------------------------------------
    sections: dict = {}
    parts = [f"# {state_r.get('project', 'Project')} — Project Narrative\n"]
    parts.append(_render_summary(state_r.get("summary", {})))
    parts.append(_render_approach(state_r.get("decisions", []), state_r, now, sections))
    parts.append(_render_operations(state_r.get("operations", [])))
    parts.append(_render_done(state_r.get("done", []), state_r, now, sections))
    parts.append(_render_learnings(state_r.get("learnings", []), state_r, now, sections))
    parts.append(_render_goals(state_r.get("goals", []), state_r, now, sections))
    parts.append(_render_suggestions(state_r.get("suggestions", []), state_r, now, sections))
    parts.append(_render_resuming(state_r))
    parts.append(_render_source_transcripts(state_r))
    md = "\n".join(parts)
    # Unconditional (§9.1, disposition C6). `_integrity_footer` already
    # returns "" when there is nothing to show, so gating the call was always
    # redundant — and wrong: it silently skipped the backlog line on a
    # clean-but-aged-backlog render. The two footer lines are independent.
    md += _integrity_footer(cert)
    status_path = memory_root() / "projects" / f"{state_r.get('project')}.extraction-status.json"
    try:
        extraction_status = json.loads(status_path.read_text())
    except (OSError, json.JSONDecodeError):
        extraction_status = {}
    waiting = int(extraction_status.get("unprocessed", 0)) + int(extraction_status.get("stale", 0))
    if extraction_status.get("state") in ("waiting", "failed") and waiting:
        md += f"\n> ⚠ Narrative pipeline: {waiting} session(s) waiting — see {status_path}\n"
    report = {
        "project": state.get("project"),
        "rendered_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_tokens": len(md) // CHARS_PER_TOKEN,
        "sections": sections,
        "certificate": cert.to_dict(),
    }
    return md, report


def _integrity_footer(cert) -> str:
    """Two INDEPENDENT lines (disposition C6): a withheld-items line and an
    aged-review-backlog line. Either can fire without the other. Returns ""
    when neither does, so the caller never has to gate this."""
    q = cert.counts["quarantined"]
    backlog = cert.resolution_backlog
    lines = []
    if q:
        lines.append(f"> ⚠ Integrity: {q} contradicted item(s) withheld pending review.")
    if backlog["open_reviews"] and backlog["oldest_render_age"] >= REVIEW_TTL_RENDERS:
        lines.append(f"> {backlog['open_reviews']} cascade review(s) await confirmation, "
                     f"oldest open for {backlog['oldest_render_age']} render(s).")
    if not lines:
        return ""
    return "\n" + "\n".join(lines) + f"\n> See `{cert.project}.certificate.json`.\n"


def render(state: dict, now: datetime | None = None) -> str:
    return render_with_report(state, now=now)[0]


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: renderer.py <project_json_path> <output_md_path>", file=sys.stderr)
        sys.exit(1)
    state_path = Path(sys.argv[1])
    project = state_path.stem
    try:
        lock = project_lock(memory_root(), project)
        lock.__enter__()
    except NarrativeLockBusy:
        print(f"LLM_MEMORY_WARN: narrative update already running for {project}; retry after it finishes")
        sys.exit(3)
    try:
        _main_locked(state_path, Path(sys.argv[2]))
    finally:
        lock.__exit__(None, None, None)


def _main_locked(state_path: Path, output_path: Path) -> None:
    state = json.loads(state_path.read_text())
    md, report = render_with_report(state)
    output_path.write_text(md)

    # Sidecar next to the state JSON. Written only when something was cut, and
    # removed when nothing is, so its presence is the signal that a
    # re-valuation pass has work to do.
    contested_path = state_path.with_suffix(".contested.json")
    if report["sections"]:
        contested_path.write_text(json.dumps(report, indent=2))
    elif contested_path.exists():
        contested_path.unlink()

    # Certificate sidecar (§9.3) — ALWAYS written, never deleted. Unlike
    # .contested.json, its absence must never be readable as "clean": a
    # missing certificate means the render never certified, which is a
    # different and worse thing than certifying and finding nothing.
    cert_path = state_path.with_suffix(".certificate.json")
    cert_path.write_text(json.dumps(report["certificate"], indent=2))
    verdict = report["certificate"]["verdict"]

    over = ", ".join(
        f"{name} {s['spent_tokens']}/{s['soft_tokens']} (-{s['dropped']})"
        for name, s in report["sections"].items()
    )
    print(f"Rendered {len(md)} chars (~{report['total_tokens']} tokens, "
          f"{len(md.splitlines())} lines) to {output_path}")
    if over:
        print(f"  at budget: {over}")
        print(f"  contested items written to {contested_path}")

    # Exit 2 means "artifacts written, integrity work remains" — NOT a failed
    # render. It fires only after every artifact is on disk, and /narrative
    # must treat it as continue, not abort. A non-empty not_checked is a
    # standing scope statement, not a finding, so it never gates this.
    if verdict in ("CONTRADICTION", "UNCERTIFIED"):
        sys.exit(2)


if __name__ == "__main__":
    main()
