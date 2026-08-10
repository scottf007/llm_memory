"""
MCP server for persistent Claude Code memory.

Live DB surface is a single FTS5-indexed `items` table (managed by
indexer.py) that projects per-project ledger items for cross-project
search. The canonical state for each project is the JSON ledger at
~/.claude/memory/projects/{project}.json; per-item files under
~/.claude/memory/items/{project}/{kind}/{id}.json are the indexer's
input. memory.db is derived; delete it and re-run indexer.py to rebuild.

Usage:
    python server.py
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DB_DIR = Path.home() / ".claude" / "memory"
DB_PATH = DB_DIR / "memory.db"


def _ensure_stignore() -> None:
    """Create .stignore so Syncthing ignores the derived SQLite DB."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    stignore_path = DB_DIR / ".stignore"
    if not stignore_path.exists():
        stignore_path.write_text("memory.db\nmemory.db-wal\nmemory.db-shm\n")


# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

app = Server("llm-memory")


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="memory_search",
            description="Cross-project fuzzy search over per-project ledger items "
            "(decisions/learnings/done/goals/suggestions). Queries the FTS5 index "
            "built from ~/.claude/memory/items/. Use project_lookup for single-project "
            "drill-down; use memory_search when you don't know which project a fact is in.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query — space-separated keywords",
                    },
                    "project": {
                        "type": "string",
                        "description": "Optional: filter to a single project",
                    },
                    "kind": {
                        "type": "string",
                        "description": "Optional: 'decisions', 'learnings', 'done', 'goals', 'suggestions'",
                    },
                    "status": {
                        "type": "string",
                        "description": "Optional: 'active' (default) or 'archived'",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 20)",
                        "default": 20,
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="narrative_coverage",
            description="Return merged vs unprocessed session transcripts for a project. "
            "Compares on-disk session files against {project}.json.sessions[]. "
            "Sub-agent transcripts (session_id starting with 'agent-') and short "
            "one-shot sessions (fewer than min_user_turns substantive user turns, "
            "default 5) are excluded so the narrative pipeline doesn't burn cycles "
            "on noise like single-prompt SDK calls.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": "Project name, e.g. 'cricket_manager'",
                    },
                    "min_user_turns": {
                        "type": "integer",
                        "description": "Drop transcripts with fewer than this many "
                        "non-sidechain user turns (default 5). Pass 0 to disable.",
                        "default": 5,
                    },
                },
                "required": ["project"],
            },
        ),
        types.Tool(
            name="resume",
            description="Return the last real session's journal and conversation tail for a project. "
            "Call this when picking up where a previous session left off — the narrative itself only "
            "carries a pointer to avoid bloating session_start context. Agents typically don't need this.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": "Project name, e.g. 'llm_memory'",
                    },
                    "lines": {
                        "type": "integer",
                        "description": "Conversation-tail lines to include (default 150)",
                    },
                },
                "required": ["project"],
            },
        ),
        types.Tool(
            name="project_lookup",
            description="Fuzzy-search a project's ledger (decisions, learnings, done, goals, suggestions) "
            "for items matching a query. Use this to drill into the full history without loading the whole "
            "project JSON. Searches text, rationale, evidence, quote, and tags. Returns both active and "
            "archived items by default so historical context stays reachable.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name, e.g. 'llm_memory'"},
                    "query": {"type": "string", "description": "Search query — space-separated keywords"},
                    "kind": {
                        "type": "string",
                        "description": "Optional filter: 'decisions', 'learnings', 'done', 'goals', 'suggestions'",
                    },
                    "status": {
                        "type": "string",
                        "description": "Optional filter: 'active' or 'archived' (default: both)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 10, cap 50)",
                    },
                },
                "required": ["project", "query"],
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _text(content: str) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=content)]


def _error(message: str) -> list[types.TextContent]:
    return _text(f"Error: {message}")


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    try:
        handlers = {
            "memory_search": _handle_search,
            "narrative_coverage": _handle_narrative_coverage,
            "resume": _handle_resume,
            "project_lookup": _handle_project_lookup,
        }
        handler = handlers.get(name)
        if not handler:
            return _error(f"Unknown tool: {name}")
        return handler(arguments)
    except Exception as exc:
        return _error(str(exc))


