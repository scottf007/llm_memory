"""
Batch processor for Claude Code JSONL transcripts.

Discovers all session transcripts in ~/.claude/projects/, extracts
summaries, archives transcripts, and stores session_summary memories
in the LLM Memory database.

Usage:
    python process_transcripts.py
    python process_transcripts.py --dry-run
    python process_transcripts.py --project finance_nexus
    python process_transcripts.py --verbose
"""

import argparse
import json
import shutil
import sqlite3
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DB_DIR = Path.home() / ".claude" / "memory"
DB_PATH = DB_DIR / "memory.db"
PROJECTS_DIR = Path.home() / ".claude" / "projects"
ARCHIVE_DIR = DB_DIR / "transcripts"

MAX_USER_PROMPTS = 5       # first N user prompts to include
MAX_ASSISTANT_BLOCKS = 5   # last N assistant text blocks to include
MAX_BLOCK_CHARS = 500      # truncate individual blocks
MAX_SUMMARY_CHARS = 2000   # overall summary limit
MIN_TURNS = 2              # skip trivial sessions
CHUNK_SIZE = 25            # turns per chunk
MIN_CHUNK_TURNS = 3        # skip chunks with fewer turns than this


# ---------------------------------------------------------------------------
# Database helpers (matches server.py pattern)
# ---------------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_processed_sessions(conn: sqlite3.Connection, mem_type: str = "session_summary") -> set[str]:
    rows = conn.execute(
        "SELECT DISTINCT session_id FROM memories "
        "WHERE type=? AND session_id IS NOT NULL",
        (mem_type,),
    ).fetchall()
    return {r["session_id"] for r in rows}


# ---------------------------------------------------------------------------
# Transcript discovery
# ---------------------------------------------------------------------------

def find_transcripts(project_filter: Optional[str] = None) -> list[tuple[Path, str]]:
    """Find main session JSONL files (skip subagents)."""
    results = []
    if not PROJECTS_DIR.exists():
        return results

    for project_dir in sorted(PROJECTS_DIR.iterdir()):
        if not project_dir.is_dir():
            continue
        if project_filter:
            derived = derive_project_from_dir(project_dir)
            if derived and project_filter not in derived:
                continue
        for jsonl in sorted(project_dir.glob("*.jsonl")):
            if jsonl.is_file():
                session_id = jsonl.stem
                results.append((jsonl, session_id))

    return results


# ---------------------------------------------------------------------------
# Project name derivation
# ---------------------------------------------------------------------------

def derive_project_from_dir(dir_path: Path) -> str:
    """Extract project name from directory like '-home-scott-projects-finance-nexus'."""
    name = dir_path.name
    # Strip the common prefix pattern: -home-USER-projects-PROJECTNAME
    # or -home-USER-projects (the bare projects dir)
    parts = name.split("-")
    # Find 'projects' in parts, take everything after it
    try:
        idx = parts.index("projects")
        after = parts[idx + 1:]
        if after:
            return "-".join(after)
        return ""  # bare projects dir, no project name
    except ValueError:
        pass
    return name


def _project_from_cwd(cwd: str) -> Optional[str]:
    """Extract project name from a cwd path like /home/user/projects/foo."""
    parts = Path(cwd).parts
    for i, part in enumerate(parts):
        if part == "projects" and i + 1 < len(parts):
            return parts[i + 1]
    return None


def derive_project(cwds: list[str], dir_path: Path) -> str:
    """Derive project name from collected cwds, falling back to directory name."""
    # Try each cwd, prefer the first one that has a project name
    for cwd in cwds:
        project = _project_from_cwd(cwd)
        if project:
            return project
    dir_project = derive_project_from_dir(dir_path)
    return dir_project if dir_project else "general"


# ---------------------------------------------------------------------------
# Transcript extraction
# ---------------------------------------------------------------------------

