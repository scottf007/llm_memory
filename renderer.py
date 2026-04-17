"""
Renderer — takes a project state JSON and produces the 9-section narrative
markdown. Pure code, no LLM. Deterministic view over status=active items.

Usage:
    python renderer.py <project_json_path> <output_md_path>

The resume_excerpt field on the most recent session is derived from the
tail of its conversation.md at render time (not stored in the JSON).
"""

import json
import sys
from pathlib import Path

HOME = Path.home()

# How many lines of dialogue to include as the resume_excerpt on the most
# recent active session. Pulled fresh from the conversation.md file.
DEFAULT_RESUME_LINES = 150

# Threshold for surfacing stale suggestions / goals in the rendered narrative.
# An item with cycles_pending >= this shows up in a callout for review.
STALE_CYCLES_THRESHOLD = 5


def _active(items: list) -> list:
    return [i for i in items if i.get("status") == "active"]


def _ordered_sessions(state: dict) -> list:
    sessions = [s for s in state.get("sessions", []) if s.get("status") == "active"]
    return sorted(sessions, key=lambda s: s.get("started", ""))


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


def _render_approach(decisions: list) -> str:
    out = ["## Approach\n"]
    active = _active(decisions)
    if not active:
        out.append("_No active decisions yet._")
        return "\n".join(out) + "\n"

    # Split by importance. Missing importance -> "standard" (legacy).
    main = []
    minor = []
    for d in active:
        imp = d.get("importance") or "standard"
        if imp == "minor":
            minor.append(d)
        else:
            main.append(d)

    def _fmt_row(d: dict) -> str:
        text = d.get("text", "").replace("|", "\\|").replace("\n", " ")
        rationale = d.get("rationale", "").replace("|", "\\|").replace("\n", " ")
        if d.get("quote"):
            q = d["quote"].replace("|", "\\|").replace("\n", " ")
            rationale = f"{rationale} Scott: \"{q}\""
        return f"| {text} | {rationale} |"

    if main:
        out.append("| Decision | Rationale |")
        out.append("|----------|-----------|")
        for d in main:
            out.append(_fmt_row(d))
    else:
        out.append("_No load-bearing or standard decisions — see minor decisions below._")

    if minor:
        out.append("")
        out.append("<details>")
        out.append("<summary>Minor decisions / conventions</summary>")
        out.append("")
        out.append("| Decision | Rationale |")
        out.append("|----------|-----------|")
        for d in minor:
            out.append(_fmt_row(d))
        out.append("")
        out.append("</details>")

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


def _render_done(done: list, state: dict | None = None) -> str:
    out = ["## What's Done\n"]
    active = _active(done)
    if not active:
        out.append("_Nothing shipped yet._")
        return "\n".join(out) + "\n"

    # Order sessions so we can identify the last 2 for "Recent work".
    # Anything older than 2 sessions dissolves into the "Earlier work" line
    # unless it's tagged load_bearing (those always render as Foundations).
    state = state or {}
    ordered = _ordered_sessions(state)
    recent_session_ids = {s.get("session_id") for s in ordered[-2:]}
    # Cutoff ordinal = session count minus "recent" window size.
    cutoff_ordinal = max(0, len(ordered) - 2)

    def _fmt_line(w: dict) -> str:
        text = w.get("text", "")
        commit = w.get("commit")
        line = f"- {text}"
        if commit:
            line += f" (`{commit}`)"
        return line

    load_bearing = []
    recent_standard = []
    older_or_minor = []

    for w in active:
        imp = w.get("importance") or "standard"
        # Done items may use completed_in or introduced_in — prefer completed_in.
        sid = w.get("completed_in") or w.get("introduced_in")
        if imp == "load_bearing":
            load_bearing.append(w)
        elif imp == "minor":
            older_or_minor.append(w)
        else:
            # standard
            if sid in recent_session_ids:
                recent_standard.append(w)
            else:
                older_or_minor.append(w)

    if load_bearing:
        out.append("### Foundations & current state")
        out.append("")
        for w in load_bearing:
            out.append(_fmt_line(w))
        out.append("")

    if recent_standard:
        out.append("### Recent work")
        out.append("")
        for w in recent_standard:
            out.append(_fmt_line(w))
        out.append("")

    if older_or_minor:
        project = state.get("project", "project")
        out.append(
            f"_Earlier work: sessions 1-{cutoff_ordinal} landed the v1/v2/v3 "
            f"architecture and multi-device sync — see `{project}.json` "
            f"`done[]` for the full list._"
        )

    # Fallback: if nothing rendered above (e.g. everything fell through), list all.
    if not (load_bearing or recent_standard or older_or_minor):
        for w in active:
            out.append(_fmt_line(w))

    return "\n".join(out) + "\n"


