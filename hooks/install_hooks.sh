#!/bin/bash
set -e

SETTINGS_FILE="$HOME/.claude/settings.json"
HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# When called standalone, show header. When called from install.sh, parent handles it.
if [ -z "$LLM_MEMORY_INSTALLING" ]; then
    echo "=== LLM Memory — Install Hooks ==="
fi

if [ ! -f "$SETTINGS_FILE" ]; then
    echo '{}' > "$SETTINGS_FILE"
fi

# The python below is fed through a QUOTED heredoc (<<'PYEOF') and the two
# paths it needs arrive as argv rather than being interpolated. This is not a
# style preference. The previous form was `python3 -c "` ... `"` — a
# double-quoted bash string — so bash evaluated anything inside it that looked
# like shell syntax before python ever saw it. A `claude --resume` written in
# backticks inside a python COMMENT (see SessionEnd below) was therefore
# EXECUTED as a command on every install, on every machine with the claude CLI
# on PATH. A quoted heredoc cannot do that, and it also stops a home directory
# containing a quote or a $ from breaking the script.
python3 - "$SETTINGS_FILE" "$HOOKS_DIR" <<'PYEOF'
import json
import sys

settings_path = sys.argv[1]
hooks_dir = sys.argv[2]

with open(settings_path, 'r') as f:
    settings = json.load(f)

if 'hooks' not in settings:
    settings['hooks'] = {}

# All hooks to install
hook_configs = {
    'SessionStart': [
        {
            'matcher': 'startup|resume|compact',
            'hooks': [{
                'type': 'command',
                'command': f'{hooks_dir}/session_start.sh',
                'timeout': 15
            }]
        }
    ],
    'PostToolUse': [
        {
            'matcher': '',
            'hooks': [{
                'type': 'command',
                'command': f'{hooks_dir}/session_monitor.sh',
                'timeout': 5
            }]
        }
    ],
    'PreCompact': [
        {
            'matcher': '',
            'hooks': [{
                'type': 'command',
                'command': f'{hooks_dir}/pre_compact.sh',
                'timeout': 5
            }]
        }
    ],
    'SessionEnd': [
        {
            'matcher': '',
            'hooks': [{
                'type': 'command',
                'command': f'{hooks_dir}/session_end.sh',
                # SessionEnd MUST be synchronous. When async, Claude exits as
                # soon as the hook launches and the python subprocess that
                # writes conversations/<sid>.md gets reaped before completing,
                # silently dropping the session from the /narrative pipeline.
                # This is especially visible for `claude --resume` chains where
                # the final session has no follow-on session_start.sh sweep to
                # recover from the missed SessionEnd.
                'timeout': 30
            }]
        }
    ],
    'SubagentStart': [
        {
            'matcher': '',
            'hooks': [{
                'type': 'command',
                'command': f'{hooks_dir}/subagent_start.sh',
                'timeout': 5
            }]
        }
    ],
    'SubagentStop': [
        {
            'matcher': '',
            'hooks': [{
                'type': 'command',
                'command': f'{hooks_dir}/subagent_stop.sh',
                'timeout': 5
            }]
        }
    ]
}

for event, entries in hook_configs.items():
    existing = settings['hooks'].get(event, [])
    # Remove any old llm_memory hooks (from previous install locations)
    existing = [
        h for h in existing
        if not any(
            '/memory/lib/hooks/' in str(hook.get('command', ''))
            or 'llm_memory_last_save' in str(hook.get('command', ''))
            for hook in h.get('hooks', [])
        )
    ]
    # Add new hooks
    for entry in entries:
        existing.append(entry)
    settings['hooks'][event] = existing

with open(settings_path, 'w') as f:
    json.dump(settings, f, indent=2)

print('All hooks installed:')
print('  - SessionStart   (auto-load narrative + memories)')
print('  - PostToolUse    (session size monitor)')
print('  - PreCompact     (save before compaction)')
print('  - SessionEnd     (auto-save session summary)')
print('  - SubagentStart  (inject narrative into agents)')
print('  - SubagentStop   (notify parent of narrative updates)')
PYEOF

echo ""
echo "Restart Claude Code to activate hooks."
