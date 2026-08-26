"""B3-N1 / B3-N3: step [1/8] Debian dependency list.

Regression guard only — not a container test. The full ubuntu:24.04
reproduction lives in the B3-N1 produce artifact, not in pytest.
"""

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


def test_apt_dependency_list_includes_python3_venv():
    """Generic python3-venv, not python3.12-venv — tracks the default python3."""
    code = _step1_code()
    assert "python3-venv" in code
    assert "python3.12-venv" not in code


def test_python3_venv_is_not_in_the_command_probe_loop():
    """python3-venv is a package name, not a binary. brew must not see it."""
    step1 = _step1()
    assert "for cmd in jq sqlite3 python3 curl; do" in step1
    assert "python3-venv" not in "for cmd in jq sqlite3 python3 curl; do"


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
