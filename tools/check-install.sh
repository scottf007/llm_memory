#!/bin/bash
# Read-only health check for an llm_memory installation on THIS machine.
#
# Written 22 Sep 2026 after the memory root moved to a generic location
# (~/.llm-memory, with ~/.claude/memory kept as a compatibility symlink) and
# it became unclear whether the other syncing machines had followed.
#
# This script CHANGES NOTHING. It prints findings and the commands to fix
# them. Run it on each machine:  bash ~/projects/llm_memory/tools/check-install.sh
#
# The dangerous case it exists to catch: a machine where ~/.claude/memory is a
# REAL DIRECTORY rather than a symlink. That machine has a second, unsynced
# memory root, and its narratives silently diverge from everyone else's.

ok=0; warn=0; bad=0
P() { printf '  %-9s %s\n' "$1" "$2"; }
pass() { P "[ ok ]" "$1"; ok=$((ok+1)); }
warning() { P "[warn]" "$1"; warn=$((warn+1)); }
fail() { P "[FAIL]" "$1"; bad=$((bad+1)); }
hdr() { printf '\n== %s ==\n' "$1"; }

echo "llm_memory install check — $(hostname) — $(date '+%Y-%m-%d %H:%M')"

# ---------------------------------------------------------------- memory root
hdr "Memory root"
CANON="$HOME/.llm-memory"
LEGACY="$HOME/.claude/memory"
echo "  LLM_MEMORY_HOME = ${LLM_MEMORY_HOME:-<unset, defaults to $LEGACY>}"

canon_exists=0; [ -d "$CANON" ] && canon_exists=1
if [ -L "$LEGACY" ]; then
    tgt=$(readlink -f "$LEGACY")
    if [ "$tgt" = "$(readlink -f "$CANON")" ]; then
        pass "$LEGACY -> $tgt (compatibility symlink, correct)"
    else
        warning "$LEGACY symlinks to $tgt, not $CANON"
    fi
elif [ -d "$LEGACY" ]; then
    if [ "$canon_exists" = 1 ]; then
        fail "TWO SEPARATE ROOTS. $LEGACY is a real directory AND $CANON exists."
        echo "           This machine is diverging. Compare before touching anything:"
        printf '             %-28s %s files, newest %s\n' "$LEGACY/projects" \
            "$(ls -1 "$LEGACY/projects"/*.json 2>/dev/null | wc -l)" \
            "$(ls -1t "$LEGACY/projects"/*.json 2>/dev/null | head -1 | xargs -r stat -c %y | cut -d. -f1)"
        printf '             %-28s %s files, newest %s\n' "$CANON/projects" \
            "$(ls -1 "$CANON/projects"/*.json 2>/dev/null | wc -l)" \
            "$(ls -1t "$CANON/projects"/*.json 2>/dev/null | head -1 | xargs -r stat -c %y | cut -d. -f1)"
        echo "           DO NOT delete either until you have compared them."
    else
        warning "$LEGACY is a real directory and $CANON does not exist (pre-move layout)."
        echo "           To adopt the synced layout, once Syncthing has $CANON populated:"
        echo "             mv $LEGACY $LEGACY.pre-move && ln -s $CANON $LEGACY"
    fi
else
    [ "$canon_exists" = 1 ] && warning "no $LEGACY shim; tools using the old path will fail" \
                           || fail "no memory root found at $CANON or $LEGACY"
fi

ROOT="${LLM_MEMORY_HOME:-$LEGACY}"
[ -d "$ROOT" ] || { echo; echo "No usable root; stopping."; exit 1; }
echo "  resolved root   = $(readlink -f "$ROOT")"

# ----------------------------------------------------------------- syncthing
hdr "Syncthing"
CFG=""
for c in "$HOME/.local/state/syncthing/config.xml" "$HOME/.config/syncthing/config.xml"; do
    [ -f "$c" ] && CFG="$c" && break
done
if [ -z "$CFG" ]; then
    warning "no syncthing config found; cannot verify this machine syncs the memory root"
else
    paths=$(grep -o 'path="[^"]*"' "$CFG" | sed 's/path="//;s/"$//' | sort -u)
    if printf '%s\n' "$paths" | grep -qx "$(readlink -f "$ROOT")"; then
        pass "resolved root is a synced folder"
    else
        fail "resolved root $(readlink -f "$ROOT") is NOT in syncthing's folder list:"
        printf '%s\n' "$paths" | sed 's/^/             /'
    fi
    if [ -f "$ROOT/.stignore" ]; then
        grep -qx 'lib/.venv' "$ROOT/.stignore" && pass ".stignore excludes lib/.venv (machine-specific)" \
                                               || warning ".stignore does NOT exclude lib/.venv — venvs will fight across machines"
    else
        warning "no .stignore in the root; memory.db and lib/.venv will sync and conflict"
    fi
