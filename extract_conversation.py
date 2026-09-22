"""Extract clean conversation text from a session transcript.

Drops tool_use, tool_result, file-history-snapshot, hooks, and other plumbing.
Keeps user/assistant text content. Blocks where a tool_use/tool_result was
dropped get a [L:N] line ref so you can look up the raw JSONL entry.

The parsing and rendering now live in `adapters/` — `adapters.claude` reads
Claude Code's format, `adapters.render` writes the `.md`. This file is the
stable entry point that hooks, `process_transcripts.py` and
`backfill_conversations.py` call, kept so those callers don't have to know
which client produced a transcript.

Usage:
    python extract_conversation.py <jsonl_path>                  # to stdout
    python extract_conversation.py <jsonl_path> --output PATH    # to file
    python extract_conversation.py <jsonl_path> --output PATH --force
    python extract_conversation.py <path> --client claude        # explicit

--client selects the parsing adapter AND the frontmatter label. Archived
transcripts under transcripts/ are normalised flat JSONL whatever produced
them, so the default (claude) is the correct reader for an archive even when
the session came from grok or codex. Passing a --client whose adapter expects a
different on-disk shape now FAILS rather than writing an empty conversation.
"""

import argparse
import sys
from pathlib import Path

import adapters


def extract(jsonl_path: Path, client: str = adapters.DEFAULT) -> str:
    """Render one transcript to the conversations/<sid>.md contract."""
    return adapters.extract_session(Path(jsonl_path), client)


def _rendered_turns(rendered: str) -> int | None:
    """Turn count from the rendered frontmatter, or None when absent."""
    for line in rendered.splitlines()[:20]:
        if line.startswith("turns:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract conversation text from a session transcript")
    parser.add_argument("jsonl_path", type=Path)
    parser.add_argument("--output", type=Path, help="Write to file instead of stdout")
    parser.add_argument("--force", action="store_true", help="Rewrite output even if newer than source")
    parser.add_argument("--client", default=adapters.DEFAULT,
                        choices=adapters.names(),
                        help="Which client produced the transcript")
    args = parser.parse_args()

    if not args.jsonl_path.exists():
        print(f"Error: {args.jsonl_path} does not exist", file=sys.stderr)
        sys.exit(1)

    if args.output and args.output.exists() and not args.force:
        if args.output.stat().st_mtime >= args.jsonl_path.stat().st_mtime:
            return

    result = extract(args.jsonl_path, args.client)

    # A wrong --client renders an empty conversation instead of failing.
    # adapters.grok expects a session DIRECTORY and looks for
    # <path>/chat_history.jsonl; handed the flat archived .jsonl it finds
    # nothing, returns `turns: 0`, and the stub is written over a good
    # conversation.  That destroyed 131 KB on 2026-09-22 and exited 0.
    #
    # Re-extraction is exactly where this bites: narrative_coverage reports
    # sessions that grew after being merged, and draining one REQUIRES
    # regenerating its conversation.
    #
    # Note the underlying conflation, not fixed here: --client selects both the
    # parsing adapter AND the frontmatter label.  Archived transcripts are
    # normalised flat JSONL and every record carries its origin in a "client"
    # field, so the adapter should follow the file's shape while the label is
    # read from the record.  Splitting that touches hooks, process_transcripts
    # and backfill_conversations, so this guard only stops the data loss.
    turns = _rendered_turns(result)
    if turns == 0 and args.jsonl_path.stat().st_size > 0:
        print(
            f"Error: extraction produced 0 turns from a non-empty transcript "
            f"({args.jsonl_path}) using --client {args.client}. Refusing to write.\n"
            f"Archived transcripts are flat JSONL regardless of origin client; "
            f"'--client {adapters.DEFAULT}' reads them.",
            file=sys.stderr,
        )
        sys.exit(2)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result)
    else:
        sys.stdout.write(result)


if __name__ == "__main__":
    main()
