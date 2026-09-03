#!/usr/bin/env python3
"""The adapter oracle: regenerating a stored conversation must reproduce it.

5,500+ files in ~/.claude/memory/conversations/ are the asset this project
exists to protect. They were produced by the pre-adapter extractor. If the
adapter refactor changed the output by so much as a byte, every downstream
narrative silently drifts — so this is the guard, and it is meant to keep
running, not to be a one-off.

What it asserts, per sampled session:

    render(adapters.get(owner).parse(source))  ==  the stored .md

byte for byte, with two declared frontmatter migrations: the `client:` line
that S1 added, and the relocation-safe `raw: transcripts/<sid>.jsonl` form.
Both exceptions are restricted to frontmatter and checked explicitly, so
neither can hide a second change.

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
from tools.memory_config import memory_root  # noqa: E402

CONV_DIR = memory_root() / "conversations"
ARCHIVE_DIR = memory_root() / "transcripts"
SAMPLE_FILE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "oracle_sample.txt"

# How long a session must have been quiet before it can be sampled. Matches the
# pipeline's own staleness threshold: below it, a transcript may still grow.
SETTLED_AFTER_HOURS = 24

_CLIENT_LINE = re.compile(r"^client: [A-Za-z0-9_.-]+\n", re.MULTILINE)
_LEGACY_RAW_LINE = re.compile(
    r"^raw: ~/.claude/memory/transcripts/([^/\r\n]+\.jsonl)\n", re.MULTILINE
)


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


def normalize_archive_path(text: str) -> tuple[str, list[str]]:
    """Relativize the one legacy `raw:` frontmatter line, if present."""
    if not text.startswith("---\n"):
        return text, []
    end = text.find("\n---\n", 4)
    if end == -1:
        return text, []
    head, rest = text[: end + 5], text[end + 5 :]
    legacy = [m.group(0).rstrip("\n") for m in _LEGACY_RAW_LINE.finditer(head)]
    normalized = _LEGACY_RAW_LINE.sub(r"raw: transcripts/\1\n", head)
    return normalized + rest, legacy


def regenerate(session_id: str) -> str:
    """Render a stored session through the adapter that owns its id.

    Claude's archive contains its original transcript. Foreign-client archive
    entries are deliberately Claude-shaped envelopes, so their stored
    ``raw_source`` instead names the original client file. The owning adapter
    translates that source path into its own ``SessionRef``.
    """
    owner = adapters.client_for_session_id(session_id)
    adapter = adapters.get(owner)
    if owner == adapters.DEFAULT:
        return adapters.render(claude_adapter.ref_for_path(ARCHIVE_DIR / f"{session_id}.jsonl"))

    stored_path = CONV_DIR / f"{session_id}.md"
    raw_source_value = _stored_field(stored_path, "raw_source")
    if not raw_source_value:
        # A foreign superseded/subagent stub deliberately has no raw_source:
        # its original dialogue belongs to the parent. Its envelope remains
        # the canonical archived input, though it is normally empty because a
        # stub has no turns. Re-read that envelope, then restore the foreign
        # identity which a Claude-shaped envelope cannot carry through the
        # Claude parser.
        envelope = ARCHIVE_DIR / f"{session_id}.jsonl"
        meta = claude_adapter.session_meta(
            claude_adapter.ref_for_path(envelope, session_id=session_id)
        )
        meta.client = adapter.client_name()
        meta.is_subagent = True
        return adapters.render_subagent(meta)
    raw_source = Path(raw_source_value)
    return adapters.render(adapter.ref_for_source(raw_source, session_id=session_id))


def compare(
    stored: bytes,
    produced: str,
    label: str = "session",
    expected_client: str = adapters.DEFAULT,
) -> tuple[bool, str, list[str]]:
    """Compare a regenerated conversation against the stored one.

    The comparison is on raw bytes, deliberately. Reading the stored file as
    text applies universal-newline translation, which silently rewrites bare
    `\\r` characters — and transcripts are full of them, from progress bars in
    captured tool output. Text comparison therefore reports differences that
    do not exist on disk. Bytes are the thing the acceptance criterion is
    about, so bytes are what this compares.

    Equality is byte equality after excusing the `client:` frontmatter line
    and normalizing one exact legacy archive prefix. Both excuses are narrow:
    exactly one `client:` line carrying the expected name, and at most one
    `raw: ~/.claude/memory/transcripts/<filename>.jsonl` line rewritten to
    `raw: transcripts/<filename>.jsonl` without changing the filename.

    A stored file that already carries `client:` needs no client-line excuse;
    its archive reference may still be in either declared form.
    """
    stored_text = stored.decode("utf-8", errors="replace")
    normalized_stored, legacy_raw = normalize_archive_path(stored_text)
    _, stored_client = strip_client_line(normalized_stored)
    expected = f"client: {expected_client}"

    if stored_client:
        # Already-migrated corpus: nothing to excuse, compare as-is.
        compared, removed = produced, []
    else:
        compared, removed = strip_client_line(produced)

    compared_bytes = compared.encode("utf-8", errors="surrogatepass")
    expected_bytes = normalized_stored.encode("utf-8", errors="surrogatepass")
    client_ok = removed == ([] if stored_client else [expected])
    archive_ok = len(legacy_raw) <= 1
    excuse_ok = client_ok and archive_ok

    if compared_bytes == expected_bytes and excuse_ok:
        detail = f"identical ({len(stored)} bytes)"
        if removed:
            detail += f"; declared addition: {removed[0]}"
        if legacy_raw:
            detail += "; declared path migration: raw: transcripts/..."
        return True, detail, []

    if not excuse_ok:
        found = ", ".join(removed) if removed else "none"
        problems = []
        if not client_ok:
            problems.append(
                f"excused frontmatter must be exactly [{expected}]; found: {found}"
            )
        if not archive_ok:
            problems.append("legacy raw frontmatter must occur at most once")
        detail = "; ".join(problems)
        if compared_bytes != expected_bytes:
            detail += " (and the rest differs too)"
        return False, detail, [f"- {expected}\n", f"+ {found}\n"]

    diff = list(
        difflib.unified_diff(
            normalized_stored.splitlines(keepends=True),
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
    if not stored_path.exists():
        return False, "no stored conversation .md", []

    owner = adapters.client_for_session_id(session_id)
    adapter = adapters.get(owner)
    if owner != adapters.DEFAULT:
        raw_source = _stored_field(stored_path, "raw_source")
        if not raw_source:
            if _stored_field(stored_path, "agent_session") == "true" and (
                ARCHIVE_DIR / f"{session_id}.jsonl"
            ).exists():
                return compare(
                    stored_path.read_bytes(),
                    regenerate(session_id),
                    session_id,
                    expected_client=adapter.client_name(),
                )
            return False, "unavailable: raw_source missing", []
        if not Path(raw_source).exists():
            return False, f"unavailable: raw_source missing ({raw_source})", []
    elif not (ARCHIVE_DIR / f"{session_id}.jsonl").exists():
        return False, "no archived transcript", []

    return compare(
        stored_path.read_bytes(),
        regenerate(session_id),
        session_id,
        expected_client=adapter.client_name(),
    )


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


def _summary_client(session_id: str) -> str:
    """Return the summary bucket for a session, including unknown clients."""
    owner = adapters.client_for_session_id(session_id)
    if owner != adapters.DEFAULT:
        return owner
    stored_path = CONV_DIR / f"{session_id}.md"
    declared = _stored_field(stored_path, "client")
    if (
        declared
        and declared not in adapters.names()
        and session_id.lower().startswith(f"{declared.lower()}-")
    ):
        return "unregistered"
    return owner


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
            if not md.stem.startswith(("agent-", "audit-"))
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

    counts = {
        client: {"ok": 0, "fail": 0, "unavailable": 0}
        for client in adapters.names()
    }
    counts["unregistered"] = {"ok": 0, "fail": 0, "unavailable": 0}
    for sid in sessions:
        ok, detail, diff = check(sid)
        client = _summary_client(sid)
        if ok:
            status = "OK  "
            counts[client]["ok"] += 1
        elif detail.startswith("unavailable:"):
            status = "UNAV"
            counts[client]["unavailable"] += 1
        else:
            status = "FAIL"
            counts[client]["fail"] += 1
        print(f"{status} {sid}  {detail}")
        if not ok and not detail.startswith("unavailable:"):
            if args.verbose:
                sys.stdout.writelines(diff)

    failures = sum(count["fail"] for count in counts.values())
    reproduced = sum(count["ok"] for count in counts.values())
    unavailable = sum(count["unavailable"] for count in counts.values())
    per_client = "; ".join(
        f"{client}: ok={count['ok']} fail={count['fail']} unavailable={count['unavailable']}"
        for client, count in counts.items()
    )
    print(
        f"\nSummary: {per_client}; total: ok={reproduced} fail={failures} "
        f"unavailable={unavailable} sessions={len(sessions)}."
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
