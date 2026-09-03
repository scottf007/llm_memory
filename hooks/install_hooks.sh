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

# Codex keeps its hook registry separate from Claude's settings.json.  Do not
# create a Codex footprint on machines without the Codex client.
if command -v codex >/dev/null 2>&1; then
CODEX_HOOKS_FILE="$HOME/.codex/hooks.json"
mkdir -p "$(dirname "$CODEX_HOOKS_FILE")"
python3 - "$CODEX_HOOKS_FILE" "$HOOKS_DIR" <<'PYEOF'
import json
import re
import sys

hooks_path = sys.argv[1]
hooks_dir = sys.argv[2]
try:
    raw = open(hooks_path).read()
    hooks = json.loads(raw)
except (FileNotFoundError, json.JSONDecodeError):
    raw = ""
    hooks = {}

# Preserve the file's house style when making the first Codex addition.  JSON
# cannot retain arbitrary trivia, but matching its indentation and final
# newline keeps foreign entries stable in normally formatted hooks files.
indent_match = re.search(r"\n([ \t]+)\"", raw)
indent = indent_match.group(1) if indent_match else 2
indent_text = indent if isinstance(indent, str) else " " * indent
trailing_newline = raw.endswith("\n")

def raw_top_level_members(text):
    """Return untouched top-level members, minus their leading indentation."""
    if not text:
        return {}
    decoder = json.JSONDecoder()
    members = {}
    index = text.find("{") + 1
    limit = text.rfind("}")
    while index and index < limit:
        while index < limit and text[index].isspace():
            index += 1
        if index >= limit:
            break
        member_start = index
        key, index = decoder.raw_decode(text, index)
        while index < limit and text[index].isspace():
            index += 1
        if index >= limit or text[index] != ":":
            return {}
        index += 1
        while index < limit and text[index].isspace():
            index += 1
        value_start = index
        _value, index = decoder.raw_decode(text, index)
        members[key] = text[member_start:index]
        while index < limit and text[index].isspace():
            index += 1
        if index < limit and text[index] == ",":
            index += 1
    return members

raw_members = raw_top_level_members(raw)
original_hooks = dict(hooks)

owned_suffixes = ("/codex_session_start.sh", "/codex_session_end.sh")

def is_owned(entry):
    return any(
        any(suffix in str(hook.get("command", "")) for suffix in owned_suffixes)
        for hook in entry.get("hooks", [])
    )

configs = {
    "SessionStart": [{
        "matcher": "startup|resume",
        "hooks": [{"type": "command", "command": f"{hooks_dir}/codex_session_start.sh", "timeout": 15}],
    }],
    "SessionEnd": [{
        "matcher": "",
        "hooks": [{"type": "command", "command": f"{hooks_dir}/codex_session_end.sh", "timeout": 30}],
    }],
}
for event, additions in configs.items():
    existing = hooks.get(event, [])
    hooks[event] = [entry for entry in existing if not is_owned(entry)] + additions

def raw_list_entries(member):
    """Extract raw array elements so foreign hook entries keep their trivia."""
    if not member:
        return []
    decoder = json.JSONDecoder()
    start = member.find("[")
    if start < 0:
        return []
    _array, end = decoder.raw_decode(member, start)
    entries = []
    index = start + 1
    while index < end:
        while index < end and member[index].isspace():
            index += 1
        if index >= end or member[index] == "]":
            break
        entry_start = index
        _entry, index = decoder.raw_decode(member, index)
        entries.append(member[entry_start:index])
        while index < end and member[index].isspace():
            index += 1
        if index < end and member[index] == ",":
            index += 1
    return entries

def render_owned_event(event, additions):
    original_entries = original_hooks.get(event, [])
    raw_entries = raw_list_entries(raw_members.get(event, ""))
    foreign = []
    if len(original_entries) == len(raw_entries):
        foreign = [raw_entry for entry, raw_entry in zip(original_entries, raw_entries) if not is_owned(entry)]
    else:
        foreign = [json.dumps(entry, indent=indent, ensure_ascii=False) for entry in original_entries if not is_owned(entry)]
    rendered_entries = foreign + [json.dumps(entry, indent=indent, ensure_ascii=False) for entry in additions]
    if not rendered_entries:
        return json.dumps(event, ensure_ascii=False) + ": []"
    child_indent = indent_text + indent_text
    return (json.dumps(event, ensure_ascii=False) + ": [\n" +
            ",\n".join(child_indent + entry for entry in rendered_entries) +
            "\n" + indent_text + "]")

# Reuse foreign top-level members verbatim rather than reserializing them. This
# retains their chosen line breaks/inline objects; our two owned event lists
# are the only portions deliberately rewritten.
members = []
for event, value in hooks.items():
    if event in configs:
        member = render_owned_event(event, configs[event])
    elif event in raw_members:
        member = raw_members[event]
    else:
        member = json.dumps(event, ensure_ascii=False) + ": " + json.dumps(value, indent=indent, ensure_ascii=False)
    members.append(indent_text + member)
rendered = "{\n" + ",\n".join(members) + "\n}"
with open(hooks_path, "w") as f:
    f.write(rendered)
    if trailing_newline:
        f.write("\n")
PYEOF

echo "For Codex, run /hooks once and trust the two llm_memory hooks."
fi

echo ""
echo "Restart Claude Code to activate hooks."
