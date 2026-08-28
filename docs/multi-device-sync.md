# Multi-Device Sync

LLM Memory is designed for use across multiple machines using [Syncthing](https://syncthing.net/), a free, open-source peer-to-peer file sync tool.

## How It Works

The architecture separates synced data (JSON files) from local-only data (SQLite index):

```
$MEMORY_ROOT/          (default ~/.claude/memory, or $LLM_MEMORY_HOME if set)
├── records/        ← SYNCED (source of truth)
├── transcripts/    ← SYNCED (raw session data)
├── config/         ← SYNCED (shared CLAUDE.md rules)
├── memory.db       ← LOCAL ONLY (rebuilt from records/)
└── lib/.venv/      ← LOCAL ONLY (per-machine Python env)
```

The store's location is configurable per machine via the `LLM_MEMORY_HOME`
environment variable; every command below substitutes
`MEMORY_ROOT="${LLM_MEMORY_HOME:-$HOME/.claude/memory}"` the same way the
installed scripts do. Each device can use a different real location —
Syncthing pairs devices by folder ID, not by local path, so what matters is
pointing each machine's Syncthing folder at *that machine's* configured
root, not that the paths match across machines.

Each machine has its own SQLite database. The MCP server rebuilds it from the JSON record files on startup. No conflicts are possible because every record uses a globally unique 32-character hex UUID as its filename.

## Setup

### Step 1: Install LLM Memory on Each Machine

```bash
curl -sL https://raw.githubusercontent.com/scottf007/llm_memory/main/install.sh | bash
```

### Step 2: Install Syncthing

Follow the [Syncthing installation guide](https://docs.syncthing.net/intro/getting-started.html) for your platform.

### Step 3: Configure the Sync Folder

Run the included setup script:

```bash
MEMORY_ROOT="${LLM_MEMORY_HOME:-$HOME/.claude/memory}"
python3 "$MEMORY_ROOT/lib/setup_syncthing.py"
```

This script:
- Finds your local Syncthing instance (Linux, macOS, or WSL)
- Adds the configured memory root as a shared folder with ID `llm-memory`
- Applies the `.stignore` rules

Alternatively, add the folder manually in the Syncthing web UI (`http://localhost:8384`):
1. Add a new folder with path set to *this machine's* configured memory root
   (`echo "${LLM_MEMORY_HOME:-$HOME/.claude/memory}"` to see it) — use the
   real underlying directory, not a symlink, as the folder path
2. Set the folder ID to `llm-memory`
3. Share it with your other devices

### Step 4: Pair Devices

In the Syncthing web UI on each machine:
1. Click "Add Remote Device"
2. Enter the device ID from the other machine
3. Accept the shared `llm-memory` folder

## What Syncs

| Directory | Synced | Contents |
|-----------|--------|----------|
| `records/` | Yes | One JSON file per memory. Source of truth for all data. |
| `transcripts/` | Yes | Raw JSONL session transcripts. Used to generate narratives. |
| `config/CLAUDE.md` | Yes | Shared rules applied to `~/.claude/CLAUDE.md` on session start. |
| `memory.db` | No | SQLite index. Each machine builds its own from records/. |
| `lib/.venv/` | No | Python virtual environment. Each machine installs its own. |
| `lib/` (code) | No | Installed via `install.sh` per machine. Auto-updates independently. |

The `.stignore` file (created by the installer) enforces these exclusions:

```
memory.db
memory.db-wal
memory.db-shm
lib/.venv
```

## Troubleshooting

### Database out of sync with record files

If the SQLite index does not reflect recently synced records:

```bash
MEMORY_ROOT="${LLM_MEMORY_HOME:-$HOME/.claude/memory}"
python3 "$MEMORY_ROOT/lib/server.py" --rebuild
```

This deletes `memory.db` and rebuilds it from all JSON files in `records/`.

### Memories from another machine not appearing

1. Check Syncthing is running on both machines: `http://localhost:8384`
2. Verify the `llm-memory` folder is connected and not paused
3. Check that new `.json` files exist under `records/` in the configured
   memory root (`echo "${LLM_MEMORY_HOME:-$HOME/.claude/memory}"`)
4. Rebuild the database if files are present but not indexed

### Conflict files

Syncthing never produces conflicts for LLM Memory because:
- Record filenames are UUIDs, so two machines never write the same file
- `memory.db` is excluded from sync
- `CLAUDE.md` is the only file that could theoretically conflict (if edited on two machines simultaneously). If this happens, Syncthing creates a `.sync-conflict-*` file. Manually merge and delete the conflict file.

### WSL-specific notes

On WSL, Syncthing typically runs on the Windows side. The `setup_syncthing.py` script detects this and looks for the config at `/mnt/c/Users/*/AppData/Local/Syncthing/config.xml`. The configured memory root is inside WSL's filesystem, so Syncthing must be configured to access it via the WSL path (e.g., `\\wsl$\Ubuntu\home\user\.claude\memory\`, or wherever `$LLM_MEMORY_HOME` points if it's been relocated).
