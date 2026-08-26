"""Apply shared settings from settings.yaml to ~/.claude/settings.json.

Merges permissions and hooks from the YAML template into the existing
settings.json without removing machine-specific accumulated permissions.
"""
import json
import sys
from pathlib import Path

from tools.memory_config import memory_root

try:
    import yaml
except ImportError:
    # Fall back to basic YAML parsing for simple structure
    print("  PyYAML not installed, skipping settings.yaml application")
    sys.exit(0)


def _expand_home(perm: str, home: str) -> str:
    """Expand a literal '~' placeholder in a permission pattern to `home`.

    settings.yaml uses "~" instead of a baked-in absolute path so the file
    stays machine-independent; this is where that placeholder gets resolved.
    """
    memory_placeholder = "~/.claude/memory"
    if memory_placeholder in perm:
        perm = perm.replace(memory_placeholder, str(memory_root()))
    return perm.replace("~", home)


def main():
    if len(sys.argv) < 2:
        print("Usage: apply_settings.py <settings.yaml>")
        sys.exit(1)

    yaml_path = Path(sys.argv[1])

    try:
        home = str(Path.home())
    except RuntimeError as e:
        print(f"  apply_settings.py: could not resolve home directory, "
              f"permissions using '~' were NOT applied: {e}", file=sys.stderr)
        sys.exit(1)

    settings_path = Path.home() / ".claude" / "settings.json"
    hooks_dir = yaml_path.parent / "hooks"

    with open(yaml_path) as f:
        shared = yaml.safe_load(f)

    # Load existing settings or start fresh
    if settings_path.exists():
        with open(settings_path) as f:
            settings = json.load(f)
    else:
        settings = {}

    # Apply top-level settings
    if "defaultMode" in shared:
        settings.setdefault("permissions", {})["defaultMode"] = shared["defaultMode"]
    if "skipDangerousModePermissionPrompt" in shared:
        settings["skipDangerousModePermissionPrompt"] = shared["skipDangerousModePermissionPrompt"]

    # Merge permissions (add shared ones, don't remove existing)
    if "permissions" in shared:
        existing = set(settings.get("permissions", {}).get("allow", []))
        for perm in shared["permissions"]:
            existing.add(_expand_home(perm, home))
        settings.setdefault("permissions", {})["allow"] = sorted(existing)

    # Apply hooks (resolve script paths)
    # Don't touch hooks here - install_hooks.sh handles that

    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)


if __name__ == "__main__":
    main()
