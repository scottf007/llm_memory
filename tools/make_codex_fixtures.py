#!/usr/bin/env python3
"""Build sanitised codex fixtures from the real sessions on this machine.

There is no stored codex corpus, so S1's byte-for-byte oracle has nothing to
compare against. The substitute is golden fixtures — but the real rollout files
are Scott's actual work, and this repository is public, so they cannot be
committed as-is.

What this does instead: keep the **structure** and throw away the **content**.
Every record type, payload type, item type, role, ordering and timestamp is
preserved exactly; every human-readable string is replaced with a deterministic
placeholder that records only its length. A parser bug shows up in the
structure, so the fixtures still bite; the prose does not survive, so nothing
leaks.

Selection is by feature coverage, not by sampling: the point of ten fixtures is
to hit ten different shapes (both dialogue dialects, subagent threads, tool
mechanisms, compaction, unattributed cwd, the degenerate cases), not to be
statistically representative of a corpus this small.

Usage:
    python3 tools/make_codex_fixtures.py            # rebuild fixtures + expectations
    python3 tools/make_codex_fixtures.py --list     # show the selection and why
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import adapters  # noqa: E402
from adapters import codex  # noqa: E402

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "codex"

# An allow-list, not a deny-list. A first attempt enumerated the keys holding
# prose, and it leaked: absolute paths, filenames and repository structure came
# through under keys nobody thought to list (`workspace_roots`, `path`,
# `command`, nested `results`). With a deny-list you have to be right about
# every key the client will ever emit, including the ones a future version adds.
#
# So the rule is inverted: a string survives only if it looks like a structural
# token — short, and made of the characters an enum, id or timestamp uses.
# Everything else, including anything with a space or a slash in it, becomes a
# length-stamped placeholder.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:+-]{1,40}$")
# UUIDs pass the token test but tie a fixture back to a real session on a real
# machine, so they are stamped wherever they appear, key or value.
_UUID_RE = re.compile(
    r"^(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|[0-9a-f]{40})$")  # git SHAs too — they name commits in real repositories

# Keys that identify a machine or a person even when they look like tokens.
_ID_KEYS = {"id", "call_id", "turn_id", "session_id", "installation_id", "account_id",
            "encrypted_content", "agent_nickname", "forked_from_id", "parent_thread_id"}

# Keys whose values name something on disk. A bare filename passes the token
# test — `usernotes.md` has no slash and 13 characters — so file and directory
# names need forcing regardless of shape. The parser keys off record and
# payload types, never off these, so nothing testable is lost.
_PATH_KEYS = {"name", "path", "file", "filename", "dir", "directory", "cwd",
              "root", "roots", "workspace_roots", "agent_path", "repo", "branch",
              "url", "origin"}


def _placeholder(key: str, value: str) -> str:
    return f"<{key or 'str'}:{len(value)}>"


def _placeholder_project(name: str) -> str:
    """Map a real project directory name to a stable, content-free stand-in.

    A real project name is a structural token by every rule above — short, no
    space, no slash — so the allow-list alone cannot catch it. It goes out the
    same way a filename does: forced regardless of shape, here rather than in
    `sanitise()` because the cwd rewrite already bypasses that path. The
    digest is deterministic so re-running this script against the same real
    session reproduces the same fixture bytes.
    """
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return f"project-{digest}"


def _sanitise_key(key: str) -> str:
    """Dict keys need the same treatment as values.

    Codex keys some structures *by file path*, so the first version of this
    leaked absolute paths through the key side while carefully scrubbing the
    values. The digest keeps distinct keys distinct without carrying the
    original, so a map does not silently collapse to one entry.
    """
    if _TOKEN_RE.match(key) and not _UUID_RE.match(key):
        return key
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
    return f"<key:{len(key)}:{digest}>"


def sanitise(node, key: str = ""):
    """Recursively replace anything that is not a structural token."""
    if isinstance(node, dict):
        return {_sanitise_key(str(k)): sanitise(v, str(k)) for k, v in node.items()}
    if isinstance(node, list):
        return [sanitise(v, key) for v in node]
    if isinstance(node, str):
        if key in _ID_KEYS or _UUID_RE.match(node):
            # Ids are structural — the parser keys off their presence — but
            # they identify a machine, so keep the shape, not the value.
            return f"<{key or 'uuid'}:{len(node)}>"
        if key in _PATH_KEYS:
            return _placeholder(key, node)
        if _TOKEN_RE.match(node):
            return node
        return _placeholder(key, node)
    return node


def sanitise_record(rec: dict, cwd_replacement: str | None) -> dict:
    out = sanitise(rec)
    # cwd is load-bearing: project attribution is derived from it. Keep the
    # convention, drop the identity.
    if cwd_replacement is not None:
        for holder in (out, out.get("payload") if isinstance(out.get("payload"), dict) else {}):
            if isinstance(holder, dict) and "cwd" in holder:
                holder["cwd"] = cwd_replacement
    return out


def _features(path: Path) -> dict:
    """What shapes does this session exercise?"""
    feats: set[str] = set()
    n_user_em = n_user_ic = 0
    metas = 0
    with open(path, errors="replace") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                feats.add("malformed-line")
                continue
            if not isinstance(rec, dict):
                continue
            t = rec.get("type")
            p = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
            pt = p.get("type")
            if t == "session_meta":
                metas += 1
                if p.get("agent_path") or p.get("parent_thread_id"):
                    feats.add("subagent")
                if not adapters.project_from_cwd(p.get("cwd") or ""):
                    feats.add("unattributed")
            elif t == "compacted":
                feats.add("compacted")
            elif t == "inter_agent_communication_metadata":
                feats.add("inter-agent")
            elif t == "event_msg":
                if pt == "user_message":
                    n_user_em += 1
                    feats.add("dialect:event_msg")
                elif pt == "agent_message":
                    feats.add("dialect:event_msg")
                elif pt == "item_completed":
                    it = p.get("item") if isinstance(p.get("item"), dict) else {}
                    if it.get("type") in ("UserMessage", "AgentMessage"):
                        n_user_ic += 1
                        feats.add("dialect:item_completed")
                    elif it.get("type") in codex._TOOL_ITEM_TYPES:
                        feats.add(f"tool_item:{it.get('type')}")
            elif t == "response_item":
                if pt in codex._TOOL_CALL_TYPES:
                    feats.add(f"tool:{pt}")
                elif pt == "agent_message":
                    feats.add("response_item:agent_message")
    if metas > 1:
        feats.add("multi-session_meta")
    if n_user_em + n_user_ic == 0:
        feats.add("zero-user-turns")
    return {"features": feats, "size": path.stat().st_size,
            "n_user": n_user_em + n_user_ic}


# Sanitising shrinks a rollout to roughly a fifth, but the richest codex
# session on disk is 36 MB and still would not fit. Rather than exclude the one
# session that exercises the TUI dialect at length, oversized sources are
# committed as a **prefix**: the first records, which is itself a structurally
# valid session that simply ends early. Truncation is recorded in the filename.
MAX_FIXTURE_BYTES = 200_000


def select(limit: int = 10) -> list[tuple[Path, list[str]]]:
    """Cover every observed shape first, then spread across conversation depth.

    Two phases because they answer different questions. Greedy cover answers
    "does the parser handle every record shape this client emits" and saturates
    after a handful of files. The top-up answers "does it handle real sessions
    at real length", which is what catches a bug that only appears after a few
    hundred records.

    Depth, not size, drives the top-up: this corpus is lopsided — 114 of 123
    sessions are one-turn `codex exec` runs against a single project — so
    sampling by size or at random would return ten near-identical files and
    call it diversity. The multi-turn sessions are the rare ones and they all
    get a slot.
    """
    catalogue = {ref.path: _features(ref.path) for ref in codex.discover()}
    chosen: list[tuple[Path, list[str]]] = []
    seen: set[str] = set()

    # Phase 1 — greedy feature cover, smallest file breaking ties.
    while len(chosen) < limit:
        best = None
        for path, info in sorted(catalogue.items()):
            if any(path == c for c, _ in chosen):
                continue
            gain = info["features"] - seen
            key = (len(gain), -info["size"])
            if gain and (best is None or key > best[0]):
                best = (key, path, sorted(gain))
        if best is None:
            break
        _, path, gain = best
        chosen.append((path, gain))
        seen |= set(gain)

    # Phase 2 — deepest conversations first, then a size spread over whatever
    # slots remain.
    picked = {c for c, _ in chosen}
    # Subagent threads render as a stub whatever their depth, so a second one
    # adds a fixture and no coverage. Phase 1 already took one.
    rest = sorted((p for p in catalogue
                   if p not in picked and "subagent" not in catalogue[p]["features"]),
                  key=lambda p: (-catalogue[p]["n_user"], catalogue[p]["size"], p.name))
    deep = [p for p in rest if catalogue[p]["n_user"] > 1]
    shallow = [p for p in rest if catalogue[p]["n_user"] <= 1]

    for path in deep:
        if len(chosen) >= limit:
            break
        chosen.append((path, [f"depth:{catalogue[path]['n_user']}-user-turns"]))
        picked.add(path)

    if shallow and len(chosen) < limit:
        step = max(1, len(shallow) // (limit - len(chosen)))
        for i in range(0, len(shallow), step):
            if len(chosen) >= limit:
                break
            chosen.append((shallow[i], ["(size spread)"]))

    return chosen


def build(limit: int = 10) -> list[str]:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for stale in FIXTURE_DIR.glob("*"):
        stale.unlink()

    written = []
    for index, (path, gain) in enumerate(select(limit), 1):
        ref = codex.ref_for_path(path)
        meta, _ = codex.parse(ref)
        # Rewrite cwd to a stable neutral path that still carries the
        # /projects/<name>/ convention, so attribution stays testable — but
        # the name itself is a placeholder, not the real project directory.
        cwd = (f"/home/user/projects/{_placeholder_project(meta.project)}"
               if meta.project else "/home/user")
        lines = []
        budget = MAX_FIXTURE_BYTES
        truncated = False
        with open(path, errors="replace") as f:
            for line in f:
                if budget <= 0:
                    truncated = True
                    break
                try:
                    rec = json.loads(line)
                except Exception:
                    out_line = line.rstrip("\n")
                else:
                    out_line = json.dumps(
                        rec if not isinstance(rec, dict) else sanitise_record(rec, cwd),
                        ensure_ascii=True)
                lines.append(out_line)
                budget -= len(out_line) + 1

        stem = '-'.join(sorted(gain)[:2]).replace(':', '_').replace('/', '_')
        base = f"{index:02d}-{stem}"[:56]
        tmp = FIXTURE_DIR / f"{base}.tmp.jsonl"
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # Name by what the fixture actually contains, not by what its source
        # contained — a truncated 75-turn session is not a 75-turn fixture, and
        # a filename that says otherwise is a lie a reader would believe.
        probe_meta, probe_turns = codex.parse(codex.ref_for_path(tmp, session_id="codex-probe"))
        depth = sum(1 for t in probe_turns if t.role == "user" and t.text)
        name = f"{base}-u{depth}{'-prefix' if truncated else ''}"
        fixture = FIXTURE_DIR / f"{name}.jsonl"
        tmp.rename(fixture)

        # Pin the expected render of the sanitised fixture.
        fref = codex.ref_for_path(fixture, session_id=f"codex-{name}")
        fmeta, fturns = codex.parse(fref)
        fmeta.raw_source = f"tests/fixtures/codex/{fixture.name}"
        (FIXTURE_DIR / f"{name}.expected.md").write_text(
            adapters.render_conversation(fmeta, fturns), encoding="utf-8")
        (FIXTURE_DIR / f"{name}.expected.envelope.jsonl").write_text(
            adapters.render_envelope(fmeta, fturns), encoding="utf-8")

        written.append(
            f"{name}  covers={','.join(gain)}  user_turns={depth}  "
            f"fixture={fixture.stat().st_size}B  source={path.stat().st_size}B"
            f"{'  TRUNCATED' if truncated else ''}")
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="Show the selection without writing")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    if not codex.SESSIONS_DIR.exists():
        print(f"No codex sessions at {codex.SESSIONS_DIR}; nothing to build.", file=sys.stderr)
        return 0

    if args.list:
        for path, gain in select(args.limit):
            print(f"{path.name}\n    adds: {', '.join(gain)}")
        return 0

    for line in build(args.limit):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