fi

# --------------------------------------------------------------- install bits
hdr "Installed components"
LIB="$ROOT/lib"
[ -d "$LIB" ]              && pass "lib/ present"            || fail "lib/ missing — run install.sh"
[ -d "$LIB/.venv" ]        && pass "lib/.venv present"       || fail "lib/.venv missing — run install.sh (venv is per-machine, never synced)"
[ -d "$LIB/hooks" ]        && pass "lib/hooks present"       || fail "lib/hooks missing — run install.sh"
[ -x "$LIB/.venv/bin/python3" ] && pass "venv python usable" || fail "venv python not executable — run install.sh"

SET="$HOME/.claude/settings.json"
if [ -f "$SET" ]; then
    missing=0
    for h in session_start session_end subagent_start pre_compact; do
        ref=$(grep -o "\"[^\"]*hooks/$h\.sh\"" "$SET" | head -1 | tr -d '"')
        if [ -z "$ref" ]; then warning "hook $h.sh not registered in settings.json"; missing=1
        elif [ ! -f "$ref" ]; then fail "settings.json points at MISSING $ref"; missing=1; fi
    done
    [ "$missing" = 0 ] && pass "all lifecycle hooks registered and present"
else
    fail "no $SET"
fi

# ------------------------------------------------------------------- skills
hdr "Skills (installed copy is per-machine, NOT synced)"
for s in "$LIB"/skills/*/; do
    [ -d "$s" ] || continue
    name=$(basename "$s")
    srcf="$s/SKILL.md"; instf="$HOME/.claude/skills/$name/SKILL.md"
    [ -f "$srcf" ] || continue
    if [ ! -f "$instf" ]; then
        fail "skill '$name' not installed to ~/.claude/skills — run install.sh"
    elif cmp -s "$srcf" "$instf"; then
        pass "skill '$name' installed and current"
    else
        fail "skill '$name' INSTALLED COPY IS STALE (synced source is newer) — run install.sh"
        echo "             source:    $(date -r "$srcf" '+%d %b %H:%M')"
        echo "             installed: $(date -r "$instf" '+%d %b %H:%M')"
    fi
done

hdr "Agents"
if [ -d "$LIB/agents" ]; then
    for a in "$LIB"/agents/*.md; do
        [ -f "$a" ] || continue
        n=$(basename "$a"); i="$HOME/.claude/agents/$n"
        [ -f "$i" ] && cmp -s "$a" "$i" && pass "agent '$n' current" \
            || fail "agent '$n' missing or stale in ~/.claude/agents — run install.sh"
    done
else
    warning "no lib/agents directory"
fi

# ------------------------------------------------------------------- systemd
hdr "Extraction worker (systemd user units)"
if command -v systemctl >/dev/null 2>&1; then
    for u in llm-memory-extract.timer llm-memory-extract.service; do
        if systemctl --user cat "$u" >/dev/null 2>&1; then
            printf '  %-9s %-34s enabled=%s active=%s\n' "[info]" "$u" \
                "$(systemctl --user is-enabled "$u" 2>&1)" "$(systemctl --user is-active "$u" 2>&1)"
        else
            warning "$u not installed"
        fi
    done
    echo "           NOTE (22 Sep 2026): the timer is deliberately DISABLED on the"
    echo "           primary machine pending queue-gating fixes. Disabled is expected."
else
    warning "systemctl not available"
fi

# ------------------------------------------------------------------ requests
hdr "Extraction request queue"
RQ="$ROOT/runtime/extraction-requests"
if [ -d "$RQ" ]; then
    n=$(ls -1 "$RQ"/*.json 2>/dev/null | wc -l)
    if [ "$n" -gt 500 ]; then
        fail "$n queued requests — almost certainly un-retired noise (see tools/ and the purge on 22 Sep 2026)"
    else
        pass "$n queued request(s)"
    fi
else
    warning "no runtime/extraction-requests directory"
fi

printf '\n== Summary ==\n  %d ok, %d warning(s), %d failure(s)\n' "$ok" "$warn" "$bad"
if [ "$bad" -gt 0 ]; then
    echo
    echo "  Most failures above are fixed by re-running the installer on THIS machine:"
    echo "    bash ~/projects/llm_memory/install.sh --update"
    echo "  EXCEPT a 'TWO SEPARATE ROOTS' failure — compare the two roots by hand first."
fi
exit 0
