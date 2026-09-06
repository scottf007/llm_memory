"""Controls for the source archive used by the fresh-install container proof."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_SCRIPT = REPO_ROOT / "tools" / "fresh_install_check.sh"


def _make_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    tools = repo / "tools"
    tools.mkdir(parents=True)
    shutil.copy2(SOURCE_SCRIPT, tools / "fresh_install_check.sh")
    tracked = repo / "tracked.txt"
    tracked.write_text("committed\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    return repo, tracked


def _fake_docker(bin_dir: Path) -> None:
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, tarfile\n"
        "args = sys.argv[1:]\n"
        "volume = next(args[i + 1] for i, arg in enumerate(args) if arg == '-v' and args[i + 1].endswith(':/archive:ro'))\n"
        "host = volume.split(':/archive:ro', 1)[0]\n"
        "with tarfile.open(host + '/llm_memory.tar.gz', 'r:gz') as archive:\n"
        "    member = next(m for m in archive.getmembers() if m.name.endswith('/tracked.txt'))\n"
        "    print('ARCHIVE_TRACKED=' + archive.extractfile(member).read().decode().strip())\n"
    )
    docker.chmod(0o755)


def _run_container_proof(repo: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_docker(bin_dir)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
    return subprocess.run(
        ["bash", str(repo / "tools" / "fresh_install_check.sh")],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )


def test_dirty_tree_proof_warns_and_archives_committed_head(tmp_path: Path) -> None:
    repo, tracked = _make_repo(tmp_path)
    tracked.write_text("dirty\n")

    result = _run_container_proof(repo, tmp_path)

    assert result.returncode == 0, result.stderr
    head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    assert f"NOTE: working tree dirty; proving HEAD {head}, not the working tree" in result.stderr
    assert "ARCHIVE_TRACKED=committed" in result.stdout


def test_clean_tree_proof_archives_head_without_dirty_note(tmp_path: Path) -> None:
    repo, _ = _make_repo(tmp_path)

    result = _run_container_proof(repo, tmp_path)

    assert result.returncode == 0, result.stderr
    assert "NOTE: working tree dirty" not in result.stderr
    assert "ARCHIVE_TRACKED=committed" in result.stdout
