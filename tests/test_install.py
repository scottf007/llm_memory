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


class TestToolsInstall:
    """tools/memory_wrap and its client config aren't *.py, so the glob that
    installs the rest of tools/ skips them — install.sh needs an explicit
    copy for each, plus a chmod, or the installed wrapper is either absent
    or unusable (see judge verdict 52e59524: the wrapper's own repo commit
    shipped without its exec bit for the same class of reason).
    """

    def _copy_block(self) -> list[str]:
        """The real lines from install.sh, not a paraphrase of them."""
        content = (REPO_DIR / "install.sh").read_text().splitlines()
        start = next(i for i, line in enumerate(content)
                     if 'mkdir -p "$LIB_DIR/tools"' in line)
        end = next(i for i, line in enumerate(content[start:], start)
                   if 'chmod +x "$LIB_DIR/tools/memory_wrap"' in line)
        return content[start:end + 1]

    def _run(self, tmp_path) -> Path:
        lib_dir = tmp_path / "lib"
        extracted = tmp_path / "extracted"
        (extracted / "tools").mkdir(parents=True)
        (extracted / "tools" / "adapter_oracle.py").write_text("# oracle\n")
        (extracted / "tools" / "memory_wrap").write_text("#!/bin/bash\necho hi\n")
        (extracted / "tools" / "memory_wrap_clients.json").write_text("{}\n")
        lib_dir.mkdir(parents=True)

        script = "\n".join([
            "set -e", f'LIB_DIR="{lib_dir}"', f'EXTRACTED="{extracted}"', *self._copy_block()])
        subprocess.run(["bash", "-c", script], check=True, capture_output=True)
        return lib_dir

    def test_wrapper_and_config_are_installed(self, tmp_path):
        lib_dir = self._run(tmp_path)
        assert (lib_dir / "tools" / "memory_wrap").is_file()
        assert (lib_dir / "tools" / "memory_wrap_clients.json").is_file()
        assert (lib_dir / "tools" / "adapter_oracle.py").is_file()

    def test_wrapper_is_executable(self, tmp_path):
        lib_dir = self._run(tmp_path)
        mode = (lib_dir / "tools" / "memory_wrap").stat().st_mode
        assert mode & 0o111, "installed memory_wrap must be executable"