# -- memory_search ---------------------------------------------------------

def _handle_search(args: dict[str, Any]) -> list[types.TextContent]:
    query = args.get("query", "").strip()
    project = args.get("project")
    kind = args.get("kind")
    status = args.get("status", "active")
    limit = min(args.get("limit", 20), 100)

    if not query:
        return _error("query is required")

    fts_query = " ".join(f'"{token}"' for token in query.split() if token)

    try:
        sys.path.insert(0, str(Path(__file__).parent))
        import indexer
        results = indexer.search_items(
            query=fts_query,
            project=project,
            kind=kind,
            status=status,
            limit=limit,
            db_path=DB_PATH,
        )
    except ImportError:
        results = []

    return _text(json.dumps(results, indent=2))


# -- narrative_coverage ----------------------------------------------------

def _count_substantive_user_turns(jsonl_path: str, cap: int) -> int:
    """Count top-level user turns that carry a real prompt.

    A "substantive" turn is one where `type == "user"`, `isSidechain` is
    falsy, `message.role == "user"`, and the content is not exclusively
    tool_result blocks (which are synthetic user turns the harness emits
    to feed tool output back to the model).

    Stops counting as soon as `cap` is reached so big transcripts don't
    pay for a full scan when the threshold is low.
    """
    if cap <= 0:
        return 0
    n = 0
    try:
        with open(jsonl_path, encoding="utf-8") as fp:
            for line in fp:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "user" or rec.get("isSidechain"):
                    continue
                msg = rec.get("message") or {}
                if msg.get("role") != "user":
                    continue
                content = msg.get("content")
                if isinstance(content, list) and content and all(
                    isinstance(c, dict) and c.get("type") == "tool_result"
                    for c in content
                ):
                    continue
                n += 1
                if n >= cap:
                    return n
    except OSError:
        # On read error, fail open: report cap so the caller keeps the file.
        return cap
    return n


# A session is merged once, but a long-running one keeps accumulating turns for
# days afterwards. Membership in sessions[] therefore proves the session was
# merged, not that it was merged in full. Compare the transcript's last message
# timestamp against the `ended` the extractor recorded: anything materially
# later is content the narrative has never seen.
#
# The threshold gates a re-extraction, not just a report, so it is set where
# the tail is worth an extractor run. A first pass over 29 projects at 1h
# flagged 32 sessions, 11 of which had grown only a few hours — closing
# messages, not work. A day of growth is the point where a session reliably
# contains something the narrative is missing.
STALE_TAIL_HOURS = 24.0


