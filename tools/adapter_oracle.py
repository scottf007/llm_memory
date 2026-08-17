#!/usr/bin/env python3
"""The adapter oracle: regenerating a stored conversation must reproduce it.

5,500+ files in ~/.claude/memory/conversations/ are the asset this project
exists to protect. They were produced by the pre-adapter extractor. If the
adapter refactor changed the output by so much as a byte, every downstream
narrative silently drifts — so this is the guard, and it is meant to keep
running, not to be a one-off.

What it asserts, per sampled session:

    render(adapters.claude.parse(transcript))  ==  the stored .md

byte for byte, with exactly one declared exception: the `client:` frontmatter
line that S1 added. That line is removed from the regenerated text before
comparison *and* checked to be the only difference — so it cannot hide a
second change.

Usage:
    python tools/adapter_oracle.py                  # run the pinned sample
    python tools/adapter_oracle.py --all            # every stored conversation
    python tools/adapter_oracle.py --select 20      # draw a fresh sample
    python tools/adapter_oracle.py --select 20 --write-sample
    python tools/adapter_oracle.py --verbose        # show diffs

Exit code is 0 only when every sampled session matches.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import adapters  # noqa: E402
from adapters import claude as claude_adapter  # noqa: E402

CONV_DIR = Path.home() / ".claude" / "memory" / "conversations"
ARCHIVE_DIR = Path.home() / ".claude" / "memory" / "transcripts"
SAMPLE_FILE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "oracle_sample.txt"

# How long a session must have been quiet before it can be sampled. Matches the
# pipeline's own staleness threshold: below it, a transcript may still grow.
SETTLED_AFTER_HOURS = 24

_CLIENT_LINE = re.compile(r"^client: [A-Za-z0-9_.-]+\n", re.MULTILINE)


def strip_client_line(text: str) -> tuple[str, list[str]]:
    """Remove `client:` frontmatter lines. Returns (text, removed lines).

    Only the frontmatter is touched: a line in the conversation body that
    happens to read `client: something` must not be eaten, or the oracle would
    be masking a real difference.
    """
    if not text.startswith("---\n"):
        return text, []
    end = text.find("\n---\n", 4)
    if end == -1:
        return text, []
    head, rest = text[: end + 5], text[end + 5 :]
    removed = [m.group(0).rstrip("\n") for m in _CLIENT_LINE.finditer(head)]
    return _CLIENT_LINE.sub("", head) + rest, removed


def regenerate(session_id: str) -> str:
    """Render a stored session through the adapter, as production would."""
    return adapters.render(claude_adapter.ref_for_path(ARCHIVE_DIR / f"{session_id}.jsonl"))


def compare(stored: bytes, produced: str, label: str = "session") -> tuple[bool, str, list[str]]:
    """Compare a regenerated conversation against the stored one.

    The comparison is on raw bytes, deliberately. Reading the stored file as
    text applies universal-newline translation, which silently rewrites bare
    `\\r` characters — and transcripts are full of them, from progress bars in
    captured tool output. Text comparison therefore reports differences that
    do not exist on disk. Bytes are the thing the acceptance criterion is
    about, so bytes are what this compares.

    Equality is byte equality after removing the declared `client:`
    frontmatter line. Nothing else is normalised: whitespace, ordering and
    encoding differences all fail.
    """
    compared, removed = strip_client_line(produced)
    compared_bytes = compared.encode("utf-8", errors="surrogatepass")
    if compared_bytes == stored:
        detail = f"identical ({len(stored)} bytes)"
        if removed:
            detail += f"; declared addition: {', '.join(removed)}"
        return True, detail, []

    diff = list(
        difflib.unified_diff(
            stored.decode("utf-8", errors="replace").splitlines(keepends=True),
            compared.splitlines(keepends=True),
            fromfile=f"stored/{label}.md",
            tofile=f"regenerated/{label}.md",
            n=2,
        )
    )
    return False, f"{len(diff)} diff lines", diff


def check(session_id: str) -> tuple[bool, str, list[str]]:
    """Return (ok, detail, diff_lines) for one stored session."""
    stored_path = CONV_DIR / f"{session_id}.md"
    jsonl_path = ARCHIVE_DIR / f"{session_id}.jsonl"
    if not stored_path.exists():
        return False, "no stored conversation .md", []
    if not jsonl_path.exists():
        return False, "no archived transcript", []

    return compare(stored_path.read_bytes(), regenerate(session_id), session_id)


def _stored_field(path: Path, field: str) -> str:
    """Read one frontmatter field without loading the whole file."""
    with open(path, "r", errors="replace") as f:
        head = f.read(2048)
    if not head.startswith("---\n"):
        return ""
    end = head.find("\n---\n", 4)
    block = head[4:end] if end != -1 else head[4:]
    for line in block.splitlines():
        if line.startswith(f"{field}: "):
            return line[len(field) + 2 :].strip()
    return ""


def select(count: int) -> list[str]:
    """Draw a deterministic, deliberately diverse sample.

    Diversity is the point: a sample of twenty recent, medium-length sessions
    from one project would pass while the extractor was broken for everything
    else. So the draw is stratified by age and by size, spread across as many
    projects as the corpus offers, and is fully deterministic — same corpus,
    same twenty files, so a failure is reproducible.
    """
    # A session that is still running has a stored .md that is already out of
    # date, and re-drawing tomorrow would pick a different one. Neither makes a
    # useful fixture, so the draw only considers sessions that have settled.
    settled_before = datetime.now(timezone.utc) - timedelta(hours=SETTLED_AFTER_HOURS)

    candidates = []
    for md in sorted(CONV_DIR.glob("*.md")):
        sid = md.stem
        if sid.startswith(("agent-", "audit-")):
            continue
        jsonl = ARCHIVE_DIR / f"{sid}.jsonl"
        if not jsonl.exists():
            continue
        if datetime.fromtimestamp(max(md.stat().st_mtime, jsonl.stat().st_mtime), timezone.utc) > settled_before:
            continue
        candidates.append((
            sid,
            md.stat().st_size,
            _stored_field(md, "started"),
            _stored_field(md, "project"),
            int(_stored_field(md, "turns") or 0),
        ))

    if not candidates:
        return []

    # Empty sessions (no turns, no timestamps) are a real edge case worth one
    # slot, but the corpus holds hundreds of near-identical ones and letting
    # them win the "oldest" and "shortest" strata would waste a quarter of the
    # sample on the same 145-byte stub.
    empty = [c for c in candidates if c[4] == 0]
    real = [c for c in candidates if c[4] > 0]
    dated = [c for c in real if c[2]]

    by_age = sorted(dated, key=lambda c: (c[2], c[0]))
    by_size = sorted(real, key=lambda c: (c[1], c[0]))
    fifth = max(1, count // 5)

    picked: list[str] = []
    seen: set[str] = set()

    def take(rows) -> None:
        for sid, *_ in rows:
            if len(picked) >= count:
                return
            if sid not in seen:
                seen.add(sid)
                picked.append(sid)

    take(sorted(empty, key=lambda c: c[0])[:1])  # one degenerate session
    take(by_age[:fifth])                         # oldest
    take(by_age[-fifth:])                        # newest
    take(by_size[:fifth])                        # shortest
    take(by_size[-fifth:])                       # longest

    # Top up by walking projects round-robin, so the sample spans as many
    # projects as possible rather than whatever the strata happened to hit.
    by_project: dict[str, list[str]] = {}
    for row in by_age:
        by_project.setdefault(row[3] or "(unattributed)", []).append(row[0])
    order = sorted(by_project, key=lambda p: (-len(by_project[p]), p))
    depth = 0
    while len(picked) < count and any(len(v) > depth for v in by_project.values()):
        for project in order:
            rows = by_project[project]
            if depth < len(rows):
                take([(rows[depth],)])
            if len(picked) >= count:
                break
        depth += 1

    return picked


def load_sample() -> list[str]:
    if not SAMPLE_FILE.exists():
        return []
    return [
        line.strip()
        for line in SAMPLE_FILE.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]


def describe(session_id: str) -> str:
    md = CONV_DIR / f"{session_id}.md"
    if not md.exists():
        return session_id
    return (
        f"{session_id}  project={_stored_field(md, 'project') or '-'}  "
        f"turns={_stored_field(md, 'turns') or '-'}  "
        f"bytes={md.stat().st_size}  started={_stored_field(md, 'started') or '-'}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true", help="Check every stored conversation")
    parser.add_argument("--select", type=int, metavar="N", help="Draw a fresh sample of N instead of the pinned one")
    parser.add_argument("--write-sample", action="store_true", help="Persist the drawn sample as the pinned one")
    parser.add_argument("--verbose", action="store_true", help="Print diffs for failures")
    parser.add_argument("--list", action="store_true", help="Print the sample and exit")
    args = parser.parse_args()

    if not CONV_DIR.exists():
        print(f"No conversation corpus at {CONV_DIR}; nothing to check.", file=sys.stderr)
        return 0

    if args.all:
        sessions = [
            md.stem
            for md in sorted(CONV_DIR.glob("*.md"))
            if not md.stem.startswith(("agent-", "audit-")) and (ARCHIVE_DIR / f"{md.stem}.jsonl").exists()
        ]
    elif args.select:
        sessions = select(args.select)
    else:
        sessions = load_sample() or select(20)

    if args.write_sample:
        SAMPLE_FILE.parent.mkdir(parents=True, exist_ok=True)
        SAMPLE_FILE.write_text(
            "# Pinned sample for tools/adapter_oracle.py.\n"
            "# Stratified by age and size across projects; see select() for how.\n"
            + "".join(f"{sid}\n" for sid in sessions)
        )
        print(f"Wrote {len(sessions)} session ids to {SAMPLE_FILE}")

    if args.list:
        for sid in sessions:
            print(describe(sid))
        return 0

    if not sessions:
        print("Sample is empty; nothing to check.", file=sys.stderr)
        return 0

    failures = 0
    for sid in sessions:
        ok, detail, diff = check(sid)
        status = "OK  " if ok else "FAIL"
        print(f"{status} {sid}  {detail}")
        if not ok:
            failures += 1
            if args.verbose:
                sys.stdout.writelines(diff)

    print(f"\n{len(sessions) - failures}/{len(sessions)} sessions reproduce byte-for-byte.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
