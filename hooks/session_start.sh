#!/bin/bash
# SessionStart hook: auto-loads memories into Claude's context.
# Fires on startup, resume, and after compaction.

INPUT=$(cat)
SOURCE=$(echo "$INPUT" | jq -r '.source // .trigger // empty')
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')

# Sync CLAUDE.md from shared config if newer
SHARED_CLAUDE_MD="$HOME/.claude/memory/config/CLAUDE.md"
LOCAL_CLAUDE_MD="$HOME/.claude/CLAUDE.md"
if [ -f "$SHARED_CLAUDE_MD" ]; then
    if [ ! -f "$LOCAL_CLAUDE_MD" ] || [ "$SHARED_CLAUDE_MD" -nt "$LOCAL_CLAUDE_MD" ]; then
        cp "$SHARED_CLAUDE_MD" "$LOCAL_CLAUDE_MD"
    fi
fi

# Auto-update: check GitHub for newer version and update in background
# Skip if the user has opted out via sentinel file
if [ "$SOURCE" != "compact" ] && [ ! -f "$HOME/.claude/memory/config/no-auto-update" ]; then
    LIB_DIR="$HOME/.claude/memory/lib"
    REPO="${LLM_MEMORY_REPO:-scottf007/llm_memory}"
    BRANCH="${LLM_MEMORY_BRANCH:-main}"
    if [ -f "$LIB_DIR/VERSION" ]; then
        LOCAL_SHA=$(cat "$LIB_DIR/VERSION")
        # Check GitHub API with a short timeout
        REMOTE_SHA=$(timeout 3 curl -sf "https://api.github.com/repos/$REPO/commits/$BRANCH" 2>/dev/null | jq -r '.sha' 2>/dev/null)
        if [ -n "$REMOTE_SHA" ] && [ "$REMOTE_SHA" != "null" ] && [ "$LOCAL_SHA" != "$REMOTE_SHA" ]; then
            # Update in background so we don't block session start
            (bash "$LIB_DIR/install.sh" --update >/dev/null 2>&1) &
            echo "LLM_MEMORY_UPDATING: Updating llm_memory in background (${LOCAL_SHA:0:8} → ${REMOTE_SHA:0:8}). Restart Claude Code for MCP server changes to take effect."
        fi
    fi
fi

# Sweep: collect transcripts not yet captured by hooks.
# Only look at JSONL files modified since the last sweep (sentinel mtime).
# This keeps startup fast on machines with thousands of accumulated transcripts.
if [ "$SOURCE" != "compact" ]; then
    TRANSCRIPT_DIR="$HOME/.claude/memory/transcripts"
    SENTINEL="$TRANSCRIPT_DIR/.last_sweep"
    mkdir -p "$TRANSCRIPT_DIR"
    if [ -f "$SENTINEL" ]; then
        # Incremental: only files newer than the sentinel.
        FIND_ARGS=(-newer "$SENTINEL")
    else
        # First run: full sweep.
        FIND_ARGS=()
    fi
    while IFS= read -r src; do
        [ -n "$src" ] || continue
        base="${src##*/}"
        [ -f "$TRANSCRIPT_DIR/$base" ] || cp "$src" "$TRANSCRIPT_DIR/$base" 2>/dev/null
    done < <(find "$HOME/.claude/projects" -maxdepth 2 -name '*.jsonl' -type f "${FIND_ARGS[@]}" 2>/dev/null)
    touch "$SENTINEL"
fi

DB="$HOME/.claude/memory/memory.db"
if [ ! -f "$DB" ]; then
    exit 0
fi

# Derive project name from cwd
PROJECT=""
if [ -n "$CWD" ]; then
    PROJECT=$(echo "$CWD" | sed -n 's|.*/projects/\([^/]*\).*|\1|p')
fi

# Seed per-project auto-memory dir so the harness template's "this directory
# already exists" claim is true — stops Claude from running mkdir -p on first
# write and getting a permission prompt. Only touches disk when MEMORY.md is
# missing, so it's effectively once per project.
if [ "$SOURCE" != "compact" ] && [ -n "$CWD" ]; then
    ENCODED="${CWD//\//-}"
    ENCODED="${ENCODED//_/-}"
    AUTO_MEM_DIR="$HOME/.claude/projects/${ENCODED}/memory"
    MEMORY_INDEX="$AUTO_MEM_DIR/MEMORY.md"
    if [ ! -f "$MEMORY_INDEX" ]; then
        mkdir -p "$AUTO_MEM_DIR"
        NARR="$HOME/.claude/memory/projects/$PROJECT.narrative.md"
        {
            echo "# Memory Index"
            echo ""
            if [ -n "$PROJECT" ] && [ -f "$NARR" ]; then
                echo "- [Project narrative]($NARR) — living story of $PROJECT, rendered by llm_memory each session. Read for past context."
            fi
            if [ -n "$PROJECT" ]; then
                echo "- Use project_lookup (MCP) to drill into ~/.claude/memory/projects/$PROJECT.json."
            fi
        } > "$MEMORY_INDEX"
    fi