def _transcript_tail_ts(jsonl_path: Path, tail_bytes: int = 200_000) -> datetime | None:
    """Timestamp of the last message in a transcript, or None.

    Reads only the tail — transcripts run to tens of MB and the coverage call
    walks every merged session.
    """
    try:
        with jsonl_path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - tail_bytes))
            chunk = f.read().decode("utf-8", "ignore")
    except OSError:
        return None
    for line in reversed(chunk.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ts = json.loads(line).get("timestamp")
        except (json.JSONDecodeError, AttributeError):
            continue
        if ts:
            try:
                return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            except ValueError:
                continue
    return None


def _stale_session(session: dict) -> dict | None:
    """Report a merged session whose transcript kept growing after the merge."""
    session_id = str(session.get("session_id") or "")
    ended = session.get("ended")
    if not session_id or not ended:
        return None
    try:
        ended_at = datetime.fromisoformat(str(ended).replace("Z", "+00:00"))
    except ValueError:
        return None
    if ended_at.tzinfo is None:
        ended_at = ended_at.replace(tzinfo=timezone.utc)

    path = DB_DIR / "transcripts" / f"{session_id}.jsonl"
    tail_at = _transcript_tail_ts(path)
    if tail_at is None:
        return None
    if tail_at.tzinfo is None:
        tail_at = tail_at.replace(tzinfo=timezone.utc)

    grew_hours = (tail_at - ended_at).total_seconds() / 3600.0
    if grew_hours <= STALE_TAIL_HOURS:
        return None
    return {
        "session_id": session_id,
        "topic": session.get("topic", ""),
        "merged_through": ended_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_activity": tail_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "grew_days": round(grew_hours / 24.0, 2),
        "path": str(path),
    }


def _coverage_summary(
    unprocessed: int, on_disk: int, filter_note: str, stale: list[dict]
) -> str:
    if unprocessed:
        head = f"{unprocessed} unprocessed transcript(s) out of {on_disk} on disk{filter_note}."
    else:
        head = f"All {on_disk} transcript(s) are merged or filtered out{filter_note}."
    if not stale:
        return head
    worst = stale[0]
    detail = (
        f"{len(stale)} merged session(s) have grown since they were merged "
        f"(worst: {worst['session_id'][:8]} +{worst['grew_days']}d) — re-extract "
        f"these or the tail of that work stays out of the narrative."
    )
    return f"{head} {detail}"


def _find_project_transcripts(project: str) -> set[str]:
    """Return all .jsonl transcript files on disk for a project.

    Live sessions live under ~/.claude/projects/<dir>/ where <dir> encodes
    the project. Archived sessions live under ~/.claude/memory/transcripts/
    and are attributed to a project via their conversation.md frontmatter.
    """
    def normalize(name: str) -> str:
        return name.lower().replace("-", "").replace("_", "")

    target = normalize(project)
    found: set[str] = set()
    seen_sessions: set[str] = set()

    projects_dir = Path.home() / ".claude" / "projects"
    if projects_dir.exists():
        for proj_dir in projects_dir.iterdir():
            if not proj_dir.is_dir():
                continue
            dir_name = proj_dir.name
            parts = dir_name.split("projects-", 1)
            dir_project = parts[1] if len(parts) == 2 else dir_name
            if normalize(dir_project) != target:
                if not (project == "general" and dir_name.endswith("-projects") and not dir_project):
                    continue
            for jsonl in proj_dir.glob("*.jsonl"):
                found.add(str(jsonl))
                seen_sessions.add(jsonl.stem)
            subagents_dir = proj_dir / "subagents"
            if subagents_dir.exists():
                for jsonl in subagents_dir.glob("*.jsonl"):
                    found.add(str(jsonl))
                    seen_sessions.add(jsonl.stem)

    # Archive dir: derive project from the matching conversation.md frontmatter.
    archive_dir = Path.home() / ".claude" / "memory" / "transcripts"
    conv_dir = DB_DIR / "conversations"
    if archive_dir.exists():
        try:
            from conversations import list_sessions as _list_sessions
            project_sids = set(_list_sessions(project, conv_dir))
        except ImportError:
            project_sids = set()
        for jsonl in archive_dir.glob("*.jsonl"):
            if not jsonl.is_file():
                continue
            sid = jsonl.stem
            if sid in seen_sessions:
                continue
            if sid in project_sids:
                found.add(str(jsonl))
                seen_sessions.add(sid)

    return found


def _handle_narrative_coverage(args: dict[str, Any]) -> list[types.TextContent]:
    project = args.get("project", "").strip()
    if not project:
        return _error("project is required")

    try:
        min_user_turns = int(args.get("min_user_turns", 5) or 0)
    except (TypeError, ValueError):
        min_user_turns = 5
    if min_user_turns < 0:
        min_user_turns = 0

    on_disk = _find_project_transcripts(project)
    state_path = DB_DIR / "projects" / f"{project}.json"
    narrative_path = DB_DIR / "projects" / f"{project}.narrative.md"

    # "Processed" is the set of main-session session_ids in {project}.json.sessions[].
    merged_ids: set[str] = set()
    stale: list[dict] = []
    state_exists = state_path.exists()
    if state_exists:
        try:
            with state_path.open() as f:
                state = json.load(f)
            for session in state.get("sessions", []) or []:
                sid = str(session.get("session_id") or "")
                if sid and not sid.startswith(("agent-", "audit-")):
                    merged_ids.add(sid)
                    info = _stale_session(session)
                    if info:
                        stale.append(info)
        except (OSError, json.JSONDecodeError):
            pass
    stale.sort(key=lambda s: s["grew_days"], reverse=True)

    # Map on-disk paths to session_ids.
    on_disk_by_sid = {Path(p).stem: p for p in on_disk}

    raw_unprocessed = [
        (sid, p) for sid, p in sorted(on_disk_by_sid.items()) if sid not in merged_ids
    ]

    # Filter: drop sub-agent transcripts (agent-*) and short one-shot sessions
    # so the narrative pipeline doesn't extract from noise like single-prompt
    # SDK calls. Counts only need to reach the threshold, so the scan short-
    # circuits cheaply for sessions that already qualify.
    unprocessed: list[str] = []
    skipped_subagent = 0
    skipped_low_turn = 0
    for sid, p in raw_unprocessed:
        if sid.startswith("agent-"):
            skipped_subagent += 1
            continue
        if min_user_turns > 0:
            turns = _count_substantive_user_turns(p, cap=min_user_turns)
            if turns < min_user_turns:
                skipped_low_turn += 1
                continue
        unprocessed.append(p)

    narrative_mtime = None
    if narrative_path.exists():
        try:
            narrative_mtime = datetime.fromtimestamp(
                narrative_path.stat().st_mtime, tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%S")
        except OSError:
            pass

    skipped_total = skipped_subagent + skipped_low_turn
    filter_note = (
        f" (excluded {skipped_total}: {skipped_subagent} sub-agent, "
        f"{skipped_low_turn} <{min_user_turns}-turn)"
        if skipped_total else ""
    )

    if not state_exists:
        return _text(json.dumps({
            "project": project,
            "status": "no_state",
            "on_disk_count": len(on_disk),
            "processed_count": 0,
            "unprocessed_count": len(unprocessed),
            "unprocessed": unprocessed,
            "skipped_subagent_count": skipped_subagent,
            "skipped_low_turn_count": skipped_low_turn,
            "min_user_turns": min_user_turns,
            "summary": (
                f"No {project}.json yet. {len(unprocessed)} substantive "
                f"transcript(s) ready to process out of {len(on_disk)} on disk"
                f"{filter_note} — run /narrative to bootstrap."
            ),
        }, indent=2))

    return _text(json.dumps({
        "project": project,
        "narrative_path": str(narrative_path) if narrative_path.exists() else None,
        "narrative_updated": narrative_mtime,
        "on_disk_count": len(on_disk),
        "processed_count": len(merged_ids),
        "unprocessed_count": len(unprocessed),
        "unprocessed": unprocessed,
        "skipped_subagent_count": skipped_subagent,
        "skipped_low_turn_count": skipped_low_turn,
        "min_user_turns": min_user_turns,
        "stale_count": len(stale),
        "stale": stale,
        "summary": _coverage_summary(
            len(unprocessed), len(on_disk), filter_note, stale
        ),
    }, indent=2))


# -- resume ----------------------------------------------------------------

def _handle_resume(args: dict[str, Any]) -> list[types.TextContent]:
    project = (args.get("project") or "").strip()
    lines_wanted = args.get("lines", 150) or 150
    if not project:
        return _error("project is required")

    state_path = DB_DIR / "projects" / f"{project}.json"
    if not state_path.exists():
        return _error(f"no project state at {state_path}")

    try:
        state = json.loads(state_path.read_text())
    except Exception as exc:
        return _error(f"failed to read state: {exc}")

    # Skip synthetic sessions (audit-*, agent-*) — same rule as the renderer.
    sessions = [
        s for s in state.get("sessions", [])
        if s.get("status") == "active"
        and not (s.get("session_id") or "").startswith(("audit-", "agent-"))
    ]
    sessions.sort(key=lambda s: s.get("started", ""))
    if not sessions:
        return _text(json.dumps({"project": project, "status": "no_sessions"}, indent=2))

    last = sessions[-1]
    sid = last.get("session_id", "")
    journal = (last.get("journal") or "").strip()
    closure = last.get("closure_status") or "unknown"

    conv_rel = last.get("conversation_md") or ""
    conv_path = Path(conv_rel.replace("~", str(Path.home())))
    excerpt = ""
    if conv_path.exists():
        try:
            text = conv_path.read_text(errors="replace")
            excerpt = "\n".join(text.splitlines()[-lines_wanted:])
        except Exception as exc:
            excerpt = f"(failed to read conversation.md: {exc})"
    else:
        excerpt = f"(conversation.md not found at {conv_path})"

    result = {
        "project": project,
        "session_id": sid,
        "started": last.get("started"),
        "ended": last.get("ended"),
        "closure_status": closure,
        "topic": last.get("topic", ""),
        "journal": journal,
        "conversation_tail_lines": lines_wanted,
        "conversation_tail": excerpt,
    }
    return _text(json.dumps(result, indent=2))


# -- project_lookup --------------------------------------------------------

_LEDGER_KINDS = ("decisions", "learnings", "done", "goals", "suggestions")


def _item_searchable_text(item: dict) -> str:
    parts = [
        item.get("text", ""),
        item.get("rationale", ""),
        item.get("evidence", "") if isinstance(item.get("evidence"), str) else " ".join(item.get("evidence") or []),
        item.get("quote", ""),
        item.get("progress", ""),
        item.get("archived_reason", ""),
        " ".join(item.get("tags") or []) if isinstance(item.get("tags"), list) else (item.get("tags") or ""),
    ]
    return " ".join(p for p in parts if p).lower()


def _handle_project_lookup(args: dict[str, Any]) -> list[types.TextContent]:
    project = (args.get("project") or "").strip()
    query = (args.get("query") or "").strip()
    kind_filter = (args.get("kind") or "").strip() or None
    status_filter = (args.get("status") or "").strip() or None
    limit = min(int(args.get("limit", 10) or 10), 50)

    if not project:
        return _error("project is required")
    if not query:
        return _error("query is required")
    if kind_filter and kind_filter not in _LEDGER_KINDS:
        return _error(f"kind must be one of: {', '.join(_LEDGER_KINDS)}")
    if status_filter and status_filter not in ("active", "archived"):
        return _error("status must be 'active' or 'archived'")

    state_path = DB_DIR / "projects" / f"{project}.json"
    if not state_path.exists():
        return _error(f"no project state at {state_path}")
    try:
        state = json.loads(state_path.read_text())
    except Exception as exc:
        return _error(f"failed to read state: {exc}")

    # Tokenize query. Keep tokens of length >= 2.
    tokens = [t.lower() for t in query.split() if len(t) >= 2]
    if not tokens:
        return _error("query must contain at least one token of length >= 2")

    kinds = (kind_filter,) if kind_filter else _LEDGER_KINDS

    # Score each item: sum of token occurrences across searchable fields.
    # Tie-break by importance (load_bearing > standard > minor) then by last_touched_at desc.
    imp_rank = {"load_bearing": 3, "standard": 2, "minor": 1}

    scored: list[tuple[float, dict, str]] = []
    for kind in kinds:
        for item in state.get(kind, []):
            if status_filter and item.get("status", "active") != status_filter:
                continue
            text = _item_searchable_text(item)
            if not text:
                continue
            hits = sum(text.count(tok) for tok in tokens)
            if hits == 0:
                continue
            # Bonus for matches in the primary text field (first 200 chars weighted 2x).
            primary = (item.get("text") or "").lower()
            primary_hits = sum(primary.count(tok) for tok in tokens)
            score = hits + primary_hits
            # Importance tiebreak baked into score.
            score += 0.01 * imp_rank.get(item.get("importance") or "standard", 2)
            scored.append((score, item, kind))

    scored.sort(key=lambda x: (-x[0], -(imp_rank.get(x[1].get("importance") or "standard", 2)), x[1].get("last_touched_at") or ""))
    top = scored[:limit]

    results = []
    for score, item, kind in top:
        results.append({
            "kind": kind,
            "id": item.get("id"),
            "status": item.get("status", "active"),
            "importance": item.get("importance"),
            "text": item.get("text"),
            "rationale": item.get("rationale"),
            "evidence": item.get("evidence"),
            "quote": item.get("quote"),
            "introduced_in": item.get("introduced_in"),
            "last_touched_in": item.get("last_touched_in"),
            "last_touched_at": item.get("last_touched_at"),
            "archived_reason": item.get("archived_reason"),
            "_score": round(score, 2),
        })

    return _text(json.dumps({
        "project": project,
        "query": query,
        "total_hits": len(scored),
        "returned": len(results),
        "results": results,
    }, indent=2))


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    _ensure_stignore()
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
