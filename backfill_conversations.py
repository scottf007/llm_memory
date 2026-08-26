"""
One-shot backfill: extract `conversation.md` for every MAIN-session archived
JSONL in ~/.claude/memory/transcripts/ that doesn't already have a fresh .md
in ~/.claude/memory/conversations/.

- Skips subagent transcripts (stems starting with 'agent-').
- Skips sessions whose .md already exists and is newer than the source JSONL.
- Catches per-file errors so one malformed transcript doesn't halt the run.
- Idempotent and re-runnable.
"""

from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

from extract_conversation import extract
from tools.memory_config import memory_root


TRANSCRIPTS_DIR = memory_root() / "transcripts"
CONVERSATIONS_DIR = memory_root() / "conversations"


def main() -> int:
    if not TRANSCRIPTS_DIR.is_dir():
        print(f"Error: transcripts dir not found: {TRANSCRIPTS_DIR}", file=sys.stderr)
        return 1

    CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)

    jsonl_files = sorted(TRANSCRIPTS_DIR.glob("*.jsonl"))
    total = len(jsonl_files)
    print(f"Found {total} JSONL files in {TRANSCRIPTS_DIR}")

    processed = 0
    skipped_agent = 0
    skipped_exists = 0
    errors: list[tuple[str, str]] = []

    start = time.time()

    for i, jsonl_path in enumerate(jsonl_files, 1):
        stem = jsonl_path.stem

        if stem.startswith("agent-"):
            skipped_agent += 1
            continue

        out_path = CONVERSATIONS_DIR / f"{stem}.md"

        try:
            if out_path.exists() and out_path.stat().st_mtime >= jsonl_path.stat().st_mtime:
                skipped_exists += 1
            else:
                result = extract(jsonl_path)
                out_path.write_text(result)
                processed += 1
        except Exception as e:  # noqa: BLE001 -- backfill must be resilient
            errors.append((jsonl_path.name, f"{type(e).__name__}: {e}"))

        if i % 100 == 0:
            elapsed = time.time() - start
            rate = i / elapsed if elapsed > 0 else 0.0
            print(
                f"[{i}/{total}] processed={processed} "
                f"skipped_exists={skipped_exists} skipped_agent={skipped_agent} "
                f"errors={len(errors)} ({rate:.1f} files/s)"
            )

    elapsed = time.time() - start
    print()
    print("=" * 60)
    print("Backfill complete")
    print("=" * 60)
    print(f"Total JSONL files seen:    {total}")
    print(f"Processed (wrote .md):     {processed}")
    print(f"Skipped (already exists):  {skipped_exists}")
    print(f"Skipped (agent-* subagent):{skipped_agent}")
    print(f"Errors:                    {len(errors)}")
    print(f"Elapsed:                   {elapsed:.1f}s")

    if errors:
        print()
        print("First errors:")
        for name, msg in errors[:5]:
            print(f"  {name}: {msg}")
        if len(errors) > 5:
            print(f"  ... and {len(errors) - 5} more")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
