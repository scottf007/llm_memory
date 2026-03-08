#!/bin/bash
set -e

SETTINGS_FILE="$HOME/.claude/settings.json"
HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== LLM Memory — Install Hooks ==="

if [ ! -f "$SETTINGS_FILE" ]; then
    echo '{}' > "$SETTINGS_FILE"
fi

python3 -c "
import json

settings_path = '$SETTINGS_FILE'
hooks_dir = '$HOOKS_DIR'

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
                'timeout': 5
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
        },
        {
            'matcher': 'mcp__llm_memory__memory_store',
            'hooks': [{
                'type': 'command',
                'command': 'date +%s > /tmp/llm_memory_last_save',
                'async': True
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
                'timeout': 10,
                'async': True
            }]
        }
    ]
}

for event, entries in hook_configs.items():
    existing = settings['hooks'].get(event, [])
    for entry in entries:
        # Check if this hook command is already installed
        cmd = entry['hooks'][0]['command']
        already = any(
            cmd in str(h.get('hooks', []))
            for h in existing
        )
        if not already:
            existing.append(entry)
    settings['hooks'][event] = existing

with open(settings_path, 'w') as f:
    json.dump(settings, f, indent=2)

print('All hooks installed:')
print('  - SessionStart  (auto-load recent memories)')
print('  - PostToolUse   (session monitor + save timestamp)')
print('  - PreCompact    (save before compaction)')
print('  - SessionEnd    (auto-save session summary)')
"

echo ""
echo "Restart Claude Code to activate hooks."