fi

# Export session ID for other hooks
if [ -n "$CLAUDE_ENV_FILE" ]; then
    SESSION_ID=$(echo "$INPUT" | jq -r '.session_id')
    echo "export LLM_MEMORY_SESSION_ID='$SESSION_ID'" >> "$CLAUDE_ENV_FILE"
fi

# Auto-process all unprocessed transcripts (including synced ones)
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [ "$SOURCE" != "compact" ]; then
    "$SCRIPT_DIR/.venv/bin/python3" "$SCRIPT_DIR/process_transcripts.py" --quiet 2>/dev/null
fi

if [ "$SOURCE" = "compact" ]; then
    # After compaction: reload narrative + recent notes
    if [ -n "$PROJECT" ]; then
        NARRATIVE_FILE="$HOME/.claude/memory/projects/$PROJECT.narrative.md"
        if [ -f "$NARRATIVE_FILE" ]; then
            NARRATIVE=$(cat "$NARRATIVE_FILE" 2>/dev/null)
        else
            NARRATIVE=""
        fi
        # Staleness: session_logs for this project minus sessions already merged into {project}.json
        COMPACT_NEW_SESSIONS=$(python3 -c "
import sys, json, pathlib
sys.path.insert(0, '$SCRIPT_DIR')
from conversations import list_sessions
proj='$PROJECT'
logged=set(list_sessions(proj))
try:
    with open('$HOME/.claude/memory/projects/'+proj+'.json') as f: d=json.load(f)
    merged={s.get('session_id') for s in d.get('sessions',[]) if not str(s.get('session_id','')).startswith(('audit-','agent-'))}
except FileNotFoundError:
    merged=set()
print(len(logged - merged))
" 2>/dev/null)
        COMPACT_NEW_SESSIONS=${COMPACT_NEW_SESSIONS:-0}
    else
        NARRATIVE=""
        COMPACT_NEW_SESSIONS="0"
    fi
    if [ -n "$NARRATIVE" ]; then
        echo "=== POST-COMPACTION CONTEXT (auto-injected from llm_memory) ==="
        if [ -n "$PROJECT" ]; then
            echo "## Active project: $PROJECT"
        fi
        echo "$NARRATIVE"
        if [ "$COMPACT_NEW_SESSIONS" -gt "0" ] 2>/dev/null; then
            echo ""
            echo "AUTOMATIC TASK: $COMPACT_NEW_SESSIONS new session(s) since last narrative update for project '$PROJECT'."
            echo "You MUST update the project narrative NOW. Do not ask the user for permission."
            echo "Use an Agent to do this in the background so the user is not blocked."
        fi
        echo "=== END POST-COMPACTION CONTEXT ==="
    fi
else
    # Fresh startup or resume
    if [ -n "$PROJECT" ]; then
        # Narrative source of truth is the rendered .narrative.md file.
        NARRATIVE_FILE="$HOME/.claude/memory/projects/$PROJECT.narrative.md"
        if [ -f "$NARRATIVE_FILE" ]; then
            NARRATIVE=$(cat "$NARRATIVE_FILE" 2>/dev/null)
        else
            NARRATIVE=""
        fi
        # Staleness: count sessions that haven't been merged into {project}.json.sessions[]
        NEW_SESSIONS=$(python3 -c "
import sys, json, pathlib
sys.path.insert(0, '$SCRIPT_DIR')
from conversations import list_sessions
proj='$PROJECT'
logged=set(list_sessions(proj))
try:
    with open('$HOME/.claude/memory/projects/'+proj+'.json') as f: d=json.load(f)
    merged={s.get('session_id') for s in d.get('sessions',[]) if not str(s.get('session_id','')).startswith(('audit-','agent-'))}
except FileNotFoundError:
    merged=set()
print(len(logged - merged))
" 2>/dev/null)
        NEW_SESSIONS=${NEW_SESSIONS:-0}
    else
        NARRATIVE=""
        NEW_SESSIONS="0"
    fi

    echo "=== LOADED MEMORIES (auto-injected from llm_memory) ==="
    # Version banner: shows current SHA every session, loud banner when it changed.
    VERSION_FILE="$HOME/.claude/memory/lib/VERSION"
    LAST_SEEN_FILE="$HOME/.claude/memory/.last_seen_sha"
    if [ -f "$VERSION_FILE" ]; then
        CURRENT_SHA=$(cat "$VERSION_FILE" 2>/dev/null)
        LAST_SHA=$(cat "$LAST_SEEN_FILE" 2>/dev/null)
        if [ -n "$CURRENT_SHA" ] && [ "$CURRENT_SHA" != "$LAST_SHA" ] && [ -n "$LAST_SHA" ]; then
            echo "## llm_memory UPDATED: ${LAST_SHA:0:8} → ${CURRENT_SHA:0:8} (restart Claude Code if MCP server changed)"
        fi
        echo "## llm_memory version: ${CURRENT_SHA:0:8}"
        echo "$CURRENT_SHA" > "$LAST_SEEN_FILE"
    fi
    if [ -n "$PROJECT" ]; then
        echo "## Active project: $PROJECT"
    fi

    if [ -n "$NARRATIVE" ]; then
        echo "## Project Narrative:"
        echo "$NARRATIVE"
    fi
    # Determine what action is needed
    if [ -z "$NARRATIVE" ] && [ -n "$PROJECT" ]; then
        SESSION_COUNT=$(python3 -c "
import sys; sys.path.insert(0, '$SCRIPT_DIR')
from conversations import list_sessions
print(len(list_sessions('$PROJECT')))
" 2>/dev/null)
        if [ "$SESSION_COUNT" -gt "0" ] 2>/dev/null; then
            echo ""
            echo "AUTOMATIC TASK: No narrative exists for project '$PROJECT' but $SESSION_COUNT session(s) found."
            echo "You MUST run the /narrative skill NOW to bootstrap the project narrative. Do not ask the user for permission."
        fi
    elif [ "$NEW_SESSIONS" -gt "0" ] 2>/dev/null; then
        echo ""
        echo "AUTOMATIC TASK: $NEW_SESSIONS new session(s) since last narrative update for project '$PROJECT'."
        echo "You MUST run the /narrative skill NOW. Do not ask the user for permission."
    fi

    # Cross-project needs-narrative scan: iterate projects derived from
    # conversation.md frontmatter, flag any without a rendered narrative or
    # with session_logs outnumbering merged sessions in {project}.json.
    NEEDS_NARRATIVE=$(python3 -c "
import sys, json, pathlib
sys.path.insert(0, '$SCRIPT_DIR')
from conversations import iter_sessions
home='$HOME'
by_project = {}
for fm in iter_sessions():
    proj = fm.get('project')
    if proj:
        by_project.setdefault(proj, set()).add(fm.get('session_id'))
needs=[]
for proj, logged in sorted(by_project.items()):
    narr=pathlib.Path(home+'/.claude/memory/projects/'+proj+'.narrative.md')
    state=pathlib.Path(home+'/.claude/memory/projects/'+proj+'.json')
    if not narr.exists():
        needs.append(proj); continue
    try:
        d=json.loads(state.read_text())
        merged={s.get('session_id') for s in d.get('sessions',[]) if not str(s.get('session_id','')).startswith(('audit-','agent-'))}
    except FileNotFoundError:
        merged=set()
    if logged - merged:
        needs.append(proj)
print('\n'.join(needs))
" 2>/dev/null)
    if [ -n "$NEEDS_NARRATIVE" ]; then
        OTHER_PROJECTS=""
        while IFS= read -r p; do
            [ "$p" = "$PROJECT" ] && continue
            [ -z "$p" ] && continue
            OTHER_PROJECTS="${OTHER_PROJECTS:+$OTHER_PROJECTS, }$p"
        done <<< "$NEEDS_NARRATIVE"
        if [ -n "$OTHER_PROJECTS" ]; then
            echo ""
            echo "OTHER PROJECTS NEEDING NARRATIVES: $OTHER_PROJECTS"
            echo "When convenient, switch to those project directories and generate their narratives."
        fi
    fi

    echo "Use project_lookup for drill-down into the project JSON. Use resume(project) to pick up prior work."
    echo "=== END LOADED MEMORIES ==="
fi

exit 0
