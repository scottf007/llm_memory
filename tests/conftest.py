"""Shared fixtures for llm_memory tests.

The old `memories`-table-based fixtures were removed along with
tests/test_server.py when the narrative/note/session_log types were
retired. New tests for the current tooling (items-table search,
narrative_coverage, resume, project_lookup) manage their own setup
in test_hooks.py / test_agent_memory.py / test_install.py.
"""

from pathlib import Path

import pytest

# Untracked F-03 list. Gitignored (see .gitignore). Present on the maintainer's
# machine so the literal-name guard runs; absent on a public clone, where the
# named guard skips and the synthetic trigger control still runs.
REAL_NAMES_FILE = Path(__file__).resolve().parent / ".real-names"


@pytest.fixture
def real_names_file():
    """Return (path, names) for tests/.real-names, or skip if it is absent.

    The file is the untracked local source for the F-03 regression guard.
    Tests that need the *mechanism* (skip-when-absent / run-when-present)
    must not use this fixture — they isolate env + path themselves. Tests
    that need the *maintainer list* use this so a public clone skips them
    instead of inventing names in the committed tree.
    """
    if not REAL_NAMES_FILE.is_file():
        pytest.skip(
            "tests/.real-names is absent; F-03 literal-name guard is disabled. "
            "Create it (one name per line) or set LLM_MEMORY_REAL_NAMES."
        )
    names = tuple(
        line.strip()
        for line in REAL_NAMES_FILE.read_text().splitlines()
        if line.strip()
    )
    if not names:
        pytest.skip("tests/.real-names exists but is empty")
    return REAL_NAMES_FILE, names
