"""
Renderer — takes a project state JSON and produces the 9-section narrative
markdown. Pure code, no LLM. Deterministic view over status=active items.

Usage:
    python renderer.py <project_json_path> <output_md_path>

The resume_excerpt field on the most recent session is derived from the
tail of its conversation.md at render time (not stored in the JSON).

Rendering uses a decay-score filter: each item's relevance is
importance_weight × exp(-age_in_days / half_life). Load-bearing items
always render (high weight floor); standard items render while recent;
minor items never render individually.
"""

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()

# How many lines of dialogue to include as the resume_excerpt on the most
# recent active session. Pulled fresh from the conversation.md file.
DEFAULT_RESUME_LINES = 150

# Decay parameters. Tunables; see architecture-redesign-2026-04-18.md.
HALF_LIFE_DAYS = 30.0
IMPORTANCE_WEIGHTS = {"load_bearing": 3.0, "standard": 1.0, "minor": 0.3}
RENDER_THRESHOLD = 0.5  # standard/minor below this dissolve; load_bearing always renders.
STALE_SCORE_THRESHOLD = 0.3  # load_bearing items below this get a stale callout.


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


def _score(item: dict, now: datetime) -> float:
    imp = item.get("importance") or "standard"
    weight = IMPORTANCE_WEIGHTS.get(imp, 1.0)
    age = _age_days(item.get("last_touched_at"), now)
    if age is None:
        # No timestamp — assume fresh-ish so new items don't dissolve on a
        # state that hasn't been migrated yet.
        return weight
    return weight * math.exp(-age / HALF_LIFE_DAYS)


def _active(items: list) -> list:
    return [i for i in items if i.get("status") == "active"]


def _ordered_sessions(state: dict) -> list:
    sessions = [s for s in state.get("sessions", []) if s.get("status") == "active"]
    return sorted(sessions, key=lambda s: s.get("started", ""))


def _partition_by_score(items: list, now: datetime) -> tuple[list, list, int]:
    """Return (always_render, standard_rendered, dissolved_count).
    - always_render: load_bearing items, regardless of score
    - standard_rendered: standard items with score >= threshold
    - dissolved_count: everything else (aged-out standard + all minor + aged-out load_bearing, though load_bearing always renders so it's only the first two)
    """
    always = []
    standard_rendered = []
    dissolved = 0
    for item in items:
        imp = item.get("importance") or "standard"
        s = _score(item, now)
        if imp == "load_bearing":
            always.append((item, s))
        elif imp == "standard" and s >= RENDER_THRESHOLD:
            standard_rendered.append((item, s))
        else:
            dissolved += 1
    # Sort each group by score descending so most-relevant shows first.
    always.sort(key=lambda x: -x[1])
    standard_rendered.sort(key=lambda x: -x[1])
    return [i for i, _ in always], [i for i, _ in standard_rendered], dissolved


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


