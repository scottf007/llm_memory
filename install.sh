#!/bin/bash
set -e

REPO="scottf007/llm_memory"
BRANCH="main"
MEMORY_DIR="$HOME/.claude/memory"
LIB_DIR="$MEMORY_DIR/lib"
VENV_DIR="$LIB_DIR/.venv"
QUIET=false

if [ "$1" = "--update" ] || [ "$1" = "--quiet" ]; then
    QUIET=true
fi

log() {
    if [ "$QUIET" = false ]; then
        echo "$@"
    fi
}

log "=== LLM Memory — Install ==="
log ""

# --- Step 1: Check system dependencies ---
log "[1/8] Checking system dependencies..."
MISSING=""
for cmd in jq sqlite3 python3 curl; do
    if ! command -v "$cmd" &> /dev/null; then
        MISSING="$MISSING $cmd"
    fi
done
if [ -n "$MISSING" ]; then
    echo "  Missing:$MISSING — attempting to install..."
    if command -v apt-get &> /dev/null; then
        sudo apt-get install -y $MISSING
    elif command -v brew &> /dev/null; then
        brew install $MISSING
    elif command -v pacman &> /dev/null; then
        sudo pacman -S --noconfirm $MISSING
    elif command -v dnf &> /dev/null; then
        sudo dnf install -y $MISSING
    else
        echo "  ERROR: Could not auto-install. Please install manually:$MISSING"
        exit 1
    fi
fi
log "  All dependencies found."

# --- Step 2: Download latest from GitHub ---
log "[2/8] Downloading latest version from GitHub..."
mkdir -p "$MEMORY_DIR" "$LIB_DIR"

TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

# Get the latest commit hash
REMOTE_SHA=$(curl -sf "https://api.github.com/repos/$REPO/commits/$BRANCH" | jq -r '.sha' 2>/dev/null)
if [ -z "$REMOTE_SHA" ] || [ "$REMOTE_SHA" = "null" ]; then
    # If API fails (rate limit, no network), check if we already have files
    if [ -f "$LIB_DIR/server.py" ]; then
        log "  Could not reach GitHub. Using existing installation."
    else
        echo "  ERROR: Could not reach GitHub and no existing installation found."
        echo "  Check your internet connection and try again."
        exit 1
    fi
else
    # Check if we already have this version
    LOCAL_SHA=""
    if [ -f "$LIB_DIR/VERSION" ]; then
        LOCAL_SHA=$(cat "$LIB_DIR/VERSION")
    fi

    if [ "$LOCAL_SHA" = "$REMOTE_SHA" ] && [ "$1" != "--force" ]; then
        log "  Already up to date ($REMOTE_SHA)."
    else
        # Download and extract
        curl -sL "https://github.com/$REPO/archive/refs/heads/$BRANCH.tar.gz" | tar xz -C "$TMPDIR"
        EXTRACTED="$TMPDIR/llm_memory-$BRANCH"

        if [ ! -d "$EXTRACTED" ]; then
            echo "  ERROR: Download failed or archive structure unexpected."
            exit 1
        fi

        # Copy source files to lib directory
        cp "$EXTRACTED/server.py" "$LIB_DIR/"
        cp "$EXTRACTED/process_transcripts.py" "$LIB_DIR/"
        cp "$EXTRACTED/dashboard.py" "$LIB_DIR/"
        cp "$EXTRACTED/apply_settings.py" "$LIB_DIR/"
        cp "$EXTRACTED/requirements.txt" "$LIB_DIR/"
        cp "$EXTRACTED/settings.yaml" "$LIB_DIR/"
        cp "$EXTRACTED/claude-rules-example.md" "$LIB_DIR/"
        cp "$EXTRACTED/setup_syncthing.py" "$LIB_DIR/"
        cp "$EXTRACTED/install.sh" "$LIB_DIR/"

        # Copy hooks
        mkdir -p "$LIB_DIR/hooks"
        cp "$EXTRACTED/hooks/"*.sh "$LIB_DIR/hooks/"
        chmod +x "$LIB_DIR/hooks/"*.sh

        # Copy templates
        mkdir -p "$LIB_DIR/templates"
        cp "$EXTRACTED/templates/"* "$LIB_DIR/templates/" 2>/dev/null || true

        # Copy tests
        mkdir -p "$LIB_DIR/tests"
        cp "$EXTRACTED/tests/"*.py "$LIB_DIR/tests/" 2>/dev/null || true

        # Store version
        echo "$REMOTE_SHA" > "$LIB_DIR/VERSION"

        if [ -n "$LOCAL_SHA" ]; then
            log "  Updated: ${LOCAL_SHA:0:8} → ${REMOTE_SHA:0:8}"
        else
            log "  Downloaded version ${REMOTE_SHA:0:8}"
        fi
    fi
fi

# --- Step 3: Python environment ---
log "[3/8] Setting up Python environment..."
if [ ! -d "$VENV_DIR" ] || [ ! -f "$VENV_DIR/bin/python3" ]; then
    rm -rf "$VENV_DIR"
    python3 -m venv "$VENV_DIR"
fi
chmod +x "$VENV_DIR/bin/python3" "$VENV_DIR/bin/pip" "$VENV_DIR/bin/pip3" "$VENV_DIR/bin/activate" 2>/dev/null
find "$VENV_DIR/bin" -type f -exec chmod +x {} \; 2>/dev/null
"$VENV_DIR/bin/python3" -m pip install -q -r "$LIB_DIR/requirements.txt"
log "  Dependencies installed."