def extract_session_data(path: Path) -> dict[str, Any]:
    """Stream a JSONL transcript and extract key data."""
    user_prompts: list[str] = []
    assistant_texts: deque[str] = deque(maxlen=MAX_ASSISTANT_BLOCKS)
    cwds: list[str] = []
    cwd_seen: set[str] = set()
    session_id = None
    first_ts = None
    last_ts = None
    turn_count = 0

    with open(path, "r", errors="replace") as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
            except (json.JSONDecodeError, ValueError):
                continue

            entry_type = entry.get("type")
            ts = entry.get("timestamp")

            if ts:
                if first_ts is None:
                    first_ts = ts
                last_ts = ts

            entry_cwd = entry.get("cwd")
            if entry_cwd and entry_cwd not in cwd_seen:
                cwd_seen.add(entry_cwd)
                cwds.append(entry_cwd)
            if not session_id and entry.get("sessionId"):
                session_id = entry["sessionId"]

            if entry_type == "user":
                turn_count += 1
                if len(user_prompts) < MAX_USER_PROMPTS:
                    text = _extract_user_text(entry)
                    if text and len(text) > 10:
                        user_prompts.append(text[:MAX_BLOCK_CHARS])

            elif entry_type == "assistant":
                text = _extract_assistant_text(entry)
                if text and len(text) > 20:
                    assistant_texts.append(text[:MAX_BLOCK_CHARS])

    return {
        "session_id": session_id or path.stem,
        "cwd": cwds[0] if cwds else None,
        "project": derive_project(cwds, path.parent),
        "user_prompts": user_prompts,
        "assistant_texts": list(assistant_texts),
        "turn_count": turn_count,
        "first_timestamp": first_ts,
        "last_timestamp": last_ts,
    }


def extract_chunks(path: Path) -> list[dict[str, Any]]:
    """Stream a JSONL transcript and split into chunks of ~CHUNK_SIZE turns."""
    chunks: list[dict[str, Any]] = []
    # Current chunk accumulator
    user_prompts: list[str] = []
    assistant_texts: list[str] = []
    cwds: list[str] = []
    cwd_seen: set[str] = set()
    session_id = None
    chunk_first_ts = None
    chunk_last_ts = None
    turn_count = 0
    chunk_turn_count = 0

    def flush_chunk():
        nonlocal user_prompts, assistant_texts, chunk_first_ts, chunk_last_ts, chunk_turn_count
        if chunk_turn_count < MIN_CHUNK_TURNS:
            # Too small, skip
            user_prompts = []
            assistant_texts = []
            chunk_first_ts = None
            chunk_last_ts = None
            chunk_turn_count = 0
            return
        chunks.append({
            "user_prompts": user_prompts[:MAX_USER_PROMPTS],
            "assistant_texts": assistant_texts[-MAX_ASSISTANT_BLOCKS:],
            "first_timestamp": chunk_first_ts,
            "last_timestamp": chunk_last_ts,
            "turn_count": chunk_turn_count,
        })
        user_prompts = []
        assistant_texts = []
        chunk_first_ts = None
        chunk_last_ts = None
        chunk_turn_count = 0

    with open(path, "r", errors="replace") as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
            except (json.JSONDecodeError, ValueError):
                continue

            entry_type = entry.get("type")
            ts = entry.get("timestamp")

            if ts:
                if chunk_first_ts is None:
                    chunk_first_ts = ts
                chunk_last_ts = ts

            entry_cwd = entry.get("cwd")
            if entry_cwd and entry_cwd not in cwd_seen:
                cwd_seen.add(entry_cwd)
                cwds.append(entry_cwd)
            if not session_id and entry.get("sessionId"):
                session_id = entry["sessionId"]

            if entry_type == "user":
                turn_count += 1
                chunk_turn_count += 1
                text = _extract_user_text(entry)
                if text and len(text) > 10:
                    user_prompts.append(text[:MAX_BLOCK_CHARS])

                # Flush chunk at boundary
                if chunk_turn_count >= CHUNK_SIZE:
                    flush_chunk()

            elif entry_type == "assistant":
                text = _extract_assistant_text(entry)
                if text and len(text) > 20:
                    assistant_texts.append(text[:MAX_BLOCK_CHARS])

    # Flush remaining
    flush_chunk()

    return chunks, derive_project(cwds, path.parent), session_id or path.stem


def build_chunk_summary(chunk: dict[str, Any], chunk_num: int, total_chunks: int, project: str) -> str:
    """Build a chunk summary string."""
    turns = chunk["turn_count"]

    date_range = ""
    if chunk["first_timestamp"]:
        try:
            start = datetime.fromisoformat(chunk["first_timestamp"].replace("Z", "+00:00"))
            date_range = start.strftime("%Y-%m-%d %H:%M")
            if chunk["last_timestamp"]:
                end = datetime.fromisoformat(chunk["last_timestamp"].replace("Z", "+00:00"))
                if start.date() == end.date():
                    date_range += f" - {end.strftime('%H:%M')}"
                else:
                    date_range += f" to {end.strftime('%Y-%m-%d %H:%M')}"
        except (ValueError, TypeError):
            pass

    lines = [f"Chunk {chunk_num}/{total_chunks}: {project} ({date_range}, {turns} turns)"]

    if chunk["user_prompts"]:
        lines.append("")
        lines.append("Topics:")
        for prompt in chunk["user_prompts"]:
            short = prompt[:150].replace("\n", " ").strip()
            if len(prompt) > 150:
                short += "..."
            lines.append(f"- {short}")

    if chunk["assistant_texts"]:
        lines.append("")
        lines.append("Outcomes:")
        for text in chunk["assistant_texts"]:
            short = text[:150].replace("\n", " ").strip()
            if len(text) > 150:
                short += "..."
            lines.append(f"- {short}")

    summary = "\n".join(lines)
    if len(summary) > MAX_SUMMARY_CHARS:
        summary = summary[:MAX_SUMMARY_CHARS] + "\n..."
    return summary


