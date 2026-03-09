"""Apply shared settings from settings.yaml to ~/.claude/settings.json.

Merges permissions and hooks from the YAML template into the existing
settings.json without removing machine-specific accumulated permissions.
"""
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    # Fall back to basic YAML parsing for simple structure
    print("  PyYAML not installed, skipping settings.yaml application")
    sys.exit(0)


def main():
    if len(sys.argv) < 2:
        print("Usage: apply_settings.py <settings.yaml>")
        sys.exit(1)

    yaml_path = Path(sys.argv[1])
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
            existing.add(perm)
        settings.setdefault("permissions", {})["allow"] = sorted(existing)

    # Apply hooks (resolve script paths)
    # Don't touch hooks here - install_hooks.sh handles that

    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)


if __name__ == "__main__":
    main()
