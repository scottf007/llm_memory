"""
Batch processor for Claude Code JSONL transcripts.

Discovers all session transcripts in ~/.claude/projects/, archives them,
and creates session_log entries in the LLM Memory database.

Usage:
    python process_transcripts.py
    python process_transcripts.py --dry-run
    python process_transcripts.py --project finance_nexus
    python process_transcripts.py --verbose
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
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

MIN_TURNS = 2  # skip trivial sessions


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_processed_sessions(conn) -> set[str]:
    sessions = set()
    # Check DB
    if conn:
        rows = conn.execute(
            "SELECT DISTINCT session_id FROM memories "
            "WHERE type='session_log' AND session_id IS NOT NULL",
        ).fetchall()
        sessions.update(r["session_id"] for r in rows)
    # Also check record files
    records_dir = DB_DIR / "records"
    if records_dir.exists():
        for path in records_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text())
                if data.get("type") == "session_log" and data.get("session_id"):
                    sessions.add(data["session_id"])
            except Exception:
                continue
    return sessions


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
    parts = name.split("-")
    try:
        idx = parts.index("projects")
        after = parts[idx + 1:]
        if after:
            return "-".join(after)
        return ""
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
    for cwd in cwds:
        project = _project_from_cwd(cwd)
        if project:
            return project
    dir_project = derive_project_from_dir(dir_path)
    return dir_project if dir_project else "general"


# ---------------------------------------------------------------------------
# Text extraction helpers
# ---------------------------------------------------------------------------

# Regex to strip noisy XML-like tags injected by hooks and IDE
_NOISE_TAG_RE = re.compile(
    r"<(?:ide_opened_file|local-command-caveat|command-me|system-reminder|command-name|"
    r"command-message|command-args|local-command-stdout|user-prompt-submit-hook|"
    r"available-deferred-tools|fast_mode_info)>.*?</(?:ide_opened_file|local-command-caveat|"
    r"command-me|system-reminder|command-name|command-message|command-args|"
    r"local-command-stdout|user-prompt-submit-hook|available-deferred-tools|fast_mode_info)>",
    re.DOTALL,
)
_NOISE_TAG_OPEN_RE = re.compile(
    r"<(?:ide_opened_file|local-command-caveat|command-me|system-reminder|"
    r"user-prompt-submit-hook|available-deferred-tools|fast_mode_info)[^>]*>[^<]*"
)
_HTML_TAG_RE = re.compile(r"</?(?:h[1-6]|p|div|span|br|ul|ol|li|a|strong|em|code|pre|table|tr|td|th)[^>]*>")


def _clean_text(text: str) -> str:
    """Strip noisy tags from extracted text."""
    text = _NOISE_TAG_RE.sub("", text)
    text = _NOISE_TAG_OPEN_RE.sub("", text)
    text = _HTML_TAG_RE.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_user_text(entry: dict) -> str:
    """Extract text content from a user message entry."""
    msg = entry.get("message", {})
    content = msg.get("content", "")

    if isinstance(content, str):
        return _clean_text(content)

    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", "").strip())
            elif isinstance(block, str):
                parts.append(block.strip())
        return _clean_text(" ".join(parts))

    return ""


def _extract_assistant_text(entry: dict) -> str:
    """Extract text content from an assistant message entry."""
    msg = entry.get("message", {})
    content = msg.get("content", [])

    if isinstance(content, str):
        return _clean_text(content)

    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "").strip()
                if text:
                    parts.append(text)
        return _clean_text(" ".join(parts))

    return ""


# ---------------------------------------------------------------------------
# Transcript extraction
# ---------------------------------------------------------------------------

def extract_session_data(path: Path) -> dict[str, Any]:
    """Stream a JSONL transcript and extract key data for a session_log."""
    cwds: list[str] = []
    cwd_seen: set[str] = set()
    session_id = None
    first_ts = None
    last_ts = None
    turn_count = 0
    last_assistant_text = ""

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

            elif entry_type == "assistant":
                text = _extract_assistant_text(entry)
                if text and len(text) > 50:
                    last_assistant_text = text[:300]

    return {
        "session_id": session_id or path.stem,
        "project": derive_project(cwds, path.parent),
        "turn_count": turn_count,
        "first_timestamp": first_ts,
        "last_timestamp": last_ts,
        "summary": last_assistant_text,
    }


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


def store_session_log(
    conn,
    session_id: str,
    project: str,
    content: str,
    transcript_ref: str,
) -> str:
    """Insert a session_log memory. Returns the UUID."""
    uuid = os.urandom(16).hex()
    created_at = datetime.now(tz=__import__('datetime').timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    record = {
        "schema_version": 1,
        "uuid": uuid,
        "type": "session_log",
        "content": content,
        "project": project,
        "session_id": session_id,
        "importance": 3,
        "transcript_ref": transcript_ref,
        "tags": None,
        "created_at": created_at,
        "connections": [],
    }

    # Write record file
    records_dir = DB_DIR / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    (records_dir / f"{uuid}.json").write_text(json.dumps(record, indent=2))

    # Also insert into DB for immediate use
    if conn:
        conn.execute(
            "INSERT INTO memories (uuid, type, content, project, session_id, importance, transcript_ref, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (uuid, "session_log", content, project, session_id, 3, transcript_ref, created_at),
        )
        conn.commit()

    return uuid


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def process_transcripts(args, conn, transcripts) -> tuple[int, int]:
    """Process transcripts into session_log memories."""
    processed = get_processed_sessions(conn)
    if not args.quiet:
        print(f"Found {len(transcripts)} transcripts, {len(processed)} session_logs already exist")

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

        content = f"Session {session_id} for {data['project']}, {data['turn_count']} turns."
        if data["summary"]:
            content += f" {data['summary']}"

        transcript_ref = f"~/.claude/memory/transcripts/{session_id}.jsonl"

        if args.dry_run:
            if not args.quiet:
                print(f"\n[DRY RUN] Would create session_log: {session_id}")
                print(f"  Project: {data['project']}")
                print(f"  Turns: {data['turn_count']}")
                print(f"  File: {path} ({path.stat().st_size / 1024:.0f} KB)")
        else:
            archive_transcript(path, session_id)
            uuid = store_session_log(conn, session_id, data["project"], content, transcript_ref)
            if not args.quiet:
                print(f"  session_log: {session_id} → {uuid} ({data['project']}, {data['turn_count']} turns)")

        new_count += 1

    return new_count, skipped_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Process Claude Code transcripts into LLM Memory")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be processed without writing")
    parser.add_argument("--verbose", action="store_true", help="Print detailed output per transcript")
    parser.add_argument("--quiet", action="store_true", help="Minimal output (for use from hooks)")
    parser.add_argument("--project", type=str, help="Only process transcripts for this project")
    args = parser.parse_args()

    if not DB_PATH.exists() and not args.dry_run:
        print(f"Error: Database not found at {DB_PATH}")
        print("Run the LLM Memory server first to initialize the database.")
        return

    conn = get_db() if not args.dry_run else None
    transcripts = find_transcripts(project_filter=args.project)

    if not args.quiet:
        print("--- Processing Transcripts ---")
    new_count, skipped = process_transcripts(args, conn, transcripts)
    if not args.quiet:
        print(f"Done: {new_count} new session_logs, {skipped} skipped.")

    if conn:
        conn.close()


if __name__ == "__main__":
    main()
