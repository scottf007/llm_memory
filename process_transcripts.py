"""Batch processor for Claude Code JSONL transcripts.

Scans ~/.claude/projects/ and ~/.claude/memory/transcripts/, ensures each
main-session .jsonl has a matching conversation.md in
~/.claude/memory/conversations/. Agent and audit transcripts are skipped.

Usage:
    python process_transcripts.py
    python process_transcripts.py --dry-run
    python process_transcripts.py --quiet
"""

import argparse
import shutil
from pathlib import Path

import adapters
import extract_conversation

DB_DIR = Path.home() / ".claude" / "memory"
ARCHIVE_DIR = DB_DIR / "transcripts"
CONVERSATIONS_DIR = DB_DIR / "conversations"


def find_transcripts(client: str = adapters.DEFAULT) -> list[tuple[Path, str]]:
    """Return (jsonl_path, session_id) for every main-session transcript.

    Discovery is the adapter's job — it knows where that client keeps its
    sessions. This wrapper keeps the historical tuple shape for callers.
    """
    return [(ref.path, ref.session_id) for ref in adapters.get(client).discover()]


def archive_transcript(path: Path, session_id: str) -> Path:
    """Ensure the jsonl lives at ARCHIVE_DIR/<sid>.jsonl. Return the archived path."""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    dest = ARCHIVE_DIR / f"{session_id}.jsonl"
    if not dest.exists() or path.stat().st_size > dest.stat().st_size:
        shutil.copy2(path, dest)
    return dest


def ensure_conversation_md(jsonl_path: Path, session_id: str) -> Path | None:
    """Write the stripped conversation .md beside the archive if missing or stale.

    Skip agent-* and audit-* stems — those transcripts are captured by their
    parent session and don't need their own conversation.md.
    """
    if session_id.startswith(("agent-", "audit-")):
        return None

    CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
    dest = CONVERSATIONS_DIR / f"{session_id}.md"
    if dest.exists() and dest.stat().st_mtime >= jsonl_path.stat().st_mtime:
        return dest
    dest.write_text(extract_conversation.extract(jsonl_path))
    return dest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ensure each JSONL transcript has an archived copy and a conversation.md sibling."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would happen without writing")
    parser.add_argument("--quiet", action="store_true",
                        help="Minimal output (for use from hooks)")
    args = parser.parse_args()

    transcripts = find_transcripts()
    archived = 0
    extracted = 0

    for path, sid in transcripts:
        if args.dry_run:
            continue
        try:
            archive = archive_transcript(path, sid)
            if ensure_conversation_md(archive, sid) is not None:
                extracted += 1
            archived += 1
        except Exception as e:
            if not args.quiet:
                print(f"  WARN: {sid}: {e}")

    if not args.quiet:
        print(f"Scanned {len(transcripts)} transcripts; "
              f"archived {archived}, produced {extracted} conversation.md.")


if __name__ == "__main__":
    main()
