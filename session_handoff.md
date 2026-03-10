# LLM Memory — Session Handoff (SCOTT-XPS → Desktop)

## What Happened This Session

Session on SCOTT-XPS (laptop). Fixed several issues with cross-machine sync and transcript processing.

## Changes Made — COMMITTED AND PUSHED

Commit `a907999` on `main`, pushed to `origin`. The auto-update hook on the desktop will detect the new commit on next session start and update `~/.claude/memory/lib/` automatically.

### 1. `process_transcripts.py` — find_transcripts() now scans archive dir
**Problem**: `find_transcripts()` only looked in `~/.claude/projects/*/` for JSONL files. Transcripts synced from another machine via Syncthing land in `~/.claude/memory/transcripts/` but were never processed into session_logs.

**Fix**: Added a second scan of `ARCHIVE_DIR` (`~/.claude/memory/transcripts/`) with deduplication by session_id so transcripts appearing in both locations are only processed once.

### 2. `process_transcripts.py` — derive_project() "transcripts" bug fix
**Problem**: When transcripts are in `~/.claude/memory/transcripts/` (the archive dir), the `derive_project()` fallback used the parent directory name, giving `"transcripts"` as the project name instead of `"general"`.

**Fix**: Added check: if `dir_project` is `"transcripts"`, return `"general"` instead.

### 3. `hooks/session_start.sh` — process ALL unprocessed sessions
**Problem**: The hook only ran `process_transcripts.py --project "$PROJECT"` when a project had zero memories. Sessions from other projects or the generic "general" project were never auto-processed.

**Fix**: Changed to run `process_transcripts.py --quiet` (no project filter) on every non-compact startup. This processes all unprocessed transcripts regardless of which project they belong to.

### 4. `setup_syncthing.py` — ignorePerms=true
**Problem**: Windows Syncthing can't preserve Unix file permissions. All shell scripts arrived as `644` instead of `755`.

**Fix**: Set `ignorePerms: true` in both the API and XML config paths. Permissions are handled by `install.sh` on each machine instead (it already does `chmod +x` on hooks and shell scripts).

### 5. `tests/test_process_transcripts.py` — 2 new tests
- `test_includes_archive_dir`: verifies find_transcripts picks up files from both project dirs and archive dir
- `test_no_duplicates_when_same_session_in_both`: verifies deduplication when same session exists in both locations

## Files Changed

| File | Change |
|------|--------|
| `process_transcripts.py` | `find_transcripts()` also scans archive dir; `derive_project()` handles "transcripts" fallback |
| `hooks/session_start.sh` | Runs `process_transcripts.py` on all sessions, not just current project |
| `setup_syncthing.py` | `ignorePerms: True` in both API and XML paths |
| `tests/test_process_transcripts.py` | 2 new tests for archive dir scanning + dedup |
| `session_handoff.md` | This file |

## Other Actions Taken

- **Restored git repo**: `.git` was empty (synced files, no git). Cloned from GitHub and moved `.git` into the working dir.
- **Fixed file permissions**: `chmod +x` on all `.sh` files that lost execute bits via Syncthing.
- **Copied updated files to live install**: `~/.claude/memory/lib/` has the updated `process_transcripts.py`, `session_start.sh`, and `setup_syncthing.py`.
- **Ran `process_transcripts.py`**: Picked up 1 new session (`44d81b47`, 68 turns, llm_memory project on SCOTT-XPS).
- **Deleted old `~/.claude/memory/session_handoff.md`**: The previous handoff (about venv permission failures) is resolved.
- **Home Assistant narrative**: Generated and stored (UUID `5c5da5e0ebee81fef4e295a3eaadc47c`, project `home_assistant`) from transcript `5fdd37a8` (116 turns). Covers Shelly fleet debugging, PIR sensor/resistor fix, UDM Pro NTP workaround, Tasmota automation.
- **Git identity configured**: `git config --global` set to "Scott Fletcher" / scott@fletchcorp.com on SCOTT-XPS.
- **GitHub tokens exposed**: Two PATs were pasted in conversation. Both should be revoked at https://github.com/settings/tokens and replaced. Token has been removed from the git remote URL.

## Outstanding Items for Desktop

1. **Revoke GitHub tokens**: Two tokens were exposed — `ghp_ICOh...` and `ghp_LR4e...`. Delete both at https://github.com/settings/tokens and create a fresh one.
2. **pytest not in requirements.txt**: Had to `pip install pytest` manually to run tests. Consider adding to requirements.txt or creating requirements-dev.txt.
3. **Desktop git pull**: Run `git pull` in `~/projects/llm_memory` to get the latest. Or let the auto-update handle `~/.claude/memory/lib/`.
4. **Narratives still needed**: `general` and `load_balancer` projects have session_logs but no narratives.

## Test Results

```
38 passed in 4.49s
- 11 process_transcripts tests (including 2 new)
- 27 server tests
```

## Machine State

- **SCOTT-XPS**: All code committed and pushed. Live install updated. MCP server working. Hooks installed. 65 transcripts in archive. Git identity configured.
- **Syncthing**: `ignorePerms` set to true on both machines (done manually in Syncthing UI). `setup_syncthing.py` updated to match.
- **Dashboard**: Not running on SCOTT-XPS. Cricket manager uvicorn is on port 8000.
- **Git**: `main` branch, commit `a907999`, up to date with `origin/main`.
