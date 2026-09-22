"""install_hooks.sh must replace its own registrations, never append beside them.

The cleanup predicate used to match the path substring ``/memory/lib/hooks/``.
That is true of the retired ``~/.claude/memory`` root and false of the current
``~/.llm-memory`` one -- ``-memory``, not ``/memory`` -- so after the root
rename every run stripped the old location and appended a fresh copy of its own
entry without removing the one it wrote last time. Observed on three machines
at 1->2, 2->3 and 3->4 groups per event per run.

It is not cosmetic: ``session_end.sh`` enqueues an extraction request, so N
registrations enqueue N requests, upstream of systemd. Sessions that ended on a
machine carrying duplicate hooks left two queued requests each.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLER = REPO_ROOT / "hooks" / "install_hooks.sh"
BASH = shutil.which("bash")
assert BASH

EVENTS = (
    "SessionStart",
    "PostToolUse",
    "PreCompact",
    "SessionEnd",
    "SubagentStart",
    "SubagentStop",
)


def _install(home: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """Run the installer against a scratch HOME, with no codex on PATH."""
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir(exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "LLM_MEMORY_INSTALLING": "1",
            # Deliberately omit codex so only the Claude half runs.
            "PATH": str(empty_bin)
            + os.pathsep
            + str(Path(sys.executable).parent)
            + os.pathsep
            + "/usr/bin:/bin",
        }
    )
    result = subprocess.run(
        [str(BASH), str(INSTALLER)], text=True, capture_output=True, env=env, timeout=60
    )
    assert result.returncode == 0, result.stderr
    return result


def _settings(home: Path) -> dict[str, object]:
    return json.loads((home / ".claude" / "settings.json").read_text())


def _groups(home: Path, event: str) -> list[dict[str, object]]:
    hooks = _settings(home).get("hooks", {})
    assert isinstance(hooks, dict)
    return hooks.get(event, [])


def test_repeated_installs_do_not_accumulate_registrations(tmp_path):
    """Three installs must leave exactly one group per event, not three."""
    home = tmp_path / "home"
    for _ in range(3):
        _install(home, tmp_path)

    for event in EVENTS:
        groups = _groups(home, event)
        assert len(groups) == 1, (
            f"{event} accumulated {len(groups)} registrations across 3 installs; "
            "the installer is appending beside its own entry again"
        )


def test_install_collapses_preexisting_duplicates(tmp_path):
    """A machine that already accumulated duplicates is healed by one install."""
    home = tmp_path / "home"
    claude = home / ".claude"
    claude.mkdir(parents=True)
    hooks_dir = str(REPO_ROOT / "hooks")

    # Three copies of SessionEnd, exactly as the bug produced them: two at the
    # current root and one via the retired root.
    duplicated = {
        "hooks": {
            "SessionEnd": [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"{hooks_dir}/session_end.sh",
                            "timeout": 30,
                        }
                    ],
                },
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"{hooks_dir}/session_end.sh",
                            "timeout": 30,
                        }
                    ],
                },
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "/home/someone/.claude/memory/lib/hooks/session_end.sh",
                            "timeout": 30,
                        }
                    ],
                },
            ]
        }
    }
    (claude / "settings.json").write_text(json.dumps(duplicated, indent=2))

    _install(home, tmp_path)

    groups = _groups(home, "SessionEnd")
    assert len(groups) == 1, f"expected duplicates collapsed to 1, got {len(groups)}"

    commands = [h["command"] for g in groups for h in g["hooks"]]
    assert commands == [f"{hooks_dir}/session_end.sh"]
    assert not any("/.claude/memory/lib/hooks/" in c for c in commands), (
        "the retired install location must not survive"
    )


def test_retired_root_entry_is_removed_even_when_current_root_absent(tmp_path):
    """The original cleanup behaviour still holds: old locations are stripped."""
    home = tmp_path / "home"
    claude = home / ".claude"
    claude.mkdir(parents=True)
    (claude / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "startup|resume|compact",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "/home/someone/.claude/memory/lib/hooks/session_start.sh",
                                    "timeout": 15,
                                }
                            ],
                        }
                    ]
                }
            },
            indent=2,
        )
    )

    _install(home, tmp_path)

    commands = [h["command"] for g in _groups(home, "SessionStart") for h in g["hooks"]]
    assert len(commands) == 1
    assert commands[0] == str(REPO_ROOT / "hooks" / "session_start.sh")


def test_foreign_hooks_on_our_events_are_preserved(tmp_path):
    """We own our registrations only; somebody else's hook must survive."""
    home = tmp_path / "home"
    claude = home / ".claude"
    claude.mkdir(parents=True)
    foreign = {
        "matcher": "",
        "hooks": [{"type": "command", "command": "/usr/local/bin/someone-elses.sh"}],
    }
    (claude / "settings.json").write_text(
        json.dumps({"hooks": {"SessionEnd": [foreign]}}, indent=2)
    )

    _install(home, tmp_path)
    _install(home, tmp_path)

    groups = _groups(home, "SessionEnd")
    assert foreign in groups, "a foreign SessionEnd hook was dropped"
    assert len(groups) == 2, f"expected foreign + ours, got {len(groups)} groups"
    assert groups[0] == foreign, "foreign hooks must keep their original position"


def test_non_hook_settings_are_untouched(tmp_path):
    """The installer must not disturb unrelated settings keys."""
    home = tmp_path / "home"
    claude = home / ".claude"
    claude.mkdir(parents=True)
    (claude / "settings.json").write_text(
        json.dumps({"model": "opus", "permissions": {"allow": ["Bash(ls)"]}}, indent=2)
    )

    _install(home, tmp_path)

    settings = _settings(home)
    assert settings["model"] == "opus"
    assert settings["permissions"] == {"allow": ["Bash(ls)"]}
