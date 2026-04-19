"""
Set up Syncthing to share the ~/.claude/memory/ directory.

Finds the local Syncthing instance, adds the memory folder, and applies
the .stignore. Works on Linux, macOS, and WSL (where Syncthing runs on Windows).

Usage:
    python setup_syncthing.py
    python setup_syncthing.py --dry-run
"""

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError


MEMORY_DIR = Path.home() / ".claude" / "memory"
FOLDER_ID = "llm-memory"
FOLDER_LABEL = "LLM Memory"


def find_syncthing_config() -> Path | None:
    """Find the Syncthing config.xml across platforms."""
    candidates = [
        # Linux (new location)
        Path.home() / ".local" / "state" / "syncthing" / "config.xml",
        # Linux (old location)
        Path.home() / ".config" / "syncthing" / "config.xml",
        # macOS
        Path.home() / "Library" / "Application Support" / "Syncthing" / "config.xml",
    ]

    # WSL: check Windows Syncthing via /mnt/c/
    wsl_candidates = list(Path("/mnt/c/Users").glob("*/AppData/Local/Syncthing/config.xml"))
    candidates.extend(wsl_candidates)

    for path in candidates:
        if path.exists():
            return path
    return None


def parse_config(config_path: Path) -> tuple[str, str]:
    """Extract API address and key from config.xml."""
    tree = ET.parse(config_path)
    root = tree.getroot()

    gui = root.find("gui")
    if gui is None:
        raise RuntimeError("No <gui> section in Syncthing config")

    address = gui.findtext("address", "127.0.0.1:8384")
    api_key = gui.findtext("apikey", "")

    if not api_key:
        raise RuntimeError("No API key found in Syncthing config")

    return address, api_key


def is_wsl() -> bool:
    """Check if running inside WSL."""
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except Exception:
        return False


def get_folder_path() -> str:
    """Get the folder path Syncthing should use.

    On WSL, Syncthing runs on Windows so needs the UNC path.
    On native Linux/macOS, use the regular path.
    """
    if is_wsl():
        # Get the WSL distro name
        try:
            result = subprocess.run(
                ["wslpath", "-w", str(MEMORY_DIR)],
                capture_output=True, text=True, check=True,
            )
            return result.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            # Fallback: construct UNC path manually
            try:
                distro = subprocess.run(
                    ["wsl.exe", "-l", "-q"],
                    capture_output=True, text=True,
                ).stdout.strip().split("\n")[0].strip("\x00")
            except Exception:
                distro = "Ubuntu"
            return f"\\\\wsl.localhost\\{distro}\\home\\{Path.home().name}\\.claude\\memory"
    else:
        return str(MEMORY_DIR)


def api_call(address: str, api_key: str, endpoint: str, method: str = "GET", data: dict = None) -> dict | None:
    """Make a Syncthing REST API call."""
    # Determine how to reach the API
    url = f"http://{address}{endpoint}"

    if is_wsl() and address.startswith("127.0.0.1"):
        # WSL can't reach Windows localhost directly on newer WSL2
        # Try the Windows host IP from resolv.conf
        try:
            resolv = Path("/etc/resolv.conf").read_text()
            for line in resolv.splitlines():
                if line.startswith("nameserver"):
                    host_ip = line.split()[1]
                    port = address.split(":")[1] if ":" in address else "8384"
                    url = f"http://{host_ip}:{port}{endpoint}"
                    break
        except Exception:
            pass

    headers = {"X-API-Key": api_key}
    body = None
    if data is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(data).encode()

    req = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                return json.loads(resp.read())
            return None
    except URLError:
        # Try plain localhost as fallback
        if "127.0.0.1" not in url:
            port = address.split(":")[1] if ":" in address else "8384"
            fallback_url = f"http://127.0.0.1:{port}{endpoint}"
            req = Request(fallback_url, data=body, headers=headers, method=method)
            try:
                with urlopen(req, timeout=5) as resp:
                    if resp.status == 200:
                        return json.loads(resp.read())
            except URLError:
                pass
        return None


def folder_exists(address: str, api_key: str) -> bool:
    """Check if the llm-memory folder is already configured."""
    config = api_call(address, api_key, "/rest/config/folders")
    if config is None:
        return False
    return any(f.get("id") == FOLDER_ID for f in config)


def get_device_id(address: str, api_key: str) -> str | None:
    """Get this device's Syncthing ID."""
    status = api_call(address, api_key, "/rest/system/status")
    if status:
        return status.get("myID")
    return None


def get_other_devices(address: str, api_key: str, my_id: str) -> list[dict]:
    """Get all configured devices except this one."""
    config = api_call(address, api_key, "/rest/config/devices")
    if config is None:
        return []
    return [d for d in config if d.get("deviceID") != my_id]


