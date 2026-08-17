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
"""

import argparse
import sys
from pathlib import Path

import adapters


def extract(jsonl_path: Path, client: str = adapters.DEFAULT) -> str:
    """Render one transcript to the conversations/<sid>.md contract."""
    return adapters.extract_session(Path(jsonl_path), client)


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

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result)
    else:
        sys.stdout.write(result)


if __name__ == "__main__":
    main()
