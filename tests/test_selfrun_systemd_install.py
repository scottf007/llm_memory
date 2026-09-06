"""Frozen tests — self-running extraction, area 2: systemd unit install
(design doc D1, D4; docs/design/self-running-extraction-2026-09-06.md).

`hooks/install_systemd_units.sh` (new; Appendix) must write
`llm-memory-extract.service` and `.timer` under
`$XDG_CONFIG_HOME/systemd/user/`, call the (faked) `systemctl --user
daemon-reload` and `enable --now llm-memory-extract.timer`, and be
idempotent. With no `systemctl` on PATH it must fall back to printing the
manual one-liners and installing nothing, exit 0.

RED on base c0570b2: hooks/install_systemd_units.sh does not exist —
subprocess fails with "No such file or directory" for every test below.
"""

import os
from pathlib import Path

import pytest

from tests.fixtures.selfrun import helpers as H


def _run_installer(tmp_path, *, with_systemctl: bool, systemctl_log: Path):
    xdg = tmp_path / "xdg-config"
    xdg.mkdir(exist_ok=True)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(xdg)
    if with_systemctl:
        env["PATH"] = str(H.FIXTURES_DIR) + os.pathsep + env.get("PATH", "/usr/bin:/bin")
        env["FAKE_SYSTEMCTL_LOG"] = str(systemctl_log)
    else:
        # A minimal PATH with no systemctl anywhere on it and no fixture dir.
        env["PATH"] = "/usr/bin:/bin"
        env.pop("FAKE_SYSTEMCTL_LOG", None)
    import subprocess
    result = subprocess.run(
        ["bash", str(H.HOOKS_DIR / "install_systemd_units.sh")],
        capture_output=True, text=True, env=env, timeout=30,
    )
    return result, xdg


def test_installs_service_and_timer_and_enables_via_systemctl(tmp_path):
    systemctl_log = tmp_path / "systemctl.log"
    result, xdg = _run_installer(tmp_path, with_systemctl=True, systemctl_log=systemctl_log)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    unit_dir = xdg / "systemd" / "user"
    service = unit_dir / "llm-memory-extract.service"
    timer = unit_dir / "llm-memory-extract.timer"
    assert service.is_file(), f"missing {service}"
    assert timer.is_file(), f"missing {timer}"

    service_text = service.read_text()
    timer_text = timer.read_text()
    assert "OnFailure=" in service_text, "service must wire an OnFailure= helper (D3)"
    assert "Persistent=true" in timer_text
    assert "OnUnitActiveSec=5min" in timer_text or "OnUnitActiveSec=300" in timer_text, (
        f"timer must recover a missed hook every five minutes, got:\n{timer_text}"
    )

    log_text = systemctl_log.read_text() if systemctl_log.exists() else ""
    assert "--user" in log_text and "daemon-reload" in log_text
    assert "enable" in log_text and "--now" in log_text and "llm-memory-extract.timer" in log_text


def test_second_run_is_idempotent_byte_identical_no_duplicate_enable(tmp_path):
    systemctl_log = tmp_path / "systemctl.log"
    result1, xdg = _run_installer(tmp_path, with_systemctl=True, systemctl_log=systemctl_log)
    assert result1.returncode == 0

    unit_dir = xdg / "systemd" / "user"
    service = unit_dir / "llm-memory-extract.service"
    timer = unit_dir / "llm-memory-extract.timer"
    sha_before = (H.sha256_file(service), H.sha256_file(timer))

    result2, _ = _run_installer(tmp_path, with_systemctl=True, systemctl_log=systemctl_log)
    assert result2.returncode == 0

    sha_after = (H.sha256_file(service), H.sha256_file(timer))
    assert sha_before == sha_after, "re-running the installer must not rewrite the unit files"

    log_text = systemctl_log.read_text()
    enable_calls = log_text.count("enable --now llm-memory-extract.timer")
    assert enable_calls == 1, (
        f"expected exactly one `enable --now` across both runs (idempotent), "
        f"got {enable_calls} in:\n{log_text}"
    )


def test_control_no_systemctl_on_path_prints_manual_instructions_and_installs_nothing(tmp_path):
    result, xdg = _run_installer(tmp_path, with_systemctl=False, systemctl_log=tmp_path / "unused.log")
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "systemctl" in result.stdout.lower() or "systemctl" in result.stderr.lower(), (
        "must print the documented manual one-liners when systemctl is unavailable"
    )
    unit_dir = xdg / "systemd" / "user"
    assert not (unit_dir / "llm-memory-extract.service").exists()
    assert not (unit_dir / "llm-memory-extract.timer").exists()