def store_chunk(
    conn: sqlite3.Connection,
    session_id: str,
    project: str,
    content: str,
    transcript_ref: str,
    prev_chunk_id: Optional[int] = None,
) -> int:
    """Insert a chunk_summary memory and link to previous chunk. Returns new ID."""
    cursor = conn.execute(
        "INSERT INTO memories (type, content, project, session_id, importance, transcript_ref) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("chunk_summary", content, project, session_id, 5, transcript_ref),
    )
    chunk_id = cursor.lastrowid

    if prev_chunk_id is not None:
        conn.execute(
            "INSERT OR IGNORE INTO connections (from_id, to_id, relationship) "
            "VALUES (?, ?, ?)",
            (chunk_id, prev_chunk_id, "related_to"),
        )

    conn.commit()
    return chunk_id


def _extract_user_text(entry: dict) -> str:
    """Extract text content from a user message entry."""
    msg = entry.get("message", {})
    content = msg.get("content", "")

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", "").strip())
            elif isinstance(block, str):
                parts.append(block.strip())
        return " ".join(parts)

    return ""


def _extract_assistant_text(entry: dict) -> str:
    """Extract text content from an assistant message entry."""
    msg = entry.get("message", {})
    content = msg.get("content", [])

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "").strip()
                if text:
                    parts.append(text)
        return " ".join(parts)

    return ""


# ---------------------------------------------------------------------------
# Summary building
# ---------------------------------------------------------------------------

def build_summary(data: dict[str, Any]) -> str:
    """Build a readable session summary from extracted data."""
    project = data["project"]
    turns = data["turn_count"]

    # Date range
    date_range = ""
    if data["first_timestamp"]:
        try:
            start = datetime.fromisoformat(data["first_timestamp"].replace("Z", "+00:00"))
            date_range = start.strftime("%Y-%m-%d %H:%M")
            if data["last_timestamp"]:
                end = datetime.fromisoformat(data["last_timestamp"].replace("Z", "+00:00"))
                if start.date() == end.date():
                    date_range += f" - {end.strftime('%H:%M')}"
                else:
                    date_range += f" to {end.strftime('%Y-%m-%d %H:%M')}"
        except (ValueError, TypeError):
            pass

    lines = [f"Session for {project} ({date_range}, {turns} turns)"]

    if data["user_prompts"]:
        lines.append("")
        lines.append("User goals:")
        for prompt in data["user_prompts"]:
            # Truncate for summary
            short = prompt[:200].replace("\n", " ").strip()
            if len(prompt) > 200:
                short += "..."
            lines.append(f"- {short}")

    if data["assistant_texts"]:
        lines.append("")
        lines.append("Key outcomes:")
        for text in data["assistant_texts"]:
            short = text[:200].replace("\n", " ").strip()
            if len(text) > 200:
                short += "..."
            lines.append(f"- {short}")

    summary = "\n".join(lines)
    if len(summary) > MAX_SUMMARY_CHARS:
        summary = summary[:MAX_SUMMARY_CHARS] + "\n..."
    return summary


# ---------------------------------------------------------------------------
# Archive & store
# ---------------------------------------------------------------------------

def archive_transcript(path: Path, session_id: str) -> Path:
    """Copy transcript to archive directory. Returns archive path."""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    dest = ARCHIVE_DIR / f"{session_id}.jsonl"
    if not dest.exists() or path.stat().st_size > dest.stat().st_size:
        shutil.copy2(path, dest)
    return dest