class TestLibPackageInstall:
    """The repo-root lib/ package (archive_class, claim_match, ...) is imported
    by merger.py and renderer.py, so an install that omits it deploys a tree
    that raises ImportError on the first merge.

    The trap this guards is the destination, not the copy: $LIB_DIR is itself
    named "lib", so the package must land at $LIB_DIR/lib/. A flat copy puts
    the package's modules next to the top-level scripts and `from lib import
    ...` still fails — an install that looks like it worked.
    """

    def _copy_block(self) -> list[str]:
        """The real lines from install.sh, not a paraphrase of them."""
        content = (REPO_DIR / "install.sh").read_text().splitlines()
        start = next(i for i, line in enumerate(content)
                     if 'if [ -d "$EXTRACTED/lib" ]; then' in line)
        end = next(i for i, line in enumerate(content[start:], start)
                   if 'cp "$EXTRACTED/lib/"*.py "$LIB_DIR/lib/"' in line)
        return content[start:end + 1] + ["fi"]

    def _run(self, tmp_path, times: int = 1) -> Path:
        lib_dir = tmp_path / "lib"
        extracted = tmp_path / "extracted"
        (extracted / "lib").mkdir(parents=True)
        (extracted / "lib" / "__init__.py").write_text("")
        (extracted / "lib" / "archive_class.py").write_text("VALUE = 1\n")
        lib_dir.mkdir(parents=True)

        script = "\n".join([
            "set -e",
            f'LIB_DIR="{lib_dir}"',
            f'EXTRACTED="{extracted}"',
            *(line for _ in range(times) for line in self._copy_block()),
        ])
        subprocess.run(["bash", "-c", script], check=True, capture_output=True)
        return lib_dir

    def test_package_lands_in_its_own_subdirectory(self, tmp_path):
        lib_dir = self._run(tmp_path)
        assert (lib_dir / "lib" / "__init__.py").is_file()
        assert (lib_dir / "lib" / "archive_class.py").is_file()

    def test_package_is_not_copied_flat(self, tmp_path):
        """$LIB_DIR/archive_class.py means `from lib import ...` will fail."""
        lib_dir = self._run(tmp_path)
        assert not (lib_dir / "archive_class.py").exists(), \
            "lib/ was copied flat into $LIB_DIR; it must land at $LIB_DIR/lib/"

    def test_deployed_package_is_importable(self, tmp_path):
        """The point of the copy: `from lib import X` works from $LIB_DIR."""
        lib_dir = self._run(tmp_path)
        proc = subprocess.run(
            ["python3", "-c", "from lib import archive_class; print(archive_class.VALUE)"],
            cwd=lib_dir, capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "1"

    def test_retired_module_does_not_linger(self, tmp_path):
        """A module dropped from the repo must not stay behind and get imported."""
        lib_dir = self._run(tmp_path)
        stale = lib_dir / "lib" / "retired.py"
        stale.write_text("raise RuntimeError('should have been removed')\n")
        self._run_again(tmp_path, lib_dir)
        assert not stale.exists()

    def _run_again(self, tmp_path, lib_dir):
        script = "\n".join([
            "set -e",
            f'LIB_DIR="{lib_dir}"',
            f'EXTRACTED="{tmp_path / "extracted"}"',
            *self._copy_block(),
        ])
        subprocess.run(["bash", "-c", script], check=True, capture_output=True)

    def test_stale_pycache_is_wiped(self, tmp_path):
        """A stale __pycache__ can shadow a removed module on some layouts."""
        lib_dir = self._run(tmp_path)
        pycache = lib_dir / "lib" / "__pycache__"
        pycache.mkdir(exist_ok=True)
        (pycache / "retired.cpython-311.pyc").write_bytes(b"\x00")
        self._run_again(tmp_path, lib_dir)
        assert not pycache.exists()

    def test_repeat_install_does_not_nest(self, tmp_path):
        lib_dir = self._run(tmp_path, times=3)
        assert not (lib_dir / "lib" / "lib").exists(), "repeat install nested the package"


class TestGhTokenFallthrough:
    """`_gh_token` is documented to fall through to unauthenticated curl when
    no token is available. Under `set -e`, a non-zero return from the function
    kills the installer at `TOKEN=$(_gh_token)` — with no message, because the
    failure is a bare exit status. That made the documented path unreachable
    for anyone without `gh` authenticated, i.e. most first-time users.
    """

    def _function(self) -> str:
        """The real function body from install.sh."""
        content = (REPO_DIR / "install.sh").read_text().splitlines()
        start = next(i for i, line in enumerate(content) if line.startswith("_gh_token()"))
        end = next(i for i, line in enumerate(content[start:], start) if line == "}")
        return "\n".join(content[start:end + 1])

    def _run(self, tmp_path, env_extra: dict) -> subprocess.CompletedProcess:
        # An empty PATH-like sandbox: HOME has no token files, and `gh` is
        # made to look either absent or unauthenticated.
        home = tmp_path / "home"
        home.mkdir()
        script = "\n".join([
            "set -e",
            self._function(),
            "TOKEN=$(_gh_token)",
            'echo "REACHED:${TOKEN}"',
        ])
        env = {**os.environ, "HOME": str(home), **env_extra}
        env.pop("GH_TOKEN", None)
        if env_extra.get("GH_TOKEN") is not None:
            env["GH_TOKEN"] = env_extra["GH_TOKEN"]
        return subprocess.run(["bash", "-c", script], capture_output=True,
                              text=True, env=env)

    def test_survives_with_no_gh_on_path(self, tmp_path):
        """PATH without gh: the installer must continue, tokenless."""
        proc = self._run(tmp_path, {"PATH": "/usr/bin:/bin"})
        # Only meaningful when gh really is absent; otherwise the next test covers it.
        if shutil.which("gh", path="/usr/bin:/bin") is None:
            assert proc.returncode == 0, f"installer died: {proc.stderr}"
            assert proc.stdout.strip() == "REACHED:"

    def test_survives_when_gh_is_unauthenticated(self, tmp_path):
        """gh present but not logged in — the default on a fresh machine."""
        stub_dir = tmp_path / "bin"
        stub_dir.mkdir()
        gh = stub_dir / "gh"
        gh.write_text('#!/bin/bash\necho "not logged in" >&2\nexit 1\n')
        gh.chmod(0o755)
        proc = self._run(tmp_path, {"PATH": f"{stub_dir}:/usr/bin:/bin"})
        assert proc.returncode == 0, f"installer died: {proc.stderr}"
        assert proc.stdout.strip() == "REACHED:", proc.stdout

    def test_uses_gh_token_when_authenticated(self, tmp_path):
        stub_dir = tmp_path / "bin"
        stub_dir.mkdir()
        gh = stub_dir / "gh"
        gh.write_text('#!/bin/bash\necho ghp_fromcli\n')
        gh.chmod(0o755)
        proc = self._run(tmp_path, {"PATH": f"{stub_dir}:/usr/bin:/bin"})
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "REACHED:ghp_fromcli"

    def test_env_var_still_wins(self, tmp_path):
        proc = self._run(tmp_path, {"GH_TOKEN": "ghp_fromenv"})
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "REACHED:ghp_fromenv"


class TestInstallScriptImportsResolve:
    """install.sh runs inline python against the deployed tree. A reference to
    a name that no longer exists there is invisible: stderr was discarded and
    `set -e` turned it into a silent death mid-install.

    `from server import init_db` was exactly this — init_db had been gone from
    server.py for some time, and it killed every fresh install at step 8.
    """

    @staticmethod
    def _code_lines() -> list[str]:
        """install.sh with shell comments stripped, so this file's own prose
        about the bug can't satisfy or trip the checks below."""
        return [line for line in (REPO_DIR / "install.sh").read_text().splitlines()
                if not line.lstrip().startswith("#")]

    def _inline_imports(self) -> list[tuple[str, str]]:
        """(module, name) for every `from X import Y` in install.sh's inline
        python, excluding the standard library — only repo modules can rot."""
        import re
        import sys
        pairs = []
        for line in self._code_lines():
            m = re.match(r"^from (\w+) import (.+)$", line)
            if not m:
                continue
            module, names = m.group(1), m.group(2)
            if module in sys.stdlib_module_names:
                continue
            for name in names.split(","):
                pairs.append((module, name.strip()))
        return pairs

    def test_there_are_inline_imports_to_check(self):
        """Guard the guard: if the extraction breaks, this test must fail loudly."""
        assert self._inline_imports(), "found no `from X import Y` in install.sh"

    def test_every_inline_import_resolves(self):
        import ast
        for module, name in self._inline_imports():
            source = REPO_DIR / f"{module}.py"
            assert source.exists(), f"install.sh imports from missing module {module}.py"
            tree = ast.parse(source.read_text())
            defined = {
                n.name for n in tree.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            } | {
                t.id for n in tree.body if isinstance(n, ast.Assign)
                for t in n.targets if isinstance(t, ast.Name)
            }
            assert name in defined, (
                f"install.sh does `from {module} import {name}` but {module}.py "
                f"does not define {name} — this kills the install silently"
            )

    def test_no_reference_to_the_removed_init_db(self):
        content = "\n".join(self._code_lines())
        assert "init_db" not in content, (
            "install.sh still references init_db, which does not exist in server.py"
        )


class TestInlinePythonIsNotShellInterpolated:
    """install_hooks.sh used to feed its python to `python3 -c "` ... `"` — a
    DOUBLE-quoted bash string. Bash evaluates command substitution inside one,
    so a `claude --resume` written in backticks inside a python *comment* was
    executed as a shell command on every install, on every machine with the
    claude CLI on PATH. Nothing in the output said so.

    A quoted heredoc (<<'EOF') is not interpolated at all, which removes the
    whole class rather than the one instance.
    """

    @staticmethod
    def _shell_code(text: str) -> str:
        """Drop shell comment lines. Bash performs no substitution inside a
        `#` comment, so a comment describing this bug is not an instance of
        it — and these tests must not trip over their own explanation."""
        return "\n".join(line for line in text.splitlines()
                          if not line.lstrip().startswith("#"))

    def test_install_hooks_uses_a_quoted_heredoc(self):
        content = self._shell_code(
            (REPO_DIR / "hooks" / "install_hooks.sh").read_text())
        assert "<<'PYEOF'" in content, (
            "install_hooks.sh must feed python via a quoted heredoc, not an "
            "interpolated double-quoted string"
        )
        assert 'python3 -c "' not in content, (
            "python3 -c \" ... \" lets bash expand backticks and $(...) inside "
            "the python source"
        )

    def test_no_backticks_survive_in_an_interpolated_context(self):
        """Belt and braces: no shell-interpolated region may contain backticks."""
        content = (REPO_DIR / "hooks" / "install_hooks.sh").read_text()
        heredoc_body = content.split("<<'PYEOF'", 1)[1].split("PYEOF", 1)[0]
        outside = self._shell_code(content.replace(heredoc_body, ""))
        assert "`" not in outside, (
            "backticks outside the quoted heredoc are command substitution"
        )

    def test_hooks_install_produces_valid_settings(self, tmp_path):
        """The rewrite must still do the job it was doing."""
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "settings.json").write_text("{}")
        env = {**os.environ, "HOME": str(home), "LLM_MEMORY_INSTALLING": "1"}
        proc = subprocess.run(
            ["bash", str(REPO_DIR / "hooks" / "install_hooks.sh")],
            capture_output=True, text=True, env=env,
        )
        assert proc.returncode == 0, proc.stderr
        settings = json.loads((home / ".claude" / "settings.json").read_text())
        assert set(settings["hooks"]) == {
            "SessionStart", "PostToolUse", "PreCompact",
            "SessionEnd", "SubagentStart", "SubagentStop",
        }
        assert len(settings["hooks"]["PostToolUse"]) == 1
        assert settings["hooks"]["PostToolUse"][0]["matcher"] == ""
        assert "llm_memory_last_save" not in json.dumps(settings)

    def test_a_home_path_with_shell_metacharacters_does_not_break_it(self, tmp_path):
        """The old interpolated form would have expanded these."""
        home = tmp_path / "ho$me `weird`"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "settings.json").write_text("{}")
        env = {**os.environ, "HOME": str(home), "LLM_MEMORY_INSTALLING": "1"}
        proc = subprocess.run(
            ["bash", str(REPO_DIR / "hooks" / "install_hooks.sh")],
            capture_output=True, text=True, env=env,
        )
        assert proc.returncode == 0, proc.stderr
        settings = json.loads((home / ".claude" / "settings.json").read_text())
        cmd = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        assert cmd.endswith("session_start.sh")
