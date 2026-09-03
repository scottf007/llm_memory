"""Regression coverage for an installed ``install.sh --update`` replacing itself.

The subprocess runs only to the beginning of step 3.  The injected sentinel
keeps this hermetic (no venv, network, MCP, or live HOME work) while proving
that execution safely reached the first post-copy step.
"""

import os
import shutil
import subprocess
import tarfile
from pathlib import Path


REPO_DIR = Path(__file__).parent.parent
INSTALLER = Path(os.environ.get("LLM_MEMORY_INSTALL_UNDER_TEST", REPO_DIR / "install.sh"))
OLD_SHA = "old-sha"
REMOTE_SHA = "new-sha"


def _instrument_step_three(script: str) -> str:
    marker = "# --- Step 3: Python environment ---"
    replacement = "\n".join(
        [
            marker,
            'touch "$MEMORY_DIR/step3-sentinel"',
            'exit "${LLM_MEMORY_TEST_STEP3_EXIT:-0}"',
        ]
    )
    assert marker in script
    return script.replace(marker, replacement, 1)


def _make_tarball(tmp_path: Path, installer: str) -> Path:
    """Build the GitHub-style source archive consumed by the fake curl."""
    extracted = tmp_path / "source" / "owner-llm_memory-deadbeef"
    extracted.mkdir(parents=True)
    # These are every required pre-step-3 copy source.  Keeping the archive
    # deliberately small also makes the test independent of local artefacts
    # such as transcript caches that are not shipped by GitHub tarballs.
    for name in [
        "server.py", "process_transcripts.py", "extract_conversation.py",
        "conversations.py", "indexer.py", "migrate_item_ids.py",
        "resolve_conflicts.py", "merger.py", "renderer.py", "delta_cache.py",
        "dashboard.py", "apply_settings.py", "requirements.txt", "settings.yaml",
        "claude-rules-example.md", "setup_syncthing.py",
    ]:
        shutil.copy2(REPO_DIR / name, extracted / name)
    for directory in ["hooks", "adapters", "lib"]:
        source = REPO_DIR / directory
        if source.exists():
            shutil.copytree(source, extracted / directory)
    (extracted / "install.sh").write_text(installer)
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(extracted, arcname=extracted.name)
    return archive


def _fake_bin(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "curl").write_text(
        "#!/bin/bash\n"
        "case \"$*\" in\n"
        "  */commits/*) printf '{\"sha\": \"%s\"}\\n' \"$FAKE_REMOTE_SHA\" ;;\n"
        "  */tarball/*) if [ \"${FAKE_CURL_FAIL:-}\" = 1 ]; then exit 22; fi; "
        "printf 'tarball\\n' >> \"$FAKE_CURL_LOG\"; cat \"$FAKE_TARBALL\" ;;\n"
        "  *) echo \"unexpected curl: $*\" >&2; exit 9 ;;\n"
        "esac\n"
    )
    (bin_dir / "jq").write_text(
        "#!/bin/bash\ncat >/dev/null\nprintf '%s\\n' \"$FAKE_REMOTE_SHA\"\n"
    )
    (bin_dir / "sqlite3").write_text("#!/bin/bash\nexit 0\n")
    for command in bin_dir.iterdir():
        command.chmod(0o755)
    return bin_dir


def _run_update(tmp_path: Path, remote_installer: str, *, local_sha: str = OLD_SHA,
                step3_exit: int = 0, download_fails: bool = False) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    home = tmp_path / "home"
    lib_dir = home / ".claude" / "memory" / "lib"
    lib_dir.mkdir(parents=True)
    local_installer = _instrument_step_three(INSTALLER.read_text())
    installed = lib_dir / "install.sh"
    installed.write_text(local_installer)
    installed.chmod(0o755)
    (lib_dir / "VERSION").write_text(f"{local_sha}\n")

    archive = _make_tarball(tmp_path, remote_installer)
    fake_bin = _fake_bin(tmp_path)
    curl_log = tmp_path / "curl.log"
    env = {
        **os.environ,
        "HOME": str(home),
        "LLM_MEMORY_HOME": str(home / ".claude" / "memory"),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_REMOTE_SHA": REMOTE_SHA if local_sha != REMOTE_SHA else local_sha,
        "FAKE_TARBALL": str(archive),
        "FAKE_CURL_LOG": str(curl_log),
        "LLM_MEMORY_TEST_STEP3_EXIT": str(step3_exit),
        "FAKE_CURL_FAIL": "1" if download_fails else "",
    }
    result = subprocess.run(
        ["bash", str(installed), "--update"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    return result, lib_dir, curl_log


def _shifted_remote_installer() -> str:
    """A valid remote installer whose early extra block shifts byte offsets."""
    original = _instrument_step_three(INSTALLER.read_text())
    return original.replace(
        "QUIET=false\n",
        "QUIET=false\nif true; then\n    : remote-offset-shift\nfi\n",
        1,
    )


def test_update_reexecs_before_replacing_an_offset_shifted_installer(tmp_path):
    result, lib_dir, curl_log = _run_update(tmp_path, _shifted_remote_installer())

    assert result.returncode == 0, result.stderr
    assert (lib_dir.parent / "step3-sentinel").is_file(), result.stdout + result.stderr
    assert "syntax error" not in result.stderr
    assert curl_log.read_text() == "tarball\n"


def test_identical_remote_installer_update_still_reaches_step_three(tmp_path):
    identical = _instrument_step_three(INSTALLER.read_text())
    result, lib_dir, curl_log = _run_update(tmp_path, identical)

    assert result.returncode == 0, result.stderr
    assert (lib_dir.parent / "step3-sentinel").is_file()
    assert curl_log.read_text() == "tarball\n"


def test_matching_sha_is_a_no_copy_update(tmp_path):
    result, lib_dir, curl_log = _run_update(
        tmp_path, _shifted_remote_installer(), local_sha=REMOTE_SHA
    )

    assert result.returncode == 0, result.stderr
    assert (lib_dir.parent / "step3-sentinel").is_file()
    assert not curl_log.exists(), "matching SHA must not fetch the tarball"


def test_failed_post_copy_step_keeps_the_previous_version(tmp_path):
    result, lib_dir, _ = _run_update(
        tmp_path, _shifted_remote_installer(), step3_exit=42
    )

    assert result.returncode == 42
    assert (lib_dir / "VERSION").read_text() == f"{OLD_SHA}\n"


def test_failed_download_is_loud_and_keeps_the_previous_version(tmp_path):
    result, lib_dir, _ = _run_update(
        tmp_path, _shifted_remote_installer(), download_fails=True
    )

    assert result.returncode != 0
    assert "ERROR: Download or extraction failed; installation was not marked current." in result.stderr
    assert (lib_dir / "VERSION").read_text() == f"{OLD_SHA}\n"
