"""
Extract clean conversation text from a Claude Code JSONL transcript.

Drops tool_use, tool_result, file-history-snapshot, hooks, and other plumbing.
Keeps user/assistant text content. Blocks where a tool_use/tool_result was
dropped get a [L:N] line ref so you can look up the raw JSONL entry.

Usage:
    python extract_conversation.py <jsonl_path>                  # to stdout
    python extract_conversation.py <jsonl_path> --output PATH    # to file
    python extract_conversation.py <jsonl_path> --output PATH --force
"""

import argparse
import json
import re
import sys
from pathlib import Path

_NOISE_TAG_RE = re.compile(
    r"<(?:ide_opened_file|local-command-caveat|command-me|system-reminder|command-name|"
    r"command-message|command-args|local-command-stdout|user-prompt-submit-hook|"
    r"available-deferred-tools|fast_mode_info)>.*?</(?:ide_opened_file|local-command-caveat|"
    r"command-me|system-reminder|command-name|command-message|command-args|"
    r"local-command-stdout|user-prompt-submit-hook|available-deferred-tools|fast_mode_info)>",
    re.DOTALL,
)


def _clean_text(text: str) -> str:
    text = _NOISE_TAG_RE.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_user_text(entry: dict) -> str:
    msg = entry.get("message", {})
    content = msg.get("content", "")
    if isinstance(content, str):
        return _clean_text(content)
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", "").strip())
            elif isinstance(block, str):
                parts.append(block.strip())
        return _clean_text(" ".join(p for p in parts if p))
    return ""


def _assistant_entry_parts(entry: dict) -> tuple[str, bool]:
    """Return (text, has_tool_use).

    has_tool_use distinguishes "this assistant entry invoked a tool" from
    "this entry was text/thinking only." Only tool_use triggers a [L:N] ref
    on the merged block — thinking blocks get dropped quietly, since the
    ref is meant to point at external side effects the reader might want
    to inspect, not at Claude's inner monologue.
    """
    msg = entry.get("message", {})
    content = msg.get("content", [])
    has_tool_use = False
    if isinstance(content, str):
        return _clean_text(content), False
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                btype = block.get("type")
                if btype == "text":
                    text = block.get("text", "").strip()
                    if text:
                        parts.append(text)
                elif btype == "tool_use":
                    has_tool_use = True
        return _clean_text(" ".join(parts)), has_tool_use
    return "", False


def _project_from_cwd(cwd: str) -> str:
    parts = Path(cwd).parts
    for i, part in enumerate(parts):
        if part == "projects" and i + 1 < len(parts):
            return parts[i + 1]
    return ""


def extract(jsonl_path: Path) -> str:
    session_id = jsonl_path.stem
    project = ""
    parent_session_id = None
    first_ts = None
    last_ts = None
    turns = 0
    blocks: list[str] = []

    # Buffer for grouping consecutive assistant entries into one block per turn.
    asst_texts: list[str] = []
    asst_first_ts = ""
    asst_tool_line: int | None = None  # first JSONL line with a tool_use in this group

    def flush_assistant() -> None:
        nonlocal asst_texts, asst_first_ts, asst_tool_line
        if asst_texts:
            ref = f" [L:{asst_tool_line}]" if asst_tool_line is not None else ""
            header = f"=== assistant {asst_first_ts}{ref} ===".rstrip()
            blocks.append(f"{header}\n" + "\n\n".join(asst_texts))
        asst_texts = []
        asst_first_ts = ""
        asst_tool_line = None

    with open(jsonl_path, "r", errors="replace") as f:
        for line_num, line in enumerate(f, 1):
            try:
                entry = json.loads(line.strip())
            except (json.JSONDecodeError, ValueError):
                continue

            entry_type = entry.get("type")
            ts = entry.get("timestamp") or ""

            if ts:
                if first_ts is None:
                    first_ts = ts
                last_ts = ts

            if not project:
                cwd = entry.get("cwd") or ""
                if cwd:
                    project = _project_from_cwd(cwd)

            if not parent_session_id and entry.get("parentSessionId"):
                parent_session_id = entry["parentSessionId"]

            if entry_type == "user":
                text = _extract_user_text(entry)
                if text:
                    # Real user turn — flush accumulated assistant group first.
                    flush_assistant()
                    turns += 1
                    header = f"=== user {ts} ===".rstrip()
                    blocks.append(f"{header}\n{text}")
                # Synthetic user entries (tool_result only) produce no text
                # and must not break the assistant grouping.
            elif entry_type == "assistant":
                text, has_tool_use = _assistant_entry_parts(entry)
                if text:
                    if not asst_first_ts:
                        asst_first_ts = ts
                    asst_texts.append(text)
                if has_tool_use and asst_tool_line is None:
                    asst_tool_line = line_num

        flush_assistant()

    fm = ["---", f"session_id: {session_id}"]
    if project:
        fm.append(f"project: {project}")
    fm.append(f"raw: ~/.claude/memory/transcripts/{session_id}.jsonl")
    fm.append(f"turns: {turns}")
    if first_ts:
        fm.append(f"started: {first_ts}")
    if last_ts:
        fm.append(f"ended: {last_ts}")
    if parent_session_id:
        fm.append(f"parent_session_id: {parent_session_id}")
    fm.append("---")

    body = "\n\n".join(blocks)
    return "\n".join(fm) + "\n\n" + body + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract conversation text from a Claude Code JSONL transcript")
    parser.add_argument("jsonl_path", type=Path)
    parser.add_argument("--output", type=Path, help="Write to file instead of stdout")
    parser.add_argument("--force", action="store_true", help="Rewrite output even if newer than source")
    args = parser.parse_args()

    if not args.jsonl_path.exists():
        print(f"Error: {args.jsonl_path} does not exist", file=sys.stderr)
        sys.exit(1)

    if args.output and args.output.exists() and not args.force:
        if args.output.stat().st_mtime >= args.jsonl_path.stat().st_mtime:
            return

    result = extract(args.jsonl_path)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result)
    else:
        sys.stdout.write(result)


if __name__ == "__main__":
    main()