def store_summary(
    conn: sqlite3.Connection,
    session_id: str,
    project: str,
    summary: str,
    transcript_ref: str,
) -> int:
    """Insert a session_summary memory. Returns the new memory ID."""
    cursor = conn.execute(
        "INSERT INTO memories (type, content, project, session_id, importance, transcript_ref) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("session_summary", summary, project, session_id, 6, transcript_ref),
    )
    # Keep FTS in sync (triggers handle this if they exist, but be safe)
    conn.commit()
    return cursor.lastrowid


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def process_summaries(args, conn, transcripts) -> tuple[int, int]:
    """Process transcripts into session_summary memories."""
    processed = get_processed_sessions(conn, "session_summary") if conn else set()
    print(f"Found {len(transcripts)} transcripts, {len(processed)} session summaries already exist")

    new_count = 0
    skipped_count = 0

    for path, session_id in transcripts:
        if session_id in processed:
            skipped_count += 1
            continue

        data = extract_session_data(path)

        if data["turn_count"] < MIN_TURNS:
            if args.verbose:
                print(f"  Skip (trivial): {session_id} ({data['turn_count']} turns)")
            skipped_count += 1
            continue

        summary = build_summary(data)
        transcript_ref = f"~/.claude/memory/transcripts/{session_id}.jsonl"

        if args.dry_run:
            print(f"\n[DRY RUN] Would create summary: {session_id}")
            print(f"  Project: {data['project']}")
            print(f"  Turns: {data['turn_count']}")
            print(f"  File: {path} ({path.stat().st_size / 1024:.0f} KB)")
            if args.verbose:
                print(f"  Summary preview:\n    {summary[:300]}...")
        else:
            archive_transcript(path, session_id)
            memory_id = store_summary(conn, session_id, data["project"], summary, transcript_ref)
            print(f"  Summary: {session_id} → #{memory_id} ({data['project']}, {data['turn_count']} turns)")

        new_count += 1

    return new_count, skipped_count


def process_chunks(args, conn, transcripts) -> tuple[int, int]:
    """Process transcripts into chunk_summary memories."""
    processed = get_processed_sessions(conn, "chunk_summary") if conn else set()
    print(f"Found {len(transcripts)} transcripts, {len(processed)} already chunked")

    new_chunks = 0
    skipped_count = 0

    for path, session_id in transcripts:
        if session_id in processed:
            skipped_count += 1
            continue

        chunks, project, _ = extract_chunks(path)

        if not chunks:
            if args.verbose:
                print(f"  Skip (no chunks): {session_id}")
            skipped_count += 1
            continue

        transcript_ref = f"~/.claude/memory/transcripts/{session_id}.jsonl"

        if args.dry_run:
            print(f"\n[DRY RUN] Would create {len(chunks)} chunks: {session_id} ({project})")
            if args.verbose:
                for i, chunk in enumerate(chunks, 1):
                    summary = build_chunk_summary(chunk, i, len(chunks), project)
                    print(f"  Chunk {i}: {chunk['turn_count']} turns")
                    print(f"    {summary[:200]}...")
        else:
            archive_transcript(path, session_id)
            prev_id = None
            for i, chunk in enumerate(chunks, 1):
                content = build_chunk_summary(chunk, i, len(chunks), project)
                chunk_id = store_chunk(conn, session_id, project, content, transcript_ref, prev_id)
                prev_id = chunk_id
                new_chunks += 1
            print(f"  Chunks: {session_id} → {len(chunks)} chunks ({project})")

    return new_chunks, skipped_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Process Claude Code transcripts into LLM Memory")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be processed without writing")
    parser.add_argument("--verbose", action="store_true", help="Print detailed output per transcript")
    parser.add_argument("--project", type=str, help="Only process transcripts for this project")
    parser.add_argument("--chunks-only", action="store_true", help="Only generate chunk summaries, skip session summaries")
    parser.add_argument("--summaries-only", action="store_true", help="Only generate session summaries, skip chunks")
    args = parser.parse_args()

    if not DB_PATH.exists() and not args.dry_run:
        print(f"Error: Database not found at {DB_PATH}")
        print("Run the LLM Memory server first to initialize the database.")
        return

    conn = get_db() if not args.dry_run else None
    transcripts = find_transcripts(project_filter=args.project)

    summary_new = summary_skip = chunk_new = chunk_skip = 0

    if not args.chunks_only:
        print("--- Session Summaries ---")
        summary_new, summary_skip = process_summaries(args, conn, transcripts)
        print(f"Summaries: {summary_new} new, {summary_skip} skipped.\n")

    if not args.summaries_only:
        print("--- Chunk Summaries ---")
        chunk_new, chunk_skip = process_chunks(args, conn, transcripts)
        print(f"Chunks: {chunk_new} new, {chunk_skip} skipped.")

    if conn:
        conn.close()


if __name__ == "__main__":
    main()
