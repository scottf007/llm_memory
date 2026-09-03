"""Regression coverage for hermetic live-corpus collection marking."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests import conftest as target_conftest


pytest_plugins = ["pytester"]
REPO_ROOT = Path(__file__).resolve().parents[1]


def _install_real_marking_hook(pytester) -> None:
    """Make a self-rooted probe project use this repository's marking hook."""
    pytester.makeini("""[pytest]
markers =
    live_corpus: probe marker
""")
    pytester.makeconftest(f"""
import sys
sys.path.insert(0, {str(REPO_ROOT)!r})
from tests.conftest import pytest_collection_modifyitems  # noqa: F401
""")


def _live_corpus_node_ids(result) -> set[str]:
    return {
        line.strip()
        for line in result.stdout.lines
        if "::" in line and not line.lstrip().startswith("=")
    }


def test_parametrized_live_name_exclusion_uses_base_node_id(pytester, monkeypatch):
    """An excluded ``test_live_`` parameterization must remain hermetic."""
    _install_real_marking_hook(pytester)
    pytester.makepyfile(test_exclusion_probe="""
import pytest


@pytest.mark.parametrize("value", ["a"])
def test_live_probe(value):
    pass
""")
    monkeypatch.setattr(
        target_conftest,
        "_LIVE_CORPUS_NAME_EXCLUSIONS",
        {"test_exclusion_probe.py::test_live_probe"},
    )

    result = pytester.runpytest("--collect-only", "-q", "-m", "live_corpus")

    assert result.ret == pytest.ExitCode.NO_TESTS_COLLECTED, result.stdout.str()
    assert not _live_corpus_node_ids(result), result.stdout.str()


def test_parametrized_live_name_not_in_exclusions_is_marked(pytester, monkeypatch):
    """Control: the same parametrized name stays live when not excluded."""
    _install_real_marking_hook(pytester)
    pytester.makepyfile(test_exclusion_probe="""
import pytest


@pytest.mark.parametrize("value", ["a"])
def test_live_probe(value):
    pass
""")
    monkeypatch.setattr(target_conftest, "_LIVE_CORPUS_NAME_EXCLUSIONS", set())

    result = pytester.runpytest("--collect-only", "-q", "-m", "live_corpus")

    assert result.ret == pytest.ExitCode.OK, result.stdout.str()
    assert _live_corpus_node_ids(result) == {
        "test_exclusion_probe.py::test_live_probe[a]"
    }


def test_name_rule_census_has_no_drift(request):
    """The real collected tree has no unclassified ``test_live_`` function."""
    unexpected = target_conftest.unclassified_live_name_node_ids(request.session.items)

    assert not unexpected, (
        "unclassified test_live_ row(s): "
        f"{unexpected}; add it to LIVE_CORPUS_NODE_IDS or "
        "_LIVE_CORPUS_NAME_EXCLUSIONS"
    )


def test_name_rule_census_guard_rejects_new_live_test(pytester):
    """Trigger: a new ``test_live_`` name is rejected with an actionable id."""
    pytester.makeini("""[pytest]
markers =
    live_corpus: probe marker
""")
    pytester.makepyfile(test_census_probe="""
def test_live_new():
    pass
""")
    pytester.makeconftest(f"""
import sys
import pytest
sys.path.insert(0, {str(REPO_ROOT)!r})
from tests.conftest import (
    pytest_collection_modifyitems as _real_collection_hook,
    unclassified_live_name_node_ids,
)


def pytest_collection_modifyitems(items):
    _real_collection_hook(items)
    unexpected = unclassified_live_name_node_ids(items)
    if unexpected:
        raise pytest.UsageError(
            "unclassified test_live_ row(s): " + repr(unexpected) +
            "; add it to LIVE_CORPUS_NODE_IDS or _LIVE_CORPUS_NAME_EXCLUSIONS"
        )
""")

    result = pytester.runpytest("--collect-only", "-q")

    assert result.ret == pytest.ExitCode.USAGE_ERROR
    output = result.stdout.str() + result.stderr.str()
    assert "test_census_probe.py::test_live_new" in output
    assert "add it to LIVE_CORPUS_NODE_IDS or _LIVE_CORPUS_NAME_EXCLUSIONS" in output
