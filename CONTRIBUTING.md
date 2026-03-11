# Contributing to LLM Memory

Thanks for your interest in contributing to LLM Memory! This guide covers
everything you need to get started.

## Dev Setup

```bash
# Clone the repo
git clone https://github.com/your-org/llm_memory.git
cd llm_memory

# Create a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt  # if available, otherwise: pip install pytest
```

## Running Tests

```bash
python -m pytest tests/ -v
```

Tests live in `tests/` and cover the MCP server and transcript processing logic.

## Project Structure

| Path | What it does |
|------|-------------|
| `server.py` | MCP server — provides 7 memory tools (store, search, get, recent, connect, explore, delete). Claude Code spawns this as a subprocess. |
| `process_transcripts.py` | Reads raw JSONL transcripts from Claude Code sessions, strips noise, and generates chunk summaries. |
| `install.sh` | One-command installer — sets up venv, hooks, Syncthing config, and CLAUDE.md rules. |
| `hooks/` | Shell scripts that run at Claude Code lifecycle events (see below). |
| `hooks/session_start.sh` | Runs on session start — checks for updates, loads narrative, sweeps transcripts. |
| `hooks/session_end.sh` | Runs on session end — archives transcript, creates session_log record. |
| `hooks/pre_compact.sh` | Runs before compaction — saves context before lossy summarization. |
| `hooks/session_monitor.sh` | PostToolUse hook — monitors transcript and tracks save timestamps. |
| `hooks/install_hooks.sh` | Registers hooks with Claude Code's settings. |
| `dashboard.py` | Web dashboard for browsing stored memories (FastAPI + Jinja2). |
| `dashboard.sh` | Launcher script for the dashboard. |
| `apply_settings.py` | Applies hook configuration to Claude Code's settings.json. |
| `setup_syncthing.py` | Configures Syncthing for multi-device memory sync. |
| `settings.yaml` | Default settings for hook configuration. |
| `templates/` | Jinja2 templates for the web dashboard. |
| `tests/` | Pytest test suite. |

## How Hooks Work

LLM Memory uses Claude Code's lifecycle hook system. The flow for a typical session:

1. **SessionStart** — Claude Code starts a session and fires the hook. The script
   checks for updates, sweeps for unprocessed transcripts from other devices,
   loads the project narrative and recent notes, and flags projects that need
   a narrative generated.
2. **PostToolUse** (session_monitor) — Fires after each tool call. Monitors the
   raw JSONL transcript file and tracks when it was last saved.
3. **PreCompact** — Fires when Claude Code is about to compact (lossy-summarize)
   the conversation. The hook saves important context before it gets compressed.
4. **SessionEnd** — Fires when the session ends. Archives the raw transcript and
   creates a `session_log` memory record.

Hooks are registered in Claude Code's `settings.json` by `install_hooks.sh`
and `apply_settings.py`.

## Pull Request Guidelines

1. **Tests pass** — Run `python -m pytest tests/ -v` before submitting.
2. **Docs updated** — If you change behavior, update the README or relevant docs.
3. **One concern per PR** — Keep PRs focused on a single change.
4. **Describe the why** — In your PR description, explain why the change is needed,
   not just what it does.

## Code Style

- Python 3.10+
- No specific linter enforced yet — just keep it readable and consistent with
  the existing codebase.
- Use type hints where practical.
- Prefer clear variable names over comments.

## Reporting Issues

Use the GitHub issue templates for bug reports and feature requests. Include
your OS, Python version, and Claude Code version when reporting bugs.

## Questions?

Open a discussion or issue on GitHub. We're happy to help.
