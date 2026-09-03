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
    python process_transcripts.py --client codex --session <session-id>
"""

import argparse
import json
import shutil
from pathlib import Path

import adapters
from tools.memory_config import memory_root

DB_DIR = memory_root()
ARCHIVE_DIR = DB_DIR / "transcripts"
CONVERSATIONS_DIR = DB_DIR / "conversations"


def reattribute_dotted_conversations(*, dry_run: bool = False) -> tuple[int, int]:
    """Repair conversation frontmatter stamped with a dotted project name.

    The archived transcript (including foreign-client envelopes) retains the
    original cwd, so attribution can be corrected without re-extraction. The
    return value is ``(found, repaired)``; an unreadable transcript or one
    without a usable cwd is counted as found but left alone.
    """
    found = repaired = 0
    for md in sorted(CONVERSATIONS_DIR.glob("*.md")):
        try:
            text = md.read_text(errors="replace")
        except OSError:
            continue
        frontmatter_end = text.find("\n---\n", 4)
        if not text.startswith("---\n") or frontmatter_end < 0:
            continue
        lines = text[:frontmatter_end].splitlines()
        project_index = next(
            (i for i, line in enumerate(lines) if line.startswith("project: ")),
            None,
        )
        if project_index is None or not lines[project_index][len("project: "):].startswith("."):
            continue
        found += 1

        cwd = ""
        transcript = ARCHIVE_DIR / f"{md.stem}.jsonl"
        try:
            with transcript.open(errors="replace") as source:
                for line in source:
                    try:
                        record = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if isinstance(record, dict) and record.get("cwd"):
                        cwd = str(record["cwd"])
                        break
        except OSError:
            continue

        project = adapters.project_from_cwd(cwd)
        if not project:
            continue
        repaired += 1
        if dry_run:
            continue
        lines[project_index] = f"project: {project}"
        rewritten = "\n".join(lines) + text[frontmatter_end:]
        md.write_text(rewritten)
    return found, repaired


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
    source_path = getattr(adapter, "source_path", lambda item: item.path)(ref)
    envelope_path = ARCHIVE_DIR / f"{ref.session_id}.jsonl"
    md_path = CONVERSATIONS_DIR / f"{ref.session_id}.md"
    if getattr(adapter, "is_superseded", lambda item: False)(ref):
        # A previously ingested chain tail can become a fork prefix without
        # its source file changing.  D4 must outrank D8's mtime fast path or
        # the old envelope and .md duplicate the later tail forever.
        envelope_path.unlink(missing_ok=True)
        md_path.unlink(missing_ok=True)
        return None
    try:
        if envelope_path.exists() and envelope_path.stat().st_mtime >= source_path.stat().st_mtime:
            return envelope_path, md_path
    except OSError:
        # Parsing below provides the existing tolerant behaviour for a source
        # that disappears or cannot be read during a sweep.
        pass

    meta, turns = adapter.parse(ref)
    if meta.is_subagent:
        # Preserve the same D4 invariant for adapters that report it only
        # after parsing, and clean an artifact written before a later fork.
        envelope_path.unlink(missing_ok=True)
        md_path.unlink(missing_ok=True)
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
    md = md_path
    md.write_text(adapters.render_conversation(meta, turns))
    return envelope, md


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ensure each JSONL transcript has an archived copy and a conversation.md sibling."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would happen without writing")
    parser.add_argument("--quiet", action="store_true",
                        help="Minimal output (for use from hooks)")
    parser.add_argument("--client", action="append", choices=adapters.names(),
                        help="Only process this client (repeatable). Default: all registered.")
    parser.add_argument("--session", action="append", metavar="SID",
                        help="Only process this session (repeatable; requires one --client).")
    args = parser.parse_args()

    if args.session and len(args.client or []) != 1:
        parser.error("--session requires exactly one --client")

    # A session-targeted hook must not repair unrelated conversations as a
    # side effect.  The normal catch-all sweep retains that maintenance pass.
    if args.session:
        dotted_found = dotted_repaired = 0
    else:
        dotted_found, dotted_repaired = reattribute_dotted_conversations(
            dry_run=args.dry_run,
        )

    clients = args.client or adapters.names()
    total = archived = extracted = 0

    for client in clients:
        adapter = adapters.get(client)
        requested: set[str] | None = None
        if args.session:
            normalise = getattr(adapter, "session_id_for", lambda sid: sid)
            requested = {normalise(sid) for sid in args.session}

        if client == adapters.DEFAULT:
            transcripts = find_transcripts(client)
            if requested is not None:
                transcripts = [(path, sid) for path, sid in transcripts if sid in requested]
                found = {sid for _path, sid in transcripts}
                missing = requested - found
                if missing:
                    print(f"ERROR: unknown or no matching session: {', '.join(sorted(missing))}")
                    return 1
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
                    if args.session:
                        print(f"ERROR: session {sid}: {e}")
                        return 1
                    if not args.quiet:
                        print(f"  WARN: {sid}: {e}")
            continue

        refs = adapter.discover()
        if requested is not None:
            refs = [ref for ref in refs if ref.session_id in requested]
            found = {ref.session_id for ref in refs}
            missing = requested - found
            if missing:
                print(f"ERROR: unknown or no matching session: {', '.join(sorted(missing))}")
                return 1
        total += len(refs)
        for ref in refs:
            if args.dry_run:
                continue
            try:
                if process_foreign_session(ref, quiet=args.quiet) is not None:
                    extracted += 1
                archived += 1
            except Exception as e:
                if args.session:
                    print(f"ERROR: session {ref.session_id}: {e}")
                    return 1
                if not args.quiet:
                    print(f"  WARN: {ref.session_id}: {e}")

    if not args.quiet:
        action = "would repair" if args.dry_run else "repaired"
        print(f"Dotted project attributions: found {dotted_found}; "
              f"{action} {dotted_repaired}.")
        print(f"Scanned {total} transcripts across {len(clients)} client(s) "
              f"({', '.join(clients)}); archived {archived}, produced "
              f"{extracted} conversation.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