# --- Step 4: Create directories and config ---
log "[4/8] Setting up directories and config..."
mkdir -p "$MEMORY_DIR/transcripts"
mkdir -p "$MEMORY_DIR/records"
mkdir -p "$MEMORY_DIR/config"

# Copy CLAUDE.md to synced config dir if not already there
if [ ! -f "$MEMORY_DIR/config/CLAUDE.md" ]; then
    if [ -f "$LIB_DIR/claude-rules-example.md" ]; then
        cp "$LIB_DIR/claude-rules-example.md" "$MEMORY_DIR/config/CLAUDE.md"
        log "  Copied CLAUDE.md to $MEMORY_DIR/config/"
    fi
fi
# Apply CLAUDE.md if no global one exists, or if config version is newer
if [ -f "$MEMORY_DIR/config/CLAUDE.md" ]; then
    if [ ! -f "$HOME/.claude/CLAUDE.md" ] || [ "$MEMORY_DIR/config/CLAUDE.md" -nt "$HOME/.claude/CLAUDE.md" ]; then
        cp "$MEMORY_DIR/config/CLAUDE.md" "$HOME/.claude/CLAUDE.md"
        log "  Applied CLAUDE.md to ~/.claude/CLAUDE.md"
    fi
fi

# Create Syncthing ignore file
cat > "$MEMORY_DIR/.stignore" << 'STIGNORE'
memory.db
memory.db-wal
memory.db-shm
lib/.venv
STIGNORE
log "  Directories ready."

# --- Step 5: Register MCP server ---
log "[5/8] Registering MCP server with Claude Code..."
SERVER_CONFIG="{\"type\":\"stdio\",\"command\":\"$VENV_DIR/bin/python3\",\"args\":[\"$LIB_DIR/server.py\"]}"
if command -v claude &> /dev/null; then
    if claude mcp add-json llm_memory "$SERVER_CONFIG" --scope user 2>/dev/null; then
        log "  MCP server registered."
    else
        log "  MCP server registration skipped (may already be configured)."
    fi
else
    log "  WARNING: 'claude' CLI not found. Add manually:"
    log "    claude mcp add-json llm_memory '$SERVER_CONFIG' --scope user"
fi

# --- Step 6: Install hooks ---
log "[6/8] Installing lifecycle hooks..."
LLM_MEMORY_INSTALLING=1 bash "$LIB_DIR/hooks/install_hooks.sh"

# --- Step 7: Apply shared settings ---
log "[7/8] Applying shared settings..."
if [ -f "$LIB_DIR/settings.yaml" ]; then
    "$VENV_DIR/bin/python3" "$LIB_DIR/apply_settings.py" "$LIB_DIR/settings.yaml" 2>/dev/null
    log "  Applied shared settings from settings.yaml"
fi

# --- Step 8: Initialize database and process transcripts ---
log "[8/8] Initializing database..."

"$VENV_DIR/bin/python3" -c "
import sys
sys.path.insert(0, '$LIB_DIR')
from server import init_db
init_db()
" 2>/dev/null

# Collect any existing transcripts from Claude's project dirs
for src in "$HOME/.claude/projects"/*/*.jsonl; do
    [ -f "$src" ] || continue
    base=$(basename "$src")
    [ -f "$MEMORY_DIR/transcripts/$base" ] || cp "$src" "$MEMORY_DIR/transcripts/$base" 2>/dev/null
done

# Process transcripts into session logs
if [ "$QUIET" = false ]; then
    "$VENV_DIR/bin/python3" -c "
import sys
sys.path.insert(0, '$LIB_DIR')
from process_transcripts import find_transcripts, extract_session_data
from collections import defaultdict

transcripts = find_transcripts()
if not transcripts:
    print('  No existing transcripts found.')
    print('  Memories will be created as you use Claude Code.')
    sys.exit(0)

projects = defaultdict(lambda: {'count': 0, 'turns': 0})
for path, session_id in transcripts:
    data = extract_session_data(path)
    p = data['project']
    projects[p]['count'] += 1
    projects[p]['turns'] += data['turn_count']

print(f'  Found {len(transcripts)} transcripts across {len(projects)} projects:')
print()
for name in sorted(projects):
    info = projects[name]
    print(f'    {name:25s} {info[\"count\"]:3d} sessions, {info[\"turns\"]:5d} turns')
" 2>/dev/null
fi

"$VENV_DIR/bin/python3" "$LIB_DIR/process_transcripts.py" 2>/dev/null
log "  Transcripts processed."

# --- Convenience symlink for dashboard ---
mkdir -p "$HOME/.local/bin"
ln -sf "$LIB_DIR/dashboard.sh" "$HOME/.local/bin/llm-memory-dashboard"
log "  Dashboard available as: llm-memory-dashboard"

log ""
log "=== Install complete ==="
log ""
log "What happens next:"
log "  1. Start a new Claude Code session (the MCP server activates on startup)"
log "  2. Session hooks will auto-load your project context"
log "  3. Auto-updates are checked on each session start"
log ""
log "For multi-device sync with Syncthing:"
log "  python3 $LIB_DIR/setup_syncthing.py"
log ""
log "One-liner to install on another machine:"
log "  curl -sL https://raw.githubusercontent.com/$REPO/$BRANCH/install.sh | bash"