def _render_learnings(learnings: list) -> str:
    out = ["## What We've Learnt\n"]
    active = _active(learnings)
    if not active:
        out.append("_No learnings captured yet._")
        return "\n".join(out) + "\n"
    for l in active:
        text = l.get("text", "")
        line = f"- **{text}**"
        if l.get("evidence"):
            ev = l["evidence"] if isinstance(l["evidence"], str) else "; ".join(l["evidence"])
            line += f" — {ev}"
        out.append(line)
    return "\n".join(out) + "\n"


def _stale_callout(items: list, label: str) -> list[str]:
    """Return lines for a stale callout over items with cycles_pending >=
    STALE_CYCLES_THRESHOLD. Empty list if none."""
    stale = [i for i in items if (i.get("cycles_pending") or 0) >= STALE_CYCLES_THRESHOLD]
    if not stale:
        return []
    lines = [""]
    lines.append(
        f"> ⚠ **Stale callout:** {len(stale)} {label}"
        f"{'s' if len(stale) != 1 else ''} pending "
        f"{STALE_CYCLES_THRESHOLD}+ sessions without movement — review: "
        f"still valid (leave), obsolete (archive), or already done elsewhere (archive)?"
    )
    lines.append(">")
    for i in stale:
        cp = i.get("cycles_pending") or 0
        lines.append(f"> - `{i.get('id')}` ({cp} cycles): {i.get('text', '')[:100]}")
    return lines


def _render_goals(goals: list) -> str:
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
    out.extend(_stale_callout(active, "goal"))
    return "\n".join(out) + "\n"


def _render_suggestions(suggestions: list) -> str:
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
    out.extend(_stale_callout(active, "suggestion"))
    return "\n".join(out) + "\n"


def _render_resuming(state: dict) -> str:
    out = ["## Resuming\n"]
    sessions = _ordered_sessions(state)
    if not sessions:
        out.append("_No sessions yet._")
        return "\n".join(out) + "\n"
    last = sessions[-1]

    # Prefer explicit closure_status from the delta-extractor.
    explicit_status = last.get("closure_status")
    if explicit_status in ("complete", "interrupted"):
        status = explicit_status
    else:
        # Legacy fallback: widened heuristic — any active goal OR active suggestion
        # introduced by this session means interrupted.
        applied = last.get("ledger_delta_applied", {}).get("introduced", {})
        last_intro_ids = set(applied.get("goals", []) or []) | set(applied.get("suggestions", []) or [])
        active_goal_ids = {g.get("id") for g in _active(state.get("goals", []))}
        active_sug_ids = {s.get("id") for s in _active(state.get("suggestions", []))}
        any_open = any(
            gid in active_goal_ids or gid in active_sug_ids
            for gid in last_intro_ids
        )
        status = "interrupted" if any_open else "complete"

    out.append(f"**Status:** `{status}`")
    out.append("")
    journal = last.get("journal", "").strip()
    if journal:
        out.append("**What was happening:**")
        out.append("")
        out.append(journal)
        out.append("")

    # Resume excerpt — read tail of conversation md fresh at render time.
    conv_rel = last.get("conversation_md", "")
    if conv_rel:
        conv_path = Path(conv_rel.replace("~", str(HOME)))
        lines_wanted = last.get("resume_excerpt_lines", DEFAULT_RESUME_LINES) or DEFAULT_RESUME_LINES
        excerpt = _tail_lines(conv_path, lines_wanted)
        out.append("**Last piece of conversation (tail of `" + last.get("session_id", "") + ".md`):**")
        out.append("")
        out.append("```")
        out.append(excerpt)
        out.append("```")

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
    parts = [f"# {state.get('project', 'Project')} — Project Narrative\n"]
    parts.append(_render_summary(state.get("summary", {})))
    parts.append(_render_approach(state.get("decisions", [])))
    parts.append(_render_operations(state.get("operations", [])))
    parts.append(_render_done(state.get("done", []), state))
    parts.append(_render_learnings(state.get("learnings", [])))
    parts.append(_render_goals(state.get("goals", [])))
    parts.append(_render_suggestions(state.get("suggestions", [])))
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
