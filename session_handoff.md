# LLM Memory — Session Handoff (SCOTT-XPS → Desktop)

## What Happened This Session

Session on SCOTT-XPS (laptop). Fixed several issues with cross-machine sync and transcript processing.

## Changes Made (NOT YET COMMITTED/PUSHED)

Git identity is not configured on SCOTT-XPS, so these changes are staged but not committed. The other machine needs to either:
- Set up git config on SCOTT-XPS and commit/push from there, OR
- Apply the same changes on the desktop and commit/push from there

### 1. `process_transcripts.py` — find_transcripts() now scans archive dir
**Problem**: `find_transcripts()` only looked in `~/.claude/projects/*/` for JSONL files. Transcripts synced from another machine via Syncthing land in `~/.claude/memory/transcripts/` but were never processed into session_logs.

**Fix**: Added a second scan of `ARCHIVE_DIR` (`~/.claude/memory/transcripts/`) with deduplication by session_id so transcripts appearing in both locations are only processed once.

### 2. `hooks/session_start.sh` — process ALL unprocessed sessions
**Problem**: The hook only ran `process_transcripts.py --project "$PROJECT"` when a project had zero memories. Sessions from other projects or the generic "general" project were never auto-processed.

**Fix**: Changed to run `process_transcripts.py --quiet` (no project filter) on every non-compact startup. This processes all unprocessed transcripts regardless of which project they belong to.

### 3. `setup_syncthing.py` — ignorePerms=true
**Problem**: Windows Syncthing can't preserve Unix file permissions. All shell scripts arrived as `644` instead of `755`.

**Fix**: Set `ignorePerms: true` in both the API and XML config paths. Permissions are handled by `install.sh` on each machine instead (it already does `chmod +x` on hooks and shell scripts).

### 4. `process_transcripts.py` — derive_project() "transcripts" bug fix
**Problem**: When transcripts are in `~/.claude/memory/transcripts/` (the archive dir), the `derive_project()` fallback used the parent directory name, giving `"transcripts"` as the project name instead of `"general"`.

**Fix**: Added check: if `dir_project` is `"transcripts"`, return `"general"` instead.

### 5. `tests/test_process_transcripts.py` — 2 new tests
- `test_includes_archive_dir`: verifies find_transcripts picks up files from both project dirs and archive dir
- `test_no_duplicates_when_same_session_in_both`: verifies deduplication when same session exists in both locations

### 6. pytest not in requirements.txt
`pytest` is not listed in `requirements.txt`. It had to be installed manually (`pip install pytest`) to run the test suite. Consider adding it, or creating a `requirements-dev.txt`.

## Files Changed

| File | Change |
|------|--------|
| `process_transcripts.py` | `find_transcripts()` also scans archive dir; `derive_project()` handles "transcripts" fallback |
| `hooks/session_start.sh` | Runs `process_transcripts.py` on all sessions, not just current project |
| `setup_syncthing.py` | `ignorePerms: True` in both API and XML paths |
| `tests/test_process_transcripts.py` | 2 new tests for archive dir scanning + dedup |

## Other Actions Taken

- **Restored git repo**: `.git` was empty (synced files, no git). Cloned from GitHub and moved `.git` into the working dir. All files match remote.
- **Fixed file permissions**: `chmod +x` on all `.sh` files that lost execute bits via Syncthing.
- **Copied updated files to live install**: `~/.claude/memory/lib/` has the updated `process_transcripts.py`, `session_start.sh`, and `setup_syncthing.py`.
- **Ran `process_transcripts.py`**: Picked up 1 new session (`44d81b47`, 68 turns, llm_memory project on SCOTT-XPS).
- **Deleted old `~/.claude/memory/session_handoff.md`**: The previous handoff (about venv permission failures) is resolved.
- **Home Assistant narrative**: Generated and stored (UUID `5c5da5e0ebee81fef4e295a3eaadc47c`, project `home_assistant`) from transcript `5fdd37a8` (116 turns). Covers Shelly fleet debugging, PIR sensor/resistor fix, UDM Pro NTP workaround, Tasmota automation.

## Git State

The repo was restored by cloning from GitHub and moving `.git` in. All changes are in the working tree but **NOT committed** because git identity is not configured on SCOTT-XPS.

```
Staged:     hooks/session_start.sh, process_transcripts.py (partial), setup_syncthing.py
Unstaged:   process_transcripts.py (transcripts bugfix), tests/test_process_transcripts.py
Untracked:  session_handoff.md
```

**Note**: The staged `process_transcripts.py` has the find_transcripts archive scan but NOT the "transcripts" project name bugfix. Both unstaged changes need to be added before committing.

## What Needs To Happen Next

1. **Configure git on SCOTT-XPS** and commit/push:
   ```bash
   git config --global user.name "Scott Fletcher"
   git config --global user.email "your@email.com"
   cd ~/projects/llm_memory
   git add process_transcripts.py tests/test_process_transcripts.py session_handoff.md
   git commit -m "Fix transcript processing for multi-device sync"
   git push
   ```
   Or if working from the desktop, the files will sync via Syncthing — just commit from there.

2. **Push to GitHub** so the install auto-update picks up the changes on both machines.

3. **All 38 tests pass** — run `pytest tests/ -v` to verify. Note: `pytest` must be installed first (`pip install pytest`) — it's not in requirements.txt.

## Test Results

```
38 passed in 3.61s
- 9 process_transcripts tests (including 2 new)
- 29 server tests
```

## Machine State

- **SCOTT-XPS**: All code changes in working dir, live install updated, MCP server working, hooks installed, 65 transcripts in archive.
- **Syncthing**: `ignorePerms` set to true on both machines (done manually in Syncthing UI). `setup_syncthing.py` updated to match.
- **Dashboard**: Not running on SCOTT-XPS. Cricket manager uvicorn is on port 8000.
