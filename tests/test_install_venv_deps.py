"""B3-N1 / B3-N3 and F-1: installer venv regression guards.

The full ubuntu:24.04 reproductions live in the board produce artifacts,
not in pytest.
"""

import os
import subprocess
from pathlib import Path

REPO_DIR = Path(__file__).parent.parent


def _step1() -> str:
    content = (REPO_DIR / "install.sh").read_text()
    start = content.index("[1/8]")
    end = content.index("[2/8]")
    return content[start:end]


def _step1_code() -> str:
    """Shell comments stripped — a comment explaining the version-specific
    package name is not an instance of installing that package."""
    return "\n".join(
        line for line in _step1().splitlines() if not line.lstrip().startswith("#")
    )


def _step3() -> str:
    content = (REPO_DIR / "install.sh").read_text()
    start = content.index("# --- Step 3:")
    end = content.index("# --- Step 4:")
    return content[start:end]


def _write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)


def _run_step3(
    tmp_path: Path, venv_python_body: str
) -> tuple[subprocess.CompletedProcess[str], Path]:
    venv_dir = tmp_path / "venv"
    bin_dir = venv_dir / "bin"
    bin_dir.mkdir(parents=True)
    _write_executable(bin_dir / "python3", venv_python_body)
    (venv_dir / "poisoned").write_text("present")

    healthy_python = tmp_path / "healthy-python"
    _write_executable(
        healthy_python,
        '[ "$1" = "-m" ] && [ "$2" = "pip" ] || exit 91\nexit 0',
    )
    fake_path = tmp_path / "path"
    fake_path.mkdir()
    _write_executable(
        fake_path / "python3",
        "\n".join(
            [
                '[ "$1" = "-m" ] && [ "$2" = "venv" ] || exit 90',
                "target=$3",
                'mkdir -p "$target/bin"',
                'cp "$HEALTHY_PYTHON" "$target/bin/python3"',
                'chmod +x "$target/bin/python3"',
                'printf \'%s\\n\' "$target" >> "$REBUILD_LOG"',
            ]
        ),
    )

    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    (lib_dir / "requirements.txt").write_text("")
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_path}:{env['PATH']}",
            "VENV_DIR": str(venv_dir),
            "LIB_DIR": str(lib_dir),
            "HEALTHY_PYTHON": str(healthy_python),
            "REBUILD_LOG": str(tmp_path / "rebuild.log"),
        }
    )
    result = subprocess.run(
        ["bash", "-c", "set -e\nlog() { :; }\n" + _step3()],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, venv_dir


def test_apt_dependency_list_includes_python3_venv():
    """Generic python3-venv, not python3.12-venv — tracks the default python3."""
    code = _step1_code()
    assert "python3-venv" in code
    assert "python3.12-venv" not in code


def test_python3_venv_is_not_in_the_command_probe_loop():
    """python3-venv is a package name, not a binary. brew must not see it."""
    step1 = _step1()
    assert "for cmd in jq sqlite3 python3 curl; do" in step1


def test_non_debian_install_lines_do_not_mention_python3_venv():
    step1 = _step1()
    brew = [ln for ln in step1.splitlines() if "brew install" in ln]
    pacman = [ln for ln in step1.splitlines() if "pacman" in ln]
    dnf = [ln for ln in step1.splitlines() if "dnf install" in ln]
    assert brew, "step 1 must still have a brew branch"
    assert pacman, "step 1 must still have a pacman branch"
    assert dnf, "step 1 must still have a dnf branch"
    assert "python3-venv" not in brew[0]
    assert "python3-venv" not in pacman[0]
    assert "python3-venv" not in dnf[0]


def test_apt_get_is_noninteractive():
    """B3-N3 (folded by PM addendum): tzdata Geographic-area prompt."""
    apt = [ln for ln in _step1().splitlines() if "apt-get install" in ln]
    assert apt, "step 1 must apt-get install"
    assert "DEBIAN_FRONTEND=noninteractive" in apt[0]


def test_step3_rebuilds_venv_when_python_exists_but_pip_is_broken(tmp_path):
    result, venv_dir = _run_step3(tmp_path, "exit 1")

    assert result.returncode == 0, result.stderr
    assert not (venv_dir / "poisoned").exists()
    assert (tmp_path / "rebuild.log").read_text().strip() == str(venv_dir)


def test_step3_keeps_venv_when_its_pip_is_healthy(tmp_path):
    result, venv_dir = _run_step3(
        tmp_path,
        '[ "$1" = "-m" ] && [ "$2" = "pip" ] || exit 92\nexit 0',
    )

    assert result.returncode == 0, result.stderr
    assert (venv_dir / "poisoned").exists()
    assert not (tmp_path / "rebuild.log").exists()
