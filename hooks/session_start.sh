#!/bin/bash
# SessionStart hook: auto-loads memories into Claude's context.
# Fires on startup, resume, and after compaction.

INPUT=$(cat)
SOURCE=$(echo "$INPUT" | jq -r '.source // .trigger // empty')
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')

MEMORY_DIR="${LLM_MEMORY_HOME:-$HOME/.claude/memory}"
export LLM_MEMORY_HOME="$MEMORY_DIR"
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=hooks/lib_session_common.sh
source "$SCRIPT_DIR/hooks/lib_session_common.sh"

# Failure visibility is an intentionally tiny early path.  A waiting/failed
# worker must be visible without making a new session pay for the legacy
# transcript sweep, liveness coverage, or any model-facing subprocess.
FAST_PROJECT=$(resolve_project_from_cwd "$CWD")
FAST_STATUS="$MEMORY_DIR/projects/$FAST_PROJECT.extraction-status.json"
if [ -n "$FAST_PROJECT" ] && [ -f "$FAST_STATUS" ]; then
    FAST_WARN=$(python3 -c "
import json
try:
 d=json.load(open('$FAST_STATUS'))
 n=int(d.get('unprocessed',0))+int(d.get('stale',0))
 state=d.get('state','idle'); since=d.get('oldest_waiting') or d.get('last_attempt') or ''
 if state in ('waiting','failed') and n: print(f'LLM_MEMORY_WARN: extraction: {n} session(s) waiting for $FAST_PROJECT ({state} since {since})')
except Exception: pass
" 2>/dev/null)
    if [ -n "$FAST_WARN" ]; then
        echo "$FAST_WARN"
        exit 0
    fi
fi

# Sync CLAUDE.md from shared config if newer
SHARED_CLAUDE_MD="$MEMORY_DIR/config/CLAUDE.md"
LOCAL_CLAUDE_MD="$HOME/.claude/CLAUDE.md"
if [ -f "$SHARED_CLAUDE_MD" ]; then
    if [ ! -f "$LOCAL_CLAUDE_MD" ] || [ "$SHARED_CLAUDE_MD" -nt "$LOCAL_CLAUDE_MD" ]; then
        cp "$SHARED_CLAUDE_MD" "$LOCAL_CLAUDE_MD"
    fi
fi

# Auto-update: check GitHub for newer version and update in background
# Skip if the user has opted out via sentinel file
if [ "$SOURCE" != "compact" ] && [ ! -f "$MEMORY_DIR/config/no-auto-update" ]; then
    LIB_DIR="$MEMORY_DIR/lib"
    REPO="${LLM_MEMORY_REPO:-scottf007/llm_memory}"
    BRANCH="${LLM_MEMORY_BRANCH:-main}"
    if [ -f "$LIB_DIR/VERSION" ]; then
        LOCAL_SHA=$(cat "$LIB_DIR/VERSION")
        # Token sources for private-repo support. Priority:
        #   1. $GH_TOKEN
        #   2. ~/.ssh/github_token (rides the existing ~/.ssh Syncthing sync)
        #   3. ~/.claude/memory/config/github_token
        #   4. gh CLI (`gh auth token`)
        # No token = unauthenticated curl (still works while repo is public).
        _tok=""
        if [ -n "$GH_TOKEN" ]; then
            _tok="$GH_TOKEN"
        elif [ -f "$HOME/.ssh/github_token" ]; then
            _tok=$(tr -d '[:space:]' < "$HOME/.ssh/github_token")
        elif [ -f "$MEMORY_DIR/config/github_token" ]; then
            _tok=$(tr -d '[:space:]' < "$MEMORY_DIR/config/github_token")
        elif command -v gh >/dev/null 2>&1; then
            _tok=$(gh auth token 2>/dev/null)
        fi
        _auth=()
        [ -n "$_tok" ] && _auth=(-H "Authorization: token $_tok")
        # Check GitHub API with a short timeout
        REMOTE_SHA=$(timeout 3 curl -sf "${_auth[@]}" "https://api.github.com/repos/$REPO/commits/$BRANCH" 2>/dev/null | jq -r '.sha' 2>/dev/null)
        if [ -n "$REMOTE_SHA" ] && [ "$REMOTE_SHA" != "null" ] && [ "$LOCAL_SHA" != "$REMOTE_SHA" ]; then
            # Update in background so we don't block session start
            (bash "$LIB_DIR/install.sh" --update >/dev/null 2>&1) &
            echo "LLM_MEMORY_UPDATING: Updating llm_memory in background (${LOCAL_SHA:0:8} → ${REMOTE_SHA:0:8}). Restart Claude Code for MCP server changes to take effect."
        fi
    fi
fi

# Post-update self-check: can the installed lib still extract a conversation?
#
# Every upgrade is performed by the OLD install.sh — it is what is on disk when
# the update fires — so a release that adds a new file the pipeline imports gets
# the new code and the old copy instructions. That happened on 2026-08-17: the
# adapters/ package was added, the transition update installed the new
# extract_conversation.py without it, and VERSION was stamped, so re-running
# --update skipped the fix. Extraction was broken for ~15 minutes and nothing
# said so: session_end.sh logs its failure to a file nobody reads, and a session
# that produces no conversation .md is simply absent downstream.
#
# The check imports the modules the pipeline needs to run: extraction
# (extract_conversation + adapters) and merge/render (merger + renderer,
# which transitively import the lib/ package). It runs at every session
# start, not only after an update, so it also catches a lib left broken
# by an interrupted install or a partial sync. The 2026-08-26 incident
# was the same stale-copy class as 17 Aug, against lib/ rather than
# adapters/: VERSION was current, merger.py/renderer.py were present,
# lib/ was not, and this check did not look at them so it stayed silent.
if [ "$SOURCE" != "compact" ]; then
    LIB_DIR="$MEMORY_DIR/lib"
    # Gate on "a lib is installed here", not on the presence of the very file
    # most likely to be missing. Keying the check off extract_conversation.py
    # meant the one failure where that file itself failed to copy went
    # unreported — the guard skipped itself for lack of the thing it guards.
    # A machine with no llm_memory at all stays silent.
    LIB_INSTALLED=false
    if [ -f "$LIB_DIR/VERSION" ]; then
        LIB_INSTALLED=true
    elif ls "$LIB_DIR"/*.py >/dev/null 2>&1; then
        LIB_INSTALLED=true
    fi
    if [ "$LIB_INSTALLED" = true ]; then
        SELFCHECK_PY="$LIB_DIR/.venv/bin/python3"
        [ -x "$SELFCHECK_PY" ] || SELFCHECK_PY="python3"
        # sys.path must be the lib dir and nothing else. `python3 -c` puts the
        # working directory first, so a session started inside a checkout of
        # this repository would import the modules from there and the check
        # would pass while the installed lib was broken — which is precisely
        # the state it exists to detect.
        #
        # adapters.base is imported by name rather than relying on `import
        # adapters` to pull it in: a package whose __init__ stops importing a
        # submodule would otherwise leave that submodule's absence undetected,
        # and base.py carries the protocol every adapter is validated against.
        # merger and renderer are imported by name for the same reason the
        # adapters check exists: a stale install.sh can copy the new
        # merger.py/renderer.py without the lib/ package they import, stamp
        # VERSION, and leave merge/render broken with nothing shouting.
        SELFCHECK_ERR=$("$SELFCHECK_PY" -c "
import os, sys
here = os.getcwd()
sys.path = [p for p in sys.path if p not in ('', here)]
sys.path.insert(0, '$LIB_DIR')
import extract_conversation
import adapters
import adapters.base
import adapters.render
import merger
import renderer
assert adapters.names(), 'no adapters registered'
for _mod in (extract_conversation, adapters, adapters.base, merger, renderer):
    assert os.path.realpath(_mod.__file__).startswith(os.path.realpath('$LIB_DIR')), \
        _mod.__name__ + ' imported from outside the lib'
" 2>&1)
        if [ -n "$SELFCHECK_ERR" ]; then
            # Both channels on purpose. stderr is where a broken hook belongs;
            # stdout is the only one that reaches the model, and a silent
            # extraction failure is exactly what went unnoticed last time.
            echo "LLM_MEMORY_BROKEN: the installed pipeline at $LIB_DIR cannot import required modules." >&2
            echo "$SELFCHECK_ERR" >&2
            echo "LLM_MEMORY_BROKEN: $LIB_DIR is incomplete — extract_conversation/adapters/merger/renderer failed to import, so extraction and/or merge/render cannot run. Repair with: bash $LIB_DIR/install.sh --update --force"
            echo "  detail: $(echo "$SELFCHECK_ERR" | tail -1)"
        fi
    fi
fi

# Sweep: collect transcripts not yet captured by hooks.
# Only look at JSONL files modified since the last sweep (sentinel mtime).
# This keeps startup fast on machines with thousands of accumulated transcripts.
if [ "$SOURCE" != "compact" ]; then
    TRANSCRIPT_DIR="$MEMORY_DIR/transcripts"
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

DB="$MEMORY_DIR/memory.db"
if [ ! -f "$DB" ]; then
    exit 0
fi

# Derive project name from cwd using the shared Codex/Claude rule.
PROJECT="$FAST_PROJECT"

# This is deliberately just a sidecar read: the worker does all coverage and
# model work away from SessionStart's latency-sensitive hook budget.
EXTRACTION_STATUS="$MEMORY_DIR/projects/$PROJECT.extraction-status.json"
if [ -n "$PROJECT" ] && [ -f "$EXTRACTION_STATUS" ]; then
    EXTRACTION_WARN=$(python3 -c "
import json
try:
 d=json.load(open('$EXTRACTION_STATUS'))
 n=int(d.get('unprocessed',0))+int(d.get('stale',0))
 state=d.get('state','idle'); since=d.get('oldest_waiting') or d.get('last_attempt') or ''
 if state in ('waiting','failed') and n: print(f'LLM_MEMORY_WARN: extraction: {n} session(s) waiting for $PROJECT ({state} since {since})')
except Exception: pass
" 2>/dev/null)
    [ -n "$EXTRACTION_WARN" ] && echo "$EXTRACTION_WARN"
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
        NARR="$MEMORY_DIR/projects/$PROJECT.narrative.md"
        {
            echo "# Memory Index"
            echo ""
            if [ -n "$PROJECT" ] && [ -f "$NARR" ]; then
                echo "- [Project narrative]($NARR) — living story of $PROJECT, rendered by llm_memory each session. Read for past context."
            fi
            if [ -n "$PROJECT" ]; then
                echo "- Use project_lookup (MCP) to drill into $MEMORY_DIR/projects/$PROJECT.json."
            fi
        } > "$MEMORY_INDEX"
    fi
fi

# Export session ID for other hooks
if [ -n "$CLAUDE_ENV_FILE" ] && [ -n "$SESSION_ID" ]; then
    echo "export LLM_MEMORY_SESSION_ID='$SESSION_ID'" >> "$CLAUDE_ENV_FILE"
fi

# Auto-process all unprocessed transcripts (including synced ones)
COUNT_PYTHON=$(resolve_hook_python "$SCRIPT_DIR" 2>/dev/null || true)

if [ "$SOURCE" != "compact" ]; then
    "$SCRIPT_DIR/.venv/bin/python3" "$SCRIPT_DIR/process_transcripts.py" --quiet 2>/dev/null
fi

if [ "$SOURCE" = "compact" ]; then
    # After compaction: reload narrative + recent notes
    if [ -n "$PROJECT" ]; then
        NARRATIVE_FILE="$MEMORY_DIR/projects/$PROJECT.narrative.md"
        if [ -f "$NARRATIVE_FILE" ]; then
            NARRATIVE=$(cat "$NARRATIVE_FILE" 2>/dev/null)
        else
            NARRATIVE=""
        fi
        # Staleness: session_logs for this project minus sessions already merged into {project}.json
        if [ -n "$COUNT_PYTHON" ] && count_new_sessions "$PROJECT" "$SCRIPT_DIR" "$COUNT_PYTHON"; then
            COMPACT_NEW_SESSIONS="$NEW_SESSION_COUNT"
        else
            COMPACT_NEW_SESSIONS=""
            echo "LLM_MEMORY_WARN: new-session count unavailable ($NEW_SESSION_COUNT_ERROR)"
        fi
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
        NARRATIVE_FILE="$MEMORY_DIR/projects/$PROJECT.narrative.md"
        if [ -f "$NARRATIVE_FILE" ]; then
            NARRATIVE=$(cat "$NARRATIVE_FILE" 2>/dev/null)
        else
            NARRATIVE=""
        fi
        # Staleness: count sessions that haven't been merged into {project}.json.sessions[]
        if [ -n "$COUNT_PYTHON" ] && count_new_sessions "$PROJECT" "$SCRIPT_DIR" "$COUNT_PYTHON"; then
            NEW_SESSIONS="$NEW_SESSION_COUNT"
        else
            NEW_SESSIONS=""
            echo "LLM_MEMORY_WARN: new-session count unavailable ($NEW_SESSION_COUNT_ERROR)"
        fi
    else
        NARRATIVE=""
        NEW_SESSIONS="0"
    fi

    # Age-based liveness (Roadmap #4). File age is not the measure — a dormant
    # project's old narrative is correct. Compose with narrative_coverage's
    # filtered-count + _stale_session rather than duplicating them here.
    # Contained on failure: an empty result means "not aging"; existing
    # count-based checks still run.
    LIVENESS=""
    if [ -n "$PROJECT" ]; then
        LIVENESS_PY="$MEMORY_DIR/lib/.venv/bin/python3"
        if [ ! -x "$LIVENESS_PY" ]; then
            LIVENESS_PY="$SCRIPT_DIR/.venv/bin/python3"
        fi
        if [ ! -x "$LIVENESS_PY" ]; then
            LIVENESS_PY="python3"
        fi
        LIVENESS=$("$LIVENESS_PY" -c "
import json, sys
sys.path.insert(0, '$SCRIPT_DIR')
from server import compute_narrative_coverage, narrative_liveness
print(json.dumps(narrative_liveness(compute_narrative_coverage('$PROJECT')), separators=(',', ':')))
" 2>/dev/null)
    fi
    if [ -n "$LIVENESS" ]; then
        LIVENESS_AGING=$(printf '%s\n' "$LIVENESS" | jq -r '.aging // false' 2>/dev/null || echo false)
        LIVENESS_DAYS=$(printf '%s\n' "$LIVENESS" | jq -r '.signal_days // .narrative_age_days // empty' 2>/dev/null || true)
        LIVENESS_THRESH=$(printf '%s\n' "$LIVENESS" | jq -r '.threshold_days // 7' 2>/dev/null || echo 7)
        LIVENESS_UNPROC=$(printf '%s\n' "$LIVENESS" | jq -r '.unprocessed_count // 0' 2>/dev/null || echo 0)
        LIVENESS_STALE=$(printf '%s\n' "$LIVENESS" | jq -r '.stale_count // 0' 2>/dev/null || echo 0)
        LIVENESS_GREW=$(printf '%s\n' "$LIVENESS" | jq -r '.worst_stale_grew_days // empty' 2>/dev/null || true)
    else
        LIVENESS_AGING=false
        LIVENESS_DAYS=""
        LIVENESS_THRESH=7
        LIVENESS_UNPROC=0
        LIVENESS_STALE=0
        LIVENESS_GREW=""
    fi

    echo "=== LOADED MEMORIES (auto-injected from llm_memory) ==="
    # Version banner: shows current SHA every session, loud banner when it changed.
    VERSION_FILE="$MEMORY_DIR/lib/VERSION"
    LAST_SEEN_FILE="$MEMORY_DIR/.last_seen_sha"
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

    # Surface a rotating sample of active suggestions — no hard age cutoff.
    # Weighted-random by age (older → slightly higher chance), but any active
    # suggestion can appear. Deterministic per session_id so the same session
    # on resume shows the same picks. Scales to years of accumulation without
    # burying ideas you pick up and put down.
    if [ -n "$PROJECT" ] && [ -n "$SESSION_ID" ]; then
        SUGGESTION_SAMPLE=$(python3 -c "
import json, random
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, '$SCRIPT_DIR')
from tools.memory_config import memory_root
path = memory_root() / 'projects' / '$PROJECT.json'
if not path.exists():
    import sys; sys.exit(0)

try:
    d = json.loads(path.read_text())
except Exception:
    import sys; sys.exit(0)

now = datetime.now(timezone.utc)
pool = []
for s in d.get('suggestions', []) or []:
    if s.get('status') != 'active': continue
    ts = s.get('last_touched_at') or ''
    try:
        t = datetime.fromisoformat(ts.replace('Z','+00:00'))
    except Exception:
        continue
    age = max(1, (now - t).days)
    pool.append((s, age))

if not pool:
    import sys; sys.exit(0)

rng = random.Random('$SESSION_ID')
k = min(3, len(pool))
picked = []
remaining = list(pool)
for _ in range(k):
    total = sum(w for _, w in remaining)
    r = rng.random() * total
    cum = 0.0
    for idx, (item, w) in enumerate(remaining):
        cum += w
        if cum >= r:
            picked.append((item, w))
            remaining.pop(idx)
            break

for item, age in sorted(picked, key=lambda p: p[1], reverse=True):
    sid = item.get('id','')
    text = (item.get('text') or '')[:140]
    print(f'  [{age}d] {sid}: {text}')
" 2>/dev/null)
        if [ -n "$SUGGESTION_SAMPLE" ]; then
            echo ""
            echo "## Active suggestions (rotating sample) — act, reject, or leave for next rotation"
            echo "$SUGGESTION_SAMPLE"
        fi
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
        if [ "$LIVENESS_AGING" = "true" ]; then
            echo "AGE: narrative is ${LIVENESS_DAYS}d stale with unprocessed content (threshold ${LIVENESS_THRESH}d; ${LIVENESS_UNPROC} unprocessed, ${LIVENESS_STALE} grown-after-merge). You MUST run /narrative NOW."
        fi
    elif [ "$LIVENESS_AGING" = "true" ]; then
        echo ""
        echo "AUTOMATIC TASK: project '$PROJECT' narrative is ${LIVENESS_DAYS}d stale with unprocessed content (${LIVENESS_UNPROC} unprocessed, ${LIVENESS_STALE} grown-after-merge, worst +${LIVENESS_GREW}d). File age is not the measure — unprocessed transcripts with real content have been waiting. You MUST run the /narrative skill NOW. Do not ask the user for permission."
    fi

    # Cross-project needs-narrative scan: iterate projects derived from
    # conversation.md frontmatter, flag any without a rendered narrative or
    # with session_logs outnumbering merged sessions in {project}.json.
    NEEDS_NARRATIVE=$(python3 -c "
import sys, json, pathlib
sys.path.insert(0, '$SCRIPT_DIR')
from conversations import iter_sessions
from tools.memory_config import memory_root
by_project = {}
for fm in iter_sessions():
    proj = fm.get('project')
    if proj:
        by_project.setdefault(proj, set()).add(fm.get('session_id'))
needs=[]
for proj, logged in sorted(by_project.items()):
    narr=memory_root() / 'projects' / (proj+'.narrative.md')
    state=memory_root() / 'projects' / (proj+'.json')
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
