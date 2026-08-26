#!/usr/bin/env python3
"""Print a resume() injection block for one project, plain text on stdout.

This is the non-Claude half of memory injection (S3, D10). Claude gets the
narrative spliced into context automatically by hooks/session_start.sh; every
other client has no such hook, so tools/memory_wrap calls this script and
prepends its stdout to the client's first prompt instead.

Calls server._handle_resume() directly — no MCP round-trip, no server
process. LLM_MEMORY_HOME (or its HOME-based fallback) must be set before this
process starts because server.py resolves its configured root at import time.
That is why tools/memory_wrap always execs this as a fresh subprocess rather
than importing it in-process.

Usage:
    python3 tools/memory_wrap_resume.py <project>
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server  # noqa: E402


def render_resume_block(project: str) -> str:
    result = server._handle_resume({"project": project})
    text = result[0].text

    # _handle_resume reports failures as a plain "Error: ..." string, not
    # JSON (see server._error) — e.g. no state file for an unknown project.
    if text.startswith("Error:"):
        return f"(memory_wrap: {text})"

    payload = json.loads(text)

    if payload.get("status") == "no_sessions":
        return f"(memory_wrap: no prior session found for project '{project}')"

    lines = [
        f"=== MEMORY (resume: {project}) ===",
        f"session {payload.get('session_id', '?')} "
        f"({payload.get('started', '?')} -> {payload.get('ended', '?')}, "
        f"{payload.get('closure_status', 'unknown')})",
    ]
    topic = payload.get("topic")
    if topic:
        lines.append(f"topic: {topic}")
    journal = payload.get("journal")
    if journal:
        lines.append("")
        lines.append(journal)
    tail = payload.get("conversation_tail")
    if tail:
        lines.append("")
        lines.append(f"--- conversation tail ({payload.get('conversation_tail_lines')} lines) ---")
        lines.append(tail)
    lines.append("=== END MEMORY ===")
    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        print("usage: memory_wrap_resume.py <project>", file=sys.stderr)
        return 2
    block = render_resume_block(sys.argv[1].strip())
    print(block)
    # A "(memory_wrap: ...)" block is a notice, not real memory — it's
    # inserted into the client's prompt either way (fail open on the model
    # side, per design note §2b.7's "say so explicitly"), but a silent
    # stderr means the person running memory_wrap never sees it. Echo it so
    # a mistyped --project or an unknown project is visible at the terminal,
    # not just to the model.
    if block.startswith("(memory_wrap:"):
        print(block, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
