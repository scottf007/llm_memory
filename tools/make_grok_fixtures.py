#!/usr/bin/env python3
"""Build sanitised grok fixtures from the real sessions on this machine.

Mirrors `make_codex_fixtures.py`'s approach — keep the structure, throw away
the content — adapted to two differences from codex:

1. A grok session is a **directory** (`chat_history.jsonl`, `summary.json`,
   `events.jsonl`), not a single file, so a fixture is a directory too.
2. `adapters/grok.py` strips exactly one outer `<user_query>`/wrapper tag from
   kept user text (D5 of `docs/design/grok-ingestion-2026-09-03.md`). A blind
   "replace every non-token string with a length placeholder" — codex's rule
   — would destroy the wrapper tags a test needs to see, so the placeholder
   for a wrapped text block keeps the wrapper and stamps only what is inside
   it. The wrapper names (`user_query`, `system-reminder`) are protocol
   markup, not content — exactly as `"type": "user"` is not content — so
   keeping them is not a hygiene exception.

Selection here is a fixed manifest, not codex's greedy feature-cover search:
the real corpus was already censused by feature (see the design note's §1)
before this tool was written, so the sessions below are named by hand rather
than rediscovered by an algorithm every run. `--list` explains each choice.

This tool writes the **inputs** of each fixture only (chat_history.jsonl,
summary.json, events.jsonl). It does not compute `.expected.md` or
`.expected.envelope.jsonl` — there is no `adapters.grok` yet for it to call,
and the point of a frozen-test gate is that the implementer cannot import the
test author's reference parser. Those two files are generated once, by hand
or by a throwaway script that is never committed, and are then committed
themselves as plain data, like any other pinned fixture output.

Usage:
    python3 tools/make_grok_fixtures.py            # rebuild the fixture dirs
    python3 tools/make_grok_fixtures.py --list     # show the selection and why
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Callable

SESSIONS_DIR = Path.home() / ".grok" / "sessions"
FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "grok"

# Same allow-list philosophy as make_codex_fixtures.py: a string survives only
# if it looks like a structural token. Everything else becomes a length-stamped
# placeholder. ISO timestamps ("2026-09-01T23:56:13.571Z") pass this as-is —
# they are exactly as structural as a codex `timestamp` field and D5 depends on
# their exact values for the turn_started/turn_ended pairing.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:+-]{1,40}$")
_UUID_RE = re.compile(
    r"^(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|[0-9a-f]{40})$")  # grok session ids are UUIDv7; git SHAs use the same length

_ID_KEYS = {"id", "session_id", "call_id", "tool_call_id", "parent_session_id",
            "request_id", "encrypted_content"}
_PATH_KEYS = {"cwd", "path", "name", "file", "filename", "dir", "directory",
              "root", "url", "origin", "git_root_dir", "target"}
# Values that name a repo, not a session — kept as a convention (the /projects/
# shape stays testable) but the identity is replaced.
_REPO_KEYS = {"git_remotes", "head_branch"}

_WRAPPER_RE = re.compile(r"^<(user_query|system-reminder)>\n(.*)\n</\1>$", re.S)


def _placeholder(key: str, value: str) -> str:
    return f"<{key or 'str'}:{len(value)}>"


def _placeholder_project(name: str) -> str:
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return f"project-{digest}"


def _sanitise_key(key: str) -> str:
    if _TOKEN_RE.match(key) and not _UUID_RE.match(key):
        return key
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
    return f"<key:{len(key)}:{digest}>"


def _sanitise_text_block(text: str) -> str:
    """Sanitise one chat message's text, preserving a D5-recognised wrapper.

    `<user_query>...</user_query>` and `<system-reminder>...</system-reminder>`
    are the two shapes `adapters.grok.turns()` strips exactly one layer of.
    Placeholdering the whole string the way `sanitise()` would for any other
    prose would erase the very tags the fixture exists to exercise, so the
    wrapper survives and only its interior is stamped.
    """
    m = _WRAPPER_RE.match(text)
    if m:
        tag = m.group(1)
        inner = m.group(2)
        return f"<{tag}>\n<inner:{len(inner)}>\n</{tag}>"
    if _TOKEN_RE.match(text):
        return text
    return _placeholder("text", text)


def sanitise(node, key: str = ""):
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            sk = _sanitise_key(str(k))
            if k == "text" and isinstance(v, str) and v:
                out[sk] = _sanitise_text_block(v)
            else:
                out[sk] = sanitise(v, str(k))
        return out
    if isinstance(node, list):
        return [sanitise(v, key) for v in node]
    if isinstance(node, str):
        if not node:
            # An empty string is a structural fact (a tool-only assistant
            # record's content, an empty summary) worth keeping distinct from
            # "there was prose here" — placeholdering it as `<key:0>` would
            # turn "no text" into a non-empty string and break every
            # empty-text-is-tool-only-marker assertion downstream.
            return node
        if key in _ID_KEYS or _UUID_RE.match(node):
            return f"<{key or 'uuid'}:{len(node)}>"
        if key in _PATH_KEYS or key in _REPO_KEYS:
            return _placeholder(key, node)
        if _TOKEN_RE.match(node):
            return node
        return _placeholder(key, node)
    return node


def sanitise_record(rec: dict, cwd_replacement: str | None) -> dict:
    out = sanitise(rec)
    if cwd_replacement is not None and isinstance(out, dict) and "cwd" in out:
        out["cwd"] = cwd_replacement
    return out


def _project_from_cwd(cwd: str) -> str:
    parts = Path(cwd).parts
    for i, part in enumerate(parts):
        if part != "projects":
            continue
        for candidate in parts[i + 1:]:
            if not candidate.startswith("."):
                return candidate
        return ""
    return ""


class FixtureSpec:
    """One manifest entry: a real session plus how to trim it down."""

    def __init__(self, name: str, project_dir: str, session_id: str, features: list[str],
                 *, chat_stop: Callable[[dict], bool] | None = None, chat_after: int = 0,
                 drop_events: bool = False, parent_fixture: str | None = None):
        self.name = name
        self.project_dir = project_dir
        self.session_id = session_id
        self.features = features
        # chat_stop(record) -> True once this record has been written and the
        # fixture should stop after `chat_after` more lines. None means "keep
        # the whole file" (used only for already-small sessions).
        self.chat_stop = chat_stop
        self.chat_after = chat_after
        self.drop_events = drop_events
        # Name of the sibling fixture directory this one's `parent_session_id`
        # must point at. D4's is_subagent rule is "another session in the same
        # project dir names this one as parent" — a same-*directory* rule —
        # and every fixture here lives flatly under tests/fixtures/grok/, which
        # is exactly the shape of one grok project directory. So the fork
        # relationship has to be expressed as one fixture's directory name,
        # not a UUID a hashed placeholder would otherwise erase.
        self.parent_fixture = parent_fixture

    @property
    def source_dir(self) -> Path:
        return SESSIONS_DIR / self.project_dir / self.session_id


# Hand-picked by a census over every session on this machine (design note
# §1), not by an automated cover search — the shapes below are rare enough
# (one images record in 981 sessions, three backend_tool_call sessions) that
# hand-picking the specific file beats re-deriving the same census on every
# run of this tool.
MANIFEST: list[FixtureSpec] = [
    FixtureSpec(
        "01-interactive-primary", "%2Fhome%2Fscott%2Fprojects%2Fagent-messaging",
        "019ff8e1-7e75-7850-ae69-4131f9c89565",
        ["interactive-primary", "user_query-wrapper"],
        chat_stop=lambda rec: rec.get("type") == "user" and rec.get("prompt_index") == 1,
        chat_after=6,
    ),
    FixtureSpec(
        "02-single-prompt-primary", "%2Fhome%2Fscott",
        "01a02ea2-e8a1-7130-a118-333553b4cf59",
        ["single-prompt-primary"],
    ),
    FixtureSpec(
        "03-chain-tail-forked", "%2Fhome%2Fscott%2Fprojects%2Fagent-messaging",
        "01a03d73-1419-78f2-ad39-62152a3f3133",
        ["chain-tail", "fork_of", "is_subagent-false-with-subagent_resume-kind"],
        parent_fixture="04-superseded-parent",
    ),
    FixtureSpec(
        "04-superseded-parent", "%2Fhome%2Fscott%2Fprojects%2Fagent-messaging",
        "01a03d6e-8038-73c3-a970-f5c887862e3a",
        ["superseded-parent", "is_subagent-true"],
    ),
    FixtureSpec(
        "05-backend-tool-call", "%2Fhome%2Fscott%2Fprojects%2Fagent-messaging",
        "01a03cbd-2fbd-7e43-a0f1-8a0dd6e0f1ea",
        ["backend_tool_call"],
        chat_stop=lambda rec: rec.get("type") == "backend_tool_call",
        chat_after=4,
    ),
    FixtureSpec(
        "06-images-block", "%2Fhome%2Fscott%2Fprojects%2Futilityswitch",
        "01a0152a-ea77-75e0-aea4-9e496d34426a",
        ["images"],
        chat_stop=lambda rec: rec.get("type") == "tool_result" and "images" in rec,
        chat_after=4,
    ),
    FixtureSpec(
        "07-unattributed-cwd", "%2Fhome%2Fscott%2Fworktrees%2Fdv2-spec-grok",
        "01a01cd6-9bee-7bf2-bfaf-cae11771577d",
        ["unattributed-cwd"],
        chat_stop=lambda rec: rec.get("type") == "user" and rec.get("prompt_index") == 1,
        chat_after=6,
    ),
    FixtureSpec(
        "08-missing-events", "%2Fhome%2Fscott",
        "01a02ea2-e8a1-7130-a118-333553b4cf59",
        ["missing-events", "timestamp-fallback"],
        drop_events=True,
    ),
    FixtureSpec(
        "09-subagent-completed-synthetic", "%2Fhome%2Fscott%2Fprojects%2Fagent-messaging",
        "019ffb15-473c-7ad3-ad8b-fa36acf41dd5",
        ["synthetic_reason:subagent_completed"],
        chat_stop=lambda rec: rec.get("synthetic_reason") == "subagent_completed",
        chat_after=4,
    ),
]


def _neutral_cwd(cwd: str) -> str:
    project = _project_from_cwd(cwd)
    if project:
        return f"/home/user/projects/{_placeholder_project(project)}"
    if "/worktrees/" in cwd:
        return "/home/user/worktrees/" + _placeholder_project(cwd)
    return "/home/user"


def build_one(spec: FixtureSpec) -> str:
    src = spec.source_dir
    summary = json.loads((src / "summary.json").read_text())
    real_cwd = summary.get("info", {}).get("cwd", "")
    neutral_cwd = _neutral_cwd(real_cwd)

    dest = FIXTURE_DIR / spec.name
    dest.mkdir(parents=True, exist_ok=True)
    for stale in dest.glob("*"):
        stale.unlink()

    # -- chat_history.jsonl -------------------------------------------------
    lines_out = []
    stop_in = None
    with open(src / "chat_history.jsonl", errors="replace") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            lines_out.append(json.dumps(sanitise_record(rec, None), ensure_ascii=True))
            if spec.chat_stop is not None:
                if stop_in is not None:
                    stop_in -= 1
                    if stop_in <= 0:
                        break
                elif spec.chat_stop(rec):
                    stop_in = spec.chat_after
    (dest / "chat_history.jsonl").write_text("\n".join(lines_out) + "\n", encoding="utf-8")
    n_user_prompts = sum(
        1 for line in lines_out
        if '"type": "user"' in line and '"prompt_index"' in line
    )

    # -- summary.json ---------------------------------------------------------
    san_summary = sanitise(summary)
    san_summary.setdefault("info", {})
    # The directory name *is* the id in the real store (`discover()` walks
    # `SESSIONS_DIR/*/*/chat_history.jsonl` and takes the id from the
    # session's own directory). Fixture directory names are already
    # non-identifying, so `info.id` is set to match them exactly rather than
    # to a hashed placeholder — that keeps it consistent with whatever a
    # correct adapter derives from `ref.path.name`.
    san_summary["info"]["id"] = dest.name
    san_summary["info"]["cwd"] = neutral_cwd
    if spec.parent_fixture is not None:
        san_summary["parent_session_id"] = spec.parent_fixture
    elif "parent_session_id" in san_summary:
        del san_summary["parent_session_id"]
    for k in ("git_root_dir",):
        if k in san_summary:
            san_summary[k] = neutral_cwd + "/"
    (dest / "summary.json").write_text(json.dumps(san_summary, indent=2), encoding="utf-8")

    # -- events.jsonl -----------------------------------------------------
    if not spec.drop_events and (src / "events.jsonl").exists():
        ev_lines = []
        # Only as many turn_started/turn_ended pairs as the truncated chat
        # needs — the same "name the fixture by what it contains" discipline
        # make_codex_fixtures.py uses for depth.
        pairs_needed = max(1, n_user_prompts)
        pairs_seen = 0
        with open(src / "events.jsonl", errors="replace") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                san = sanitise(rec)
                if isinstance(san, dict) and san.get("type") == "turn_started":
                    san["session_id"] = dest.name
                ev_lines.append(json.dumps(san, ensure_ascii=True))
                if rec.get("type") == "turn_ended":
                    pairs_seen += 1
                    if pairs_seen >= pairs_needed:
                        break
        (dest / "events.jsonl").write_text("\n".join(ev_lines) + "\n", encoding="utf-8")

    return (f"{spec.name}  covers={','.join(spec.features)}  "
            f"user_prompts={n_user_prompts}  source={spec.session_id}")


def build(specs: list[FixtureSpec] = MANIFEST) -> list[str]:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    return [build_one(spec) for spec in specs]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="Show the selection without writing")
    args = parser.parse_args()

    if not SESSIONS_DIR.exists():
        print(f"No grok sessions at {SESSIONS_DIR}; nothing to build.", file=sys.stderr)
        return 0

    if args.list:
        for spec in MANIFEST:
            print(f"{spec.name}  (source {spec.project_dir}/{spec.session_id})")
            print(f"    covers: {', '.join(spec.features)}")
        return 0

    for line in build():
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
