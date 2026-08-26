"""Batch processor for session transcripts, across every registered client.

Ensures each main session has an archived .jsonl in
~/.claude/memory/transcripts/ and a matching conversation.md in
~/.claude/memory/conversations/. Agent and audit transcripts are skipped.

Claude sessions are archived by copying the transcript. Foreign clients keep
their own format, so their archive entry is a canonical Claude-shaped
*envelope* generated from the same turns that produced the .md — see
adapters/envelope.py for why. The client's original file is never moved.

Usage:
    python process_transcripts.py
    python process_transcripts.py --dry-run
    python process_transcripts.py --quiet
    python process_transcripts.py --client codex
"""

import argparse
import shutil
from pathlib import Path

import adapters
from tools.memory_config import memory_root

DB_DIR = memory_root()
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


def ensure_conversation_md(jsonl_path: Path, session_id: str,
                           client: str | None = None) -> Path | None:
    """Write the stripped conversation .md if it is missing or stale.

    Skip agent-* and audit-* stems — those transcripts are captured by their
    parent session and don't need their own conversation.md.

    Also skip session ids owned by another client. That guard is load-bearing,
    not cosmetic: a foreign client's archive entry is a Claude-*shaped*
    envelope, so it lands inside the claude adapter's glob of the archive
    directory. Shape cannot tell the two apart — that is the point of the
    envelope — so the id does. Without this, the claude adapter would
    re-extract a codex envelope and overwrite the codex conversation with
    `client: claude`, and the codex adapter pointed at the same envelope would
    find none of its own record types and write an empty one. Foreign sessions
    are produced from the client's own file, by `process_foreign_session`.
    """
    if session_id.startswith(adapters.RESERVED_PREFIXES):
        return None

    owner = adapters.client_for_session_id(session_id)
    if client is None and owner != adapters.DEFAULT:
        return None
    client = client or owner

    CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
    dest = CONVERSATIONS_DIR / f"{session_id}.md"
    if dest.exists() and dest.stat().st_mtime >= jsonl_path.stat().st_mtime:
        return dest
    dest.write_text(adapters.extract_session(jsonl_path, client))
    return dest


def process_foreign_session(ref, quiet: bool = True) -> tuple[Path, Path] | None:
    """Archive one foreign session as an envelope and write its .md.

    Returns (envelope_path, md_path), or None for a subagent thread — those are
    captured inside their parent, same rule as Claude's `agent-` transcripts.
    """
    adapter = adapters.get(ref.client)
    meta, turns = adapter.parse(ref)
    if meta.is_subagent:
        return None

    envelope = adapters.write_envelope(meta, turns, ARCHIVE_DIR)
    expected = adapters.envelope.user_turn_count(turns)
    ok, detail = adapters.verify_envelope(envelope, expected)
    if not ok and not quiet:
        # Loud, because the failure it describes is otherwise invisible: a
        # session the server counts as zero-turn is filtered out of
        # narrative_coverage with no error anywhere.
        print(f"  WARN: {detail}")

    CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
    md = CONVERSATIONS_DIR / f"{meta.session_id}.md"
    md.write_text(adapters.render_conversation(meta, turns))
    return envelope, md


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ensure each JSONL transcript has an archived copy and a conversation.md sibling."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would happen without writing")
    parser.add_argument("--quiet", action="store_true",
                        help="Minimal output (for use from hooks)")
    parser.add_argument("--client", action="append", choices=adapters.names(),
                        help="Only process this client (repeatable). Default: all registered.")
    args = parser.parse_args()

    clients = args.client or adapters.names()
    total = archived = extracted = 0

    for client in clients:
        if client == adapters.DEFAULT:
            transcripts = find_transcripts(client)
            total += len(transcripts)
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
            continue

        refs = adapters.get(client).discover()
        total += len(refs)
        for ref in refs:
            if args.dry_run:
                continue
            try:
                if process_foreign_session(ref, quiet=args.quiet) is not None:
                    extracted += 1
                archived += 1
            except Exception as e:
                if not args.quiet:
                    print(f"  WARN: {ref.session_id}: {e}")

    if not args.quiet:
        print(f"Scanned {total} transcripts across {len(clients)} client(s) "
              f"({', '.join(clients)}); archived {archived}, produced "
              f"{extracted} conversation.md.")


if __name__ == "__main__":
    main()
