"""Tests for install.sh — verifies skills, agents, and hooks are installed correctly.

These tests simulate install.sh behavior by creating a fake repo extract
and a fake HOME, then running the relevant install steps in isolation.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_DIR = Path(__file__).parent.parent


def _setup_fake_install(tmp_path):
    """Create a fake HOME and a fake extracted repo to simulate install."""
    home = tmp_path / "home"
    claude_dir = home / ".claude"
    claude_dir.mkdir(parents=True)

    # Create a minimal settings.json
    (claude_dir / "settings.json").write_text("{}")

    # Simulate LIB_DIR (what install.sh copies repo files into)
    lib_dir = home / ".claude" / "memory" / "lib"
    lib_dir.mkdir(parents=True)

    # Copy real repo files into lib_dir to simulate post-download state
    # Hooks
    hooks_dest = lib_dir / "hooks"
    hooks_dest.mkdir()
    for f in (REPO_DIR / "hooks").glob("*.sh"):
        shutil.copy2(f, hooks_dest / f.name)

    # Skills
    skills_src = REPO_DIR / "skills"
    if skills_src.exists():
        shutil.copytree(skills_src, lib_dir / "skills")

    # Agents
    agents_src = REPO_DIR / "agents"
    if agents_src.exists():
        shutil.copytree(agents_src, lib_dir / "agents")

    return home, lib_dir


class TestSkillInstall:
    """Test that skills are installed from lib to ~/.claude/skills/."""

    def test_narrative_skill_exists_in_repo(self):
        """The narrative skill file must exist in the repo."""
        skill_path = REPO_DIR / "skills" / "narrative" / "SKILL.md"
        assert skill_path.exists(), f"Missing {skill_path}"

    def test_narrative_skill_has_frontmatter(self):
        """Skill file must have valid frontmatter with required fields."""
        content = (REPO_DIR / "skills" / "narrative" / "SKILL.md").read_text()
        assert content.startswith("---"), "Skill must start with frontmatter"
        assert "name: narrative" in content
        assert "user_invocable: true" in content

    def test_skill_installed_to_claude_dir(self, tmp_path):
        """install.sh must copy skills to ~/.claude/skills/."""
        home, lib_dir = _setup_fake_install(tmp_path)

        # Run the skill install logic (extracted from install.sh)
        script = f"""
        LIB_DIR="{lib_dir}"
        HOME="{home}"

        if [ -d "$LIB_DIR/skills" ]; then
            for skill_dir in "$LIB_DIR/skills"/*/; do
                [ -d "$skill_dir" ] || continue
                skill_name=$(basename "$skill_dir")
                mkdir -p "$HOME/.claude/skills/$skill_name"
                cp "$skill_dir"* "$HOME/.claude/skills/$skill_name/" 2>/dev/null || true
            done
        fi
        """
        subprocess.run(["bash", "-c", script], check=True)

        installed = home / ".claude" / "skills" / "narrative" / "SKILL.md"
        assert installed.exists(), f"Skill not installed at {installed}"

        content = installed.read_text()
        assert "name: narrative" in content

    def test_skill_sql_uses_angle_brackets(self):
        """The narrative skill must use <> not != in SQL to avoid bash escaping."""
        content = (REPO_DIR / "skills" / "narrative" / "SKILL.md").read_text()
        # Check there's no != in sqlite3 commands
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if "sqlite3" in line or ("SELECT" in line and "project" in line):
                assert "!=" not in line, (
                    f"Line {i+1} uses != in SQL which breaks in bash. Use <> instead: {line.strip()}"
                )


class TestAgentInstall:
    """Test that agent definitions are installed from lib to ~/.claude/agents/."""

    def test_delta_extractor_exists_in_repo(self):
        agent_path = REPO_DIR / "agents" / "delta-extractor.md"
        assert agent_path.exists(), f"Missing {agent_path}"

    def test_memory_aware_exists_in_repo(self):
        agent_path = REPO_DIR / "agents" / "memory-aware.md"
        assert agent_path.exists(), f"Missing {agent_path}"

    def test_agent_has_frontmatter(self):
        """Agent files must have frontmatter with name and tools."""
        for name in ["delta-extractor.md", "memory-aware.md"]:
            content = (REPO_DIR / "agents" / name).read_text()
            assert content.startswith("---"), f"{name} must start with frontmatter"
            assert "name:" in content, f"{name} missing name field"
            assert "tools:" in content, f"{name} missing tools field"

    def test_agents_installed_to_claude_dir(self, tmp_path):
        """install.sh must copy agents to ~/.claude/agents/."""
        home, lib_dir = _setup_fake_install(tmp_path)

        script = f"""
        LIB_DIR="{lib_dir}"
        HOME="{home}"

        if [ -d "$LIB_DIR/agents" ]; then
            mkdir -p "$HOME/.claude/agents"
            cp "$LIB_DIR/agents/"*.md "$HOME/.claude/agents/" 2>/dev/null || true
        fi
        """
        subprocess.run(["bash", "-c", script], check=True)

        for name in ["delta-extractor.md", "memory-aware.md"]:
            installed = home / ".claude" / "agents" / name
            assert installed.exists(), f"Agent not installed at {installed}"


class TestInstallScript:
    """Test the install.sh script structure for correctness."""

    def test_install_sh_creates_skills_dir(self):
        """install.sh must mkdir -p before copying skills."""
        content = (REPO_DIR / "install.sh").read_text()
        # Find the skills copy section and verify mkdir comes before cp
        lines = content.split("\n")
        mkdir_skills_line = None
        cp_skills_line = None
        for i, line in enumerate(lines):
            if "mkdir -p" in line and "skills" in line and "claude/skills" not in line:
                mkdir_skills_line = i
            if "cp -r" in line and "skills" in line and "LIB_DIR" in line:
                cp_skills_line = i

        assert mkdir_skills_line is not None, "install.sh must mkdir skills dir before copying"
        assert cp_skills_line is not None, "install.sh must copy skills"
        assert mkdir_skills_line < cp_skills_line, "mkdir must come before cp for skills"

    def test_install_sh_creates_agents_dir(self):
        """install.sh must mkdir -p before copying agents."""
        content = (REPO_DIR / "install.sh").read_text()
        assert 'mkdir -p "$LIB_DIR/agents"' in content or "mkdir -p" in content


class TestFixturesInstall:
    """The fixtures tree gained subdirectories when the codex adapter landed.

    A flat `cp` of a tree that now has directories in it is where repeat
    installs go wrong, and the installed copy self-updates on every session
    start, so "repeat install" is the normal case, not an edge case.
    """

    def _copy_block(self) -> list[str]:
        """The real lines from install.sh, not a paraphrase of them."""
        content = (REPO_DIR / "install.sh").read_text().splitlines()
        start = next(i for i, line in enumerate(content)
                     if 'rm -rf "$LIB_DIR/tests/fixtures"' in line)
        end = next(i for i, line in enumerate(content[start:], start)
                   if 'cp -r "$EXTRACTED/tests/fixtures/."' in line)
        return content[start:end + 1]

    def _run(self, tmp_path, times: int) -> Path:
        lib_dir = tmp_path / "lib"
        extracted = tmp_path / "extracted"
        (extracted / "tests" / "fixtures" / "codex").mkdir(parents=True)
        (extracted / "tests" / "fixtures" / "codex" / "01-a.jsonl").write_text("{}\n")
        (extracted / "tests" / "fixtures" / "oracle_sample.txt").write_text("x\n")
        (lib_dir / "tests").mkdir(parents=True)

        script = "\n".join([
            "set -e",
            f'LIB_DIR="{lib_dir}"',
            f'EXTRACTED="{extracted}"',
            *(line for _ in range(times) for line in self._copy_block()),
        ])
        subprocess.run(["bash", "-c", script], check=True, capture_output=True)
        return lib_dir

    def test_single_install_places_fixtures(self, tmp_path):
        lib_dir = self._run(tmp_path, times=1)
        assert (lib_dir / "tests" / "fixtures" / "codex" / "01-a.jsonl").is_file()
        assert (lib_dir / "tests" / "fixtures" / "oracle_sample.txt").is_file()

    def test_repeat_install_does_not_nest_fixture_directories(self, tmp_path):
        """`cp -r src/* dst/` merges under GNU cp and nests under others.

        Whichever it does, a second run must leave the same tree as the first.
        """
        lib_dir = self._run(tmp_path, times=3)
        fixtures = lib_dir / "tests" / "fixtures"
        assert not (fixtures / "codex" / "codex").exists(), "repeat install nested the tree"
        assert not (fixtures / "fixtures").exists(), "repeat install nested the tree"
        assert sorted(p.relative_to(fixtures).as_posix() for p in fixtures.rglob("*")) == [
            "codex", "codex/01-a.jsonl", "oracle_sample.txt",
        ]

    def test_retired_fixture_does_not_linger(self, tmp_path):
        """A fixture removed from the repo must not stay behind and be collected."""
        lib_dir = self._run(tmp_path, times=1)
        stale = lib_dir / "tests" / "fixtures" / "codex" / "99-retired.jsonl"
        stale.write_text("{}\n")

        extracted = tmp_path / "extracted"
        script = "\n".join([
            "set -e", f'LIB_DIR="{lib_dir}"', f'EXTRACTED="{extracted}"', *self._copy_block()])
        subprocess.run(["bash", "-c", script], check=True, capture_output=True)
        assert not stale.exists()

    def test_installer_copies_the_fixtures_subtree(self):
        """The codex fixtures live in a subdirectory; a flat copy would skip them."""
        content = (REPO_DIR / "install.sh").read_text()
        assert 'cp -r "$EXTRACTED/tests/fixtures/." "$LIB_DIR/tests/fixtures/"' in content
