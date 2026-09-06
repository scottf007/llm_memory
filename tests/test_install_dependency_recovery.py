"""Hermetic controls for install.sh's apt dependency recovery.

The dependency block is executed verbatim with tiny command shims.  An
``id`` shim is the smallest honest way to select the installer branch: the
production decision is based on ``id -u`` and no package installation is
allowed during a unit test.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _dependency_block() -> str:
    source = (REPO_ROOT / "install.sh").read_text()
    start = source.index("# --- Step 1: Check system dependencies ---")
    end = source.index("# --- Step 2: Download latest from GitHub ---")
    return source[start:end]


def _write_shim(path: Path, name: str, body: str) -> None:
    target = path / name
    target.write_text("#!/bin/sh\n" + body + "\n")
    target.chmod(0o755)


def _run_dependency_check(tmp_path: Path, *, uid: int, has_sudo: bool) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    _write_shim(bin_dir, "id", f"echo {uid}")
    _write_shim(bin_dir, "apt-get", f'printf "apt:%s:%s\\n" "$DEBIAN_FRONTEND" "$*" >> "{log}"')
    if has_sudo:
        _write_shim(bin_dir, "sudo", f'printf "sudo:%s\\n" "$*" >> "{log}"')
    for command in ("sqlite3", "python3", "curl"):
        _write_shim(bin_dir, command, "exit 0")

    script = "set -e\nlog() { :; }\n" + _dependency_block()
    env = dict(os.environ, PATH=str(bin_dir))
    result = subprocess.run(
        ["/bin/bash", "-c", script],
        text=True,
        capture_output=True,
        env=env,
        timeout=15,
    )
    result.call_log = log.read_text() if log.exists() else ""  # type: ignore[attr-defined]
    return result


def test_root_uses_apt_get_directly_without_sudo(tmp_path: Path) -> None:
    result = _run_dependency_check(tmp_path, uid=0, has_sudo=False)

    assert result.returncode == 0, result.stderr
    assert result.call_log == "apt:noninteractive:install -y jq\n"  # type: ignore[attr-defined]


def test_non_root_uses_sudo_when_available(tmp_path: Path) -> None:
    result = _run_dependency_check(tmp_path, uid=1000, has_sudo=True)

    assert result.returncode == 0, result.stderr
    assert result.call_log == "sudo:DEBIAN_FRONTEND=noninteractive apt-get install -y jq\n"  # type: ignore[attr-defined]


def test_non_root_without_sudo_names_exact_manual_apt_command(tmp_path: Path) -> None:
    result = _run_dependency_check(tmp_path, uid=1000, has_sudo=False)

    assert result.returncode != 0
    assert "ERROR: Cannot auto-install without root or sudo." in result.stdout
    assert "Run: apt-get install -y jq" in result.stdout