def add_folder(address: str, api_key: str, folder_path: str, devices: list[dict], dry_run: bool = False) -> bool:
    """Add the memory folder to Syncthing config."""
    device_entries = [
        {"deviceID": d["deviceID"], "introducedBy": "", "encryptionPassword": ""}
        for d in devices
    ]

    folder_config = {
        "id": FOLDER_ID,
        "label": FOLDER_LABEL,
        "path": folder_path,
        "type": "sendreceive",
        "rescanIntervalS": 60,
        "fsWatcherEnabled": True,
        "fsWatcherDelayS": 5,
        "devices": device_entries,
        "minDiskFree": {"value": 1, "unit": "%"},
        "versioning": {"type": ""},
        "ignorePerms": True,
        "autoNormalize": True,
    }

    if dry_run:
        print(f"  Would add folder: {FOLDER_ID}")
        print(f"  Path: {folder_path}")
        print(f"  Shared with {len(devices)} device(s)")
        return True

    result = api_call(address, api_key, "/rest/config/folders", method="POST", data=folder_config)
    return result is not None


def folder_exists_in_xml(config_path: Path) -> bool:
    """Check if the folder is already in config.xml."""
    tree = ET.parse(config_path)
    root = tree.getroot()
    for folder in root.findall("folder"):
        if folder.get("id") == FOLDER_ID:
            return True
    return False


def get_devices_from_xml(config_path: Path) -> list[str]:
    """Get all device IDs from config.xml."""
    tree = ET.parse(config_path)
    root = tree.getroot()
    return [d.get("id") for d in root.findall("device") if d.get("id")]


def add_folder_to_xml(config_path: Path, folder_path: str, dry_run: bool = False) -> bool:
    """Add the memory folder directly to config.xml (fallback when API unreachable)."""
    tree = ET.parse(config_path)
    root = tree.getroot()

    # Get all device IDs to share with
    device_ids = get_devices_from_xml(config_path)

    if dry_run:
        print(f"  Would add folder '{FOLDER_ID}' to {config_path}")
        print(f"  Path: {folder_path}")
        print(f"  Shared with {len(device_ids)} device(s)")
        return True

    # Build folder element
    folder = ET.SubElement(root, "folder", {
        "id": FOLDER_ID,
        "label": FOLDER_LABEL,
        "path": folder_path,
        "type": "sendreceive",
        "rescanIntervalS": "60",
        "fsWatcherEnabled": "true",
        "fsWatcherDelayS": "5",
        "ignorePerms": "true",
        "autoNormalize": "true",
    })

    for did in device_ids:
        ET.SubElement(folder, "device", {"id": did, "introducedBy": ""})

    ET.SubElement(folder, "minDiskFree", {"unit": "%"}).text = "1"

    tree.write(config_path, encoding="unicode", xml_declaration=True)
    return True


def main():
    parser = argparse.ArgumentParser(description="Set up Syncthing for LLM Memory sync")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    args = parser.parse_args()

    print("=== LLM Memory — Syncthing Setup ===\n")

    # Step 1: Find config
    config_path = find_syncthing_config()
    if not config_path:
        print("ERROR: Could not find Syncthing config.xml")
        print("Is Syncthing installed? Check: https://syncthing.net/")
        sys.exit(1)
    print(f"Found config: {config_path}")

    # Step 2: Parse API credentials
    try:
        address, api_key = parse_config(config_path)
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    print(f"API: {address}")

    # Step 3: Check if folder already exists (check XML directly — works even if API is unreachable)
    if folder_exists_in_xml(config_path):
        print(f"\nFolder '{FOLDER_ID}' already configured in Syncthing. Nothing to do.")
        sys.exit(0)

    # Step 4: Determine folder path
    folder_path = get_folder_path()
    print(f"Folder path: {folder_path}")

    # Step 5: Ensure .stignore exists
    stignore = MEMORY_DIR / ".stignore"
    if not stignore.exists():
        if not args.dry_run:
            stignore.write_text("memory.db\nmemory.db-wal\nmemory.db-shm\nlib/.venv\n")
        print("Created .stignore")

    # Step 6: Try API first, fall back to direct XML edit
    my_id = get_device_id(address, api_key)
    if my_id:
        # API is reachable — use it
        print(f"Device ID: {my_id[:12]}...")
        devices = get_other_devices(address, api_key, my_id)
        print(f"Sharing with: {len(devices)} device(s)")

        if add_folder(address, api_key, folder_path, devices, dry_run=args.dry_run):
            if args.dry_run:
                print("\n[DRY RUN] No changes made.")
            else:
                print(f"\nFolder '{FOLDER_ID}' added via Syncthing API!")
        else:
            print("API call failed, falling back to config.xml edit...")
            if add_folder_to_xml(config_path, folder_path, dry_run=args.dry_run):
                print(f"\nFolder '{FOLDER_ID}' added to config.xml!")
                print("Restart Syncthing to pick up the change.")
    else:
        # API unreachable — edit config.xml directly
        print("Syncthing API not reachable. Editing config.xml directly...")
        device_ids = get_devices_from_xml(config_path)
        print(f"Found {len(device_ids)} device(s) in config")

        if add_folder_to_xml(config_path, folder_path, dry_run=args.dry_run):
            if args.dry_run:
                print("\n[DRY RUN] No changes made.")
            else:
                print(f"\nFolder '{FOLDER_ID}' added to config.xml!")
                print("Restart Syncthing to pick up the change.")

    print("\nOn your other device, accept the folder share in the Syncthing GUI")
    print(f"and point it to ~/.claude/memory/ (or run install.sh + this script).")


if __name__ == "__main__":
    main()
