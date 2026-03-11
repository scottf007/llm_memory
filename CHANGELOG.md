# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.0.0] - 2026-03-10

First public release. This changelog is retroactive, covering all development
from initial commit through stabilization.

### Added

- MCP server (`server.py`) with 7 memory tools: store, search, get, recent,
  connect, explore, delete
- SQLite + FTS5 index for fast full-text search across memories
- File-based JSON record storage (rebuildable from records alone)
- Three memory types: `narrative` (living project document), `note` (atomic
  facts/decisions), `session_log` (automatic session records)
- Lifecycle hooks for Claude Code:
  - `session_start.sh` — loads project context, sweeps transcripts, checks updates
  - `session_end.sh` — archives transcripts, creates session_log records
  - `pre_compact.sh` — saves context before lossy compaction
  - `session_monitor.sh` — PostToolUse hook for transcript monitoring
  - `install_hooks.sh` — registers hooks in Claude Code settings
- Transcript processor (`process_transcripts.py`) with noise stripping and
  chunk summary generation
- Web dashboard (`dashboard.py`) for browsing memories (FastAPI + Jinja2)
- Syncthing-based multi-device sync (`setup_syncthing.py`)
- One-command installer (`install.sh`) — sets up venv, hooks, config, and
  optional Syncthing
- Shared CLAUDE.md rules that teach Claude when and how to use memory tools
- `apply_settings.py` for programmatic hook configuration
- MIT license

### Changed

- **v2 rewrite** (2026-03-09): New narrative format with required sections
  (What This Is, Session History, Decisions Made, Gotchas & Lessons, Current
  State, Outstanding Items, Direction, Source Transcripts). Unified install
  script. Dashboard fixes. Auto-narrative generation without asking the user.
- **v3 file-based sync** (2026-03-10): Replaced SQLite-only storage with
  file-based JSON records for reliable multi-device sync via Syncthing.
  SQLite becomes a derived index that can be rebuilt from records.
- **v4 auto-install** (2026-03-10): Auto-install from GitHub on first run,
  auto-update checks, transcript sweep for uncollected sessions, and
  auto-install of system dependencies (jq, sqlite3, curl).

### Fixed

- Hardcoded path in `session_end.sh`
- Venv permission errors: recreate venv if pip not executable, use
  `python3 -m pip` instead of calling venv pip directly, chmod +x venv
  binaries after creation
- `set -e` killing scripts on non-critical chmod/find exit codes
- Transcript processing for multi-device sync scenarios
- Hook dedup filter and SessionStart timeout