def _render_approach(decisions: list, state: dict | None = None, now: datetime | None = None) -> str:
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
        return f"| {text} | {rationale} |"

    always, standard_rendered, dissolved = _partition_by_score(active, now)

    if always or standard_rendered:
        out.append("| Decision | Rationale |")
        out.append("|----------|-----------|")
        for d in always + standard_rendered:
            out.append(_fmt_row(d))
    else:
        out.append("_No load-bearing or currently-active decisions._")

    if dissolved:
        project = state.get("project", "project")
        out.append("")
        out.append(
            f"_{dissolved} older decision(s) and minor conventions "
            f"dissolved — see `{project}.json` `decisions[]` or use "
            f"`project_lookup` for drill-down._"
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


def _render_done(done: list, state: dict | None = None, now: datetime | None = None) -> str:
    out = ["## What's Done\n"]
    active = _active(done)
    if not active:
        out.append("_Nothing shipped yet._")
        return "\n".join(out) + "\n"

    state = state or {}
    now = now or datetime.now(timezone.utc)

    def _fmt_line(w: dict) -> str:
        text = w.get("text", "")
        commit = w.get("commit")
        line = f"- {text}"
        if commit:
            line += f" (`{commit}`)"
        return line

    always, standard_rendered, dissolved = _partition_by_score(active, now)

    if always:
        out.append("### Foundations & current state")
        out.append("")
        for w in always:
            out.append(_fmt_line(w))
        out.append("")

    if standard_rendered:
        out.append("### Recent work")
        out.append("")
        for w in standard_rendered:
            out.append(_fmt_line(w))
        out.append("")

    if dissolved:
        project = state.get("project", "project")
        out.append(
            f"_{dissolved} earlier work item(s) dissolved — see "
            f"`{project}.json` `done[]` or use `project_lookup` for drill-down._"
        )

    return "\n".join(out) + "\n"


def _render_learnings(learnings: list, state: dict | None = None, now: datetime | None = None) -> str:
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
        return line

    always, standard_rendered, dissolved = _partition_by_score(active, now)

    for l in always + standard_rendered:
        out.append(_fmt(l))

    if dissolved:
        project = state.get("project", "project")
        out.append("")
        out.append(
            f"_{dissolved} older learning(s) dissolved — see "
            f"`{project}.json` `learnings[]` or use `project_lookup` "
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
        if _score(i, now) >= STALE_SCORE_THRESHOLD:
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


def _render_goals(goals: list, now: datetime | None = None) -> str:
    out = ["## What We Want To Do\n"]
    active = _active(goals)
    if not active:
        out.append("_No open goals._")
        return "\n".join(out) + "\n"
    for i, g in enumerate(active, 1):
        text = g.get("text", "")
        line = f"{i}. {text}"
        if g.get("progress"):
            line += f" — _{g['progress']}_"
        out.append(line)
    out.extend(_stale_callout(active, "goal", now))
    return "\n".join(out) + "\n"


def _render_suggestions(suggestions: list, now: datetime | None = None) -> str:
    out = ["## Suggested Work\n"]
    active = _active(suggestions)
    if not active:
        out.append("_No pending suggestions._")
        return "\n".join(out) + "\n"
    for s in active:
        text = s.get("text", "")
        who = s.get("originator", "claude")
        line = f"- ({who}) {text}"
        out.append(line)
    out.extend(_stale_callout(active, "suggestion", now))
    return "\n".join(out) + "\n"


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
        f"Last real session `{sid[:8]}` ended {ended} — status: `{status}`. "
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
        sid = s.get("session_id", "")[:8]
        topic = (s.get("topic") or "").replace("|", "\\|")
        out.append(f"| {started} | `{sid}` | {topic} |")

    if older_count > 0:
        out.append("")
        out.append(f"_{older_count} earlier session(s) dissolved into the ledger above._ "
                   f"Full list and drill-down available in `~/.claude/memory/projects/{state.get('project','?')}.json` "
                   f"or via `narrative_coverage(project='{state.get('project','?')}')`.")
    return "\n".join(out) + "\n"


def render(state: dict) -> str:
    now = datetime.now(timezone.utc)
    parts = [f"# {state.get('project', 'Project')} — Project Narrative\n"]
    parts.append(_render_summary(state.get("summary", {})))
    parts.append(_render_approach(state.get("decisions", []), state, now))
    parts.append(_render_operations(state.get("operations", [])))
    parts.append(_render_done(state.get("done", []), state, now))
    parts.append(_render_learnings(state.get("learnings", []), state, now))
    parts.append(_render_goals(state.get("goals", []), now))
    parts.append(_render_suggestions(state.get("suggestions", []), now))
    parts.append(_render_resuming(state))
    parts.append(_render_source_transcripts(state))
    return "\n".join(parts)


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: renderer.py <project_json_path> <output_md_path>", file=sys.stderr)
        sys.exit(1)
    state = json.loads(Path(sys.argv[1]).read_text())
    md = render(state)
    Path(sys.argv[2]).write_text(md)
    print(f"Rendered {len(md)} chars ({len(md.splitlines())} lines) to {sys.argv[2]}")


if __name__ == "__main__":
    main()
