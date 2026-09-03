r"""Frozen tests for the codex client-memory arc (rigor c).

Consumes docs/design/codex-client-memory-2026-09-03.md (D1-D6). Written by the
test-author seat; a codex seat implements against this file unmodified; an
opus seat judges by execution. Every test is hermetic: fake $HOME, a fake
`codex` executable on PATH (never the real one), no reads/writes under the
real ~/.codex or ~/.claude/memory, sandboxed LLM_MEMORY_HOME.

This file IS the contract for six new surfaces the implementer must add.
Where the design note names an entry point, that name is used; where it
leaves the entry point to the test author ("Drive the step through whatever
entry point the implementer exposes ... pin the contract, the implementer
conforms"), the contract is pinned here, precisely:

T1 (D1) -- hooks/install_codex_mcp.sh <python3-path> <server.py-path>
    - No `codex` resolvable on PATH: print exactly one non-empty stdout line
      matching /codex.*not found/i; exit 0; invoke nothing.
    - `codex` present: run `codex mcp get llm_memory` first.
        * exit 0 ("already configured"): print a line matching
          /already configured/i; do NOT invoke `codex mcp add`; exit 0.
        * nonzero: run `codex mcp add llm_memory -- <python3-path>
          <server.py-path>` (argv exactly ["mcp", "add", "llm_memory", "--",
          <python3-path>, <server.py-path>]); print a line matching
          /registered/i on success. Exit 0 either way -- MCP wiring must
          never fail the installer (matches the existing Claude branch in
          install.sh, which is not fatal on failure either).
    install.sh's own step 5 is expected to call this script with its own
    $VENV_DIR/bin/python3 and $LIB_DIR/server.py; that wiring is not itself
    under test here (no network, no real install.sh run).

T2 (D4) -- hooks/install_hooks.sh gains a codex branch: it now also merges
    $HOME/.codex/hooks.json, alongside its existing $HOME/.claude/settings.json
    behaviour. Shape (top-level keyed by event name -- "codex's hook protocol
    is the Claude Code protocol", design note SS1):
        {
          "SessionStart": [{"matcher": "startup|resume|compact",
                             "hooks": [{"type": "command",
                                        "command": "<hooks_dir>/codex_session_start.sh",
                                        "timeout": 15}]}],
          "SessionEnd":   [{"matcher": "",
                             "hooks": [{"type": "command",
                                        "command": "<hooks_dir>/codex_session_end.sh",
                                        "timeout": 30}]}]
        }
    <hooks_dir> is the same directory install_hooks.sh already derives for
    itself (BASH_SOURCE parent), exactly like the Claude branch's own
    f'{hooks_dir}/session_start.sh'. An entry under any event array is
    llm_memory-owned iff any of its hooks[].command contains
    "/codex_session_start.sh" or "/codex_session_end.sh" -- the same
    owned-handler test the Claude branch already applies via
    '/memory/lib/hooks/' in command. Non-owned entries must survive
    byte-for-byte; owned entries are removed then re-added, so a second run
    is idempotent. $HOME/.codex/ must be created if absent.

T3 (D2) -- process_transcripts.py gains --session <sid> (repeatable: no --
    combined with exactly one --client <name>). <sid> may be the client's raw
    id or the already-prefixed id; normalise via
    `getattr(adapters.get(client), "session_id_for", lambda s: s)(sid)` (codex
    and grok both already expose session_id_for, and it is documented
    idempotent). Only the ref whose (normalised) session_id matches is
    processed -- discovery still runs, everything else is skipped outright.
    If nothing matches, or the one matched ref raises during processing, exit
    non-zero with a message naming the requested sid -- printed even under
    --quiet, unlike the existing best-effort per-ref WARN path used for the
    unfiltered sweep (which stays non-fatal). Without --session, behaviour is
    unchanged.

T4 -- hooks/codex_session_end.sh: stdin JSON {"session_id", "cwd",
    "hook_event_name"}; resolves $LLM_MEMORY_HOME and its own lib dir exactly
    as hooks/session_end.sh does (SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)",
    PYTHON3="$SCRIPT_DIR/.venv/bin/python3" falling back to `python3`); runs
    `"$PYTHON3" "$SCRIPT_DIR/process_transcripts.py" --client codex --session
    "$SESSION_ID" --quiet` (the raw stdin session_id, unprefixed -- T3's
    normalisation handles the rest). Prints nothing and exits 0 on success.
    On failure (T3's non-zero exit), prints exactly one line to stdout
    starting with "LLM_MEMORY_WARN:" and still exits 0 -- a hook must never
    break the client (T-F6).

T5 (D3, injection half only) -- hooks/codex_session_start.sh: stdin JSON with
    "source" ("startup"/"resume"/"clear"/"compact"), "cwd", "session_id".
    Derives PROJECT from cwd with the identical one-line sed
    session_start.sh already uses (`.*/projects/\([^/]*\).*`). For
    source in {startup, resume} with a derivable project: if
    $LLM_MEMORY_HOME/projects/<project>.narrative.md exists, print its exact
    content to stdout; if the project additionally has unprocessed sessions
    (conversations/*.md frontmatter session_ids for that project, minus the
    ones already merged into <project>.json's sessions[]), also print a line
    containing the literal string "AUTOMATIC TASK". Unlike
    hooks/session_start.sh, this hook must NOT gate any of the above on
    memory.db existing -- that check is Claude-specific residue and is not
    part of this contract. source in {compact, clear}, or no derivable
    project: print nothing; exit 0 in every case.

T6 -- tools/codex_injection_probe.sh --dry-run: never invokes a real `codex`
    (assert this by leaving nothing named `codex` resolvable on PATH). Prints
    exactly 4 non-blank lines:
      1. the literal header "hook\trules-line\twrapper" (tab-separated)
      2. the exact `codex exec ...` command line for the "hook" subject,
         containing "--dangerously-bypass-hook-trust"
      3. the exact `codex exec ...` command line for "rules-line", without
         that flag
      4. the exact `codex exec ...` command line for "wrapper", without that
         flag
    Exit 0. (The real run against the live client is the subject seat's job,
    not this file's.)

RED proof (main d6e5d8d, no codex client-memory code yet): every T1/T4/T5/T6
subprocess call targets a script that does not exist on main, so `bash
<missing path> ...` fails with "No such file or directory" (exit 127) before
any of this file's own assertions even run -- RED for exactly the missing-
script reason. T2's two new-behaviour tests fail because
$HOME/.codex/hooks.json is never written by install_hooks.sh today (RED:
FileNotFoundError reading it back / missing branch). T3's --session tests
fail because --session is not a recognised process_transcripts.py flag today
-- argparse itself exits non-zero with "unrecognized arguments: --session
..." (RED: missing flag), which is why the unknown-session test's *specific*
wording assertion ("unknown"/"no matching session") is also checked, not just
the exit code: argparse's own error text would otherwise make a bare
`returncode != 0` check pass for the wrong reason. T3's no-`--session`
control test is NOT expected to be red -- `--client codex` already exists and
already writes all three conversations today; it is included as the
brief's required non-trigger control, not a new-feature trigger.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from adapters import codex as codex_adapter

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = REPO_ROOT / "hooks"
TOOLS_DIR = REPO_ROOT / "tools"
FIXTURES_CODEX = Path(__file__).parent / "fixtures" / "codex"

_bash = shutil.which("bash")
assert _bash, "bash must be on PATH to run these tests"
BASH: str = _bash


def _venv_python() -> str:
    candidate = REPO_ROOT / ".venv" / "bin" / "python3"
    return str(candidate) if candidate.exists() else sys.executable


def _inherited_path_with_interpreter_first() -> str:
    """PATH for hook scripts that need real jq/python -- the interpreter
    running this test suite goes first (mirrors tests/test_hooks.py's
    _run_hook), everything else (jq, bash) still resolves from the
    inherited PATH."""
    return str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin")


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _write_fake_codex(bin_dir: Path) -> Path:
    """A python-scripted fake `codex` on PATH.

    Records every invocation's argv (sys.argv[1:]) as one JSON array per
    line, to the file named by $FAKE_CODEX_LOG (no-op if unset). Exit code
    for `mcp get llm_memory` is controlled by $FAKE_CODEX_MCP_GET_EXIT
    (default "1" -- "not configured"); every other invocation (`mcp add
    ...`) exits per $FAKE_CODEX_MCP_ADD_EXIT (default "0").
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    fake = bin_dir / "codex"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "log = os.environ.get('FAKE_CODEX_LOG')\n"
        "if log:\n"
        "    with open(log, 'a') as f:\n"
        "        f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "args = sys.argv[1:]\n"
        "if len(args) >= 3 and args[0] == 'mcp' and args[1] == 'get' and args[2] == 'llm_memory':\n"
        "    sys.exit(int(os.environ.get('FAKE_CODEX_MCP_GET_EXIT', '1')))\n"
        "sys.exit(int(os.environ.get('FAKE_CODEX_MCP_ADD_EXIT', '0')))\n"
    )
    mode = fake.stat().st_mode
    fake.chmod(mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return fake


def _read_argv_log(log_path: Path) -> list[list[str]]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


def _pick_non_subagent_fixtures(n: int) -> list[Path]:
    """First n real codex fixtures (sorted) whose parse is not a subagent
    stub -- same technique test_process_transcripts_sweep.py's
    test_skip_applies_to_codex_too already uses, so this stays correct if the
    fixture set changes."""
    candidates = sorted(FIXTURES_CODEX.glob("*.jsonl"))
    candidates = [p for p in candidates if ".expected" not in p.name]
    picked: list[Path] = []
    for p in candidates:
        ref = codex_adapter.ref_for_path(p, session_id=f"codex-pick-{p.stem}")
        meta, _turns = codex_adapter.parse(ref)
        if not meta.is_subagent:
            picked.append(p)
        if len(picked) == n:
            break
    assert len(picked) == n, f"need {n} non-subagent codex fixtures, found {len(picked)}"
    return picked


def _install_codex_sessions(home: Path, fixture_paths: list[Path]) -> list[str]:
    """Copy fixtures into <home>/.codex/sessions/... as valid rollout files.

    Returns the resulting (already-prefixed) session_ids, same order as
    fixture_paths.
    """
    sessions_dir = home / ".codex" / "sessions" / "2026" / "01" / "01"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    session_ids = []
    for i, src in enumerate(fixture_paths):
        u = str(uuid.uuid4())
        dest = sessions_dir / f"rollout-2026-01-01T00-00-{i:02d}-{u}.jsonl"
        dest.write_text(src.read_text())
        session_ids.append(f"codex-{u}")
    return session_ids


# ---------------------------------------------------------------------------
# T1 (D1) -- hooks/install_codex_mcp.sh <python3-path> <server.py-path>
# ---------------------------------------------------------------------------

class TestT1InstallCodexMcp:
    SCRIPT = HOOKS_DIR / "install_codex_mcp.sh"

    def _run(self, tmp_path, *, path, extra_env=None):
        py = "/fake/venv/bin/python3"
        server = "/fake/lib/server.py"
        log = tmp_path / "codex-argv.jsonl"
        env = os.environ.copy()
        env["PATH"] = path
        env["FAKE_CODEX_LOG"] = str(log)
        if extra_env:
            env.update(extra_env)
        result = subprocess.run(
            [BASH, str(self.SCRIPT), py, server],
            capture_output=True, text=True, env=env, timeout=30,
        )
        return result, log, py, server

    def test_mcp_add_invoked_when_absent(self, tmp_path):
        fake_bin = tmp_path / "bin"
        _write_fake_codex(fake_bin)
        result, log, py, server = self._run(
            tmp_path, path=str(fake_bin),
            extra_env={"FAKE_CODEX_MCP_GET_EXIT": "1"},
        )
        assert result.returncode == 0, result.stderr
        calls = _read_argv_log(log)
        assert calls == [
            ["mcp", "get", "llm_memory"],
            ["mcp", "add", "llm_memory", "--", py, server],
        ], calls
        assert any(re.search(r"registered", line, re.I) for line in result.stdout.splitlines()), result.stdout

    def test_control_no_add_when_already_configured(self, tmp_path):
        fake_bin = tmp_path / "bin"
        _write_fake_codex(fake_bin)
        result, log, py, server = self._run(
            tmp_path, path=str(fake_bin),
            extra_env={"FAKE_CODEX_MCP_GET_EXIT": "0"},
        )
        assert result.returncode == 0, result.stderr
        calls = _read_argv_log(log)
        assert calls == [["mcp", "get", "llm_memory"]], calls
        assert any(re.search(r"already configured", line, re.I) for line in result.stdout.splitlines()), result.stdout

    def test_control_no_codex_on_path(self, tmp_path):
        empty_bin = tmp_path / "empty"
        empty_bin.mkdir()
        result, log, _py, _server = self._run(tmp_path, path=str(empty_bin))
        assert result.returncode == 0, result.stderr
        assert not log.exists(), "no codex on PATH must mean zero invocations"
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        assert len(lines) == 1, lines
        assert re.search(r"codex.*not found", lines[0], re.I), lines


# ---------------------------------------------------------------------------
# T2 (D4) -- hooks/install_hooks.sh gains a $HOME/.codex/hooks.json merge
# ---------------------------------------------------------------------------

class TestT2CodexHooksJsonMerge:
    def _codex_hooks_path(self, home: Path) -> Path:
        return home / ".codex" / "hooks.json"

    def _home(self, tmp_path) -> Path:
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        return home

    def _run(self, home: Path):
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["LLM_MEMORY_INSTALLING"] = "1"
        return subprocess.run(
            [BASH, str(HOOKS_DIR / "install_hooks.sh")],
            capture_output=True, text=True, env=env, timeout=30,
        )

    def test_created_when_absent(self, tmp_path):
        home = self._home(tmp_path)
        result = self._run(home)
        assert result.returncode == 0, result.stderr
        data = json.loads(self._codex_hooks_path(home).read_text())
        assert set(data.keys()) == {"SessionStart", "SessionEnd"}, data.keys()
        assert len(data["SessionStart"]) == 1, data["SessionStart"]
        assert len(data["SessionEnd"]) == 1, data["SessionEnd"]
        start_cmd = data["SessionStart"][0]["hooks"][0]["command"]
        end_cmd = data["SessionEnd"][0]["hooks"][0]["command"]
        assert start_cmd.endswith("/codex_session_start.sh"), start_cmd
        assert end_cmd.endswith("/codex_session_end.sh"), end_cmd

    def test_merge_preserves_foreign_entries(self, tmp_path):
        home = self._home(tmp_path)
        (home / ".codex").mkdir(parents=True)
        foreign_pretooluse = {"matcher": "Bash", "hooks": [{"type": "command", "command": "/opt/foo/bar.sh"}]}
        foreign_sessionstart = {"matcher": "", "hooks": [{"type": "command", "command": "/opt/other/baz.sh"}]}
        original = {
            "PreToolUse": [foreign_pretooluse],
            "SessionStart": [foreign_sessionstart],
        }
        self._codex_hooks_path(home).write_text(json.dumps(original))

        result = self._run(home)
        assert result.returncode == 0, result.stderr
        data = json.loads(self._codex_hooks_path(home).read_text())

        assert data["PreToolUse"] == [foreign_pretooluse], "foreign PreToolUse entry must survive byte-for-byte"
        assert foreign_sessionstart in data["SessionStart"], "foreign SessionStart entry must survive"
        assert len(data["SessionStart"]) == 2, data["SessionStart"]
        owned = [e for e in data["SessionStart"] if e != foreign_sessionstart]
        assert len(owned) == 1, owned
        assert owned[0]["hooks"][0]["command"].endswith("/codex_session_start.sh"), owned
        assert len(data["SessionEnd"]) == 1, data["SessionEnd"]

    def test_idempotent_second_run(self, tmp_path):
        home = self._home(tmp_path)
        r1 = self._run(home)
        assert r1.returncode == 0, r1.stderr
        first = json.loads(self._codex_hooks_path(home).read_text())
        r2 = self._run(home)
        assert r2.returncode == 0, r2.stderr
        second = json.loads(self._codex_hooks_path(home).read_text())
        assert first == second, "re-running must not duplicate or reorder entries"


# ---------------------------------------------------------------------------
# T3 (D2) -- process_transcripts.py --client codex --session <sid>
# ---------------------------------------------------------------------------

class TestT3SessionFilter:
    def _run_pt(self, home: Path, memhome: Path, args: list[str]):
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["LLM_MEMORY_HOME"] = str(memhome)
        return subprocess.run(
            [_venv_python(), str(REPO_ROOT / "process_transcripts.py"), *args],
            capture_output=True, text=True, env=env, timeout=60,
        )

    def test_session_filter_writes_only_that_conversation(self, tmp_path):
        fixtures = _pick_non_subagent_fixtures(3)
        home = tmp_path / "home"
        memhome = tmp_path / "mem"
        sids = _install_codex_sessions(home, fixtures)

        result = self._run_pt(home, memhome, ["--client", "codex", "--session", sids[0], "--quiet"])
        assert result.returncode == 0, result.stdout + result.stderr

        conv = memhome / "conversations"
        written = sorted(p.name for p in conv.glob("codex-*.md")) if conv.exists() else []
        assert written == [f"{sids[0]}.md"], written

    def test_control_without_session_writes_all(self, tmp_path):
        fixtures = _pick_non_subagent_fixtures(3)
        home = tmp_path / "home"
        memhome = tmp_path / "mem"
        sids = _install_codex_sessions(home, fixtures)

        result = self._run_pt(home, memhome, ["--client", "codex", "--quiet"])
        assert result.returncode == 0, result.stdout + result.stderr

        conv = memhome / "conversations"
        written = sorted(p.name for p in conv.glob("codex-*.md"))
        assert written == sorted(f"{s}.md" for s in sids), written

    def test_unknown_session_errors_and_writes_nothing(self, tmp_path):
        fixtures = _pick_non_subagent_fixtures(3)
        home = tmp_path / "home"
        memhome = tmp_path / "mem"
        _install_codex_sessions(home, fixtures)

        result = self._run_pt(home, memhome, ["--client", "codex", "--session", "codex-doesnotexist", "--quiet"])
        assert result.returncode != 0, result.stdout + result.stderr
        combined = (result.stdout + result.stderr).lower()
        assert "codex-doesnotexist" in combined, combined
        assert "unknown" in combined or "no matching session" in combined, combined

        conv = memhome / "conversations"
        assert not conv.exists() or not any(conv.glob("*.md")), list(conv.glob("*.md"))


# ---------------------------------------------------------------------------
# T4 -- hooks/codex_session_end.sh
# ---------------------------------------------------------------------------

class TestT4SessionEndHook:
    SCRIPT = HOOKS_DIR / "codex_session_end.sh"

    def _run(self, home: Path, memhome: Path, session_id: str, cwd: str = "/home/user/projects/testproj"):
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["LLM_MEMORY_HOME"] = str(memhome)
        env["PATH"] = _inherited_path_with_interpreter_first()
        stdin = json.dumps({"session_id": session_id, "cwd": cwd, "hook_event_name": "SessionEnd"})
        return subprocess.run(
            [BASH, str(self.SCRIPT)], input=stdin, capture_output=True, text=True, env=env, timeout=60,
        )

    def test_happy_path_sweeps_silently(self, tmp_path):
        fixtures = _pick_non_subagent_fixtures(1)
        home = tmp_path / "home"
        memhome = tmp_path / "mem"
        sids = _install_codex_sessions(home, fixtures)
        raw_uuid = sids[0][len("codex-"):]

        result = self._run(home, memhome, raw_uuid)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "", result.stdout
        assert (memhome / "conversations" / f"{sids[0]}.md").exists()

    def test_warn_on_failure_without_breaking(self, tmp_path):
        fixtures = _pick_non_subagent_fixtures(1)
        home = tmp_path / "home"
        sids = _install_codex_sessions(home, fixtures)
        raw_uuid = sids[0][len("codex-"):]
        # Poison: point LLM_MEMORY_HOME at an existing plain file, so any
        # mkdir(parents=True) underneath it fails deterministically
        # (NotADirectoryError) regardless of uid/permission bits -- unlike a
        # chmod-based poison, this cannot be bypassed by running as root.
        poisoned_memhome = tmp_path / "poisoned_not_a_dir"
        poisoned_memhome.write_text("not a directory")

        result = self._run(home, poisoned_memhome, raw_uuid)
        assert result.returncode == 0, result.stderr
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        assert len(lines) == 1, lines
        assert lines[0].startswith("LLM_MEMORY_WARN:"), lines


# ---------------------------------------------------------------------------
# T5 (D3, injection half) -- hooks/codex_session_start.sh
# ---------------------------------------------------------------------------

class TestT5SessionStartHook:
    SCRIPT = HOOKS_DIR / "codex_session_start.sh"

    def _run(self, home: Path, memhome: Path, source: str, cwd: str, session_id: str = "codex-test-session"):
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["LLM_MEMORY_HOME"] = str(memhome)
        env["PATH"] = _inherited_path_with_interpreter_first()
        stdin = json.dumps({"source": source, "cwd": cwd, "session_id": session_id,
                             "hook_event_name": "SessionStart"})
        return subprocess.run(
            [BASH, str(self.SCRIPT)], input=stdin, capture_output=True, text=True, env=env, timeout=60,
        )

    def _sandbox(self, tmp_path, project="testproj", narrative_text="SENTINEL_CODEX_9f3a\n"):
        home = tmp_path / "home"
        memhome = tmp_path / "mem"
        (memhome / "projects").mkdir(parents=True)
        (memhome / "projects" / f"{project}.narrative.md").write_text(narrative_text)
        (memhome / "config").mkdir(parents=True)
        (memhome / "config" / "no-auto-update").touch()
        cwd = f"/home/user/projects/{project}"
        return home, memhome, cwd

    @pytest.mark.parametrize("source", ["startup", "resume"])
    def test_prints_narrative_on_startup_and_resume(self, tmp_path, source):
        home, memhome, cwd = self._sandbox(tmp_path)
        result = self._run(home, memhome, source, cwd)
        assert result.returncode == 0, result.stderr
        assert "SENTINEL_CODEX_9f3a" in result.stdout, result.stdout

    @pytest.mark.parametrize("source", ["compact", "clear"])
    def test_silent_on_compact_and_clear(self, tmp_path, source):
        home, memhome, cwd = self._sandbox(tmp_path)
        result = self._run(home, memhome, source, cwd)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "", result.stdout

    def test_silent_when_no_project(self, tmp_path):
        home, memhome, _cwd = self._sandbox(tmp_path)
        result = self._run(home, memhome, "startup", "/tmp/not-a-project-dir")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "", result.stdout

    def test_automatic_task_when_unprocessed_sessions_present(self, tmp_path):
        home, memhome, _cwd = self._sandbox(tmp_path, project="testproj2")
        (memhome / "conversations").mkdir(parents=True)
        (memhome / "conversations" / "codex-unmerged-1.md").write_text(
            "---\nsession_id: codex-unmerged-1\nproject: testproj2\n---\n\n=== user ===\nhi\n"
        )
        (memhome / "projects" / "testproj2.json").write_text(json.dumps(
            {"schema_version": "0.1", "project": "testproj2", "sessions": []}
        ))
        result = self._run(home, memhome, "startup", "/home/user/projects/testproj2")
        assert result.returncode == 0, result.stderr
        assert "AUTOMATIC TASK" in result.stdout, result.stdout


# ---------------------------------------------------------------------------
# T6 -- tools/codex_injection_probe.sh --dry-run
# ---------------------------------------------------------------------------

class TestT6ProbeDryRun:
    SCRIPT = TOOLS_DIR / "codex_injection_probe.sh"

    def test_dry_run_table_and_bypass_flag_scoped_to_hook(self, tmp_path):
        empty_bin = tmp_path / "empty"
        empty_bin.mkdir()
        env = os.environ.copy()
        # codex must not be resolvable anywhere -- --dry-run must never
        # actually invoke it.
        env["PATH"] = str(empty_bin)

        result = subprocess.run(
            [BASH, str(self.SCRIPT), "--dry-run"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        assert result.returncode == 0, result.stderr

        lines = [line for line in result.stdout.splitlines() if line.strip()]
        assert len(lines) == 4, lines
        assert lines[0] == "hook\trules-line\twrapper", lines[0]

        hook_line, rules_line, wrapper_line = lines[1], lines[2], lines[3]
        for line in (hook_line, rules_line, wrapper_line):
            assert line.startswith("codex exec "), line
        assert "--dangerously-bypass-hook-trust" in hook_line, hook_line
        assert "--dangerously-bypass-hook-trust" not in rules_line, rules_line
        assert "--dangerously-bypass-hook-trust" not in wrapper_line, wrapper_line
