"""Skip-when-absent / run-when-present tests for the relocated F-03 guard.

The literal real-name list lives outside the published tree
(`LLM_MEMORY_REAL_NAMES` or gitignored `tests/.real-names`). These tests
prove that mechanism with synthetic names only — they never write a real
project name into a committed file.

`test_codex_adapter._real_names` is the loader under test; the named
fixture-tree guard (`test_no_real_project_name_ships_in_a_fixture`) is the
skip/run consumer.
"""

from __future__ import annotations

import pytest

from tests import test_codex_adapter as tca


def test_loader_returns_none_when_env_and_file_absent(monkeypatch, tmp_path):
    monkeypatch.delenv("LLM_MEMORY_REAL_NAMES", raising=False)
    monkeypatch.setattr(tca, "_REAL_NAMES_FILE", tmp_path / "missing")
    assert tca._real_names() is None


def test_loader_reads_comma_separated_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_MEMORY_REAL_NAMES", " alpha ,beta, ")
    monkeypatch.setattr(tca, "_REAL_NAMES_FILE", tmp_path / "missing")
    assert tca._real_names() == ("alpha", "beta")


def test_loader_reads_untracked_file_when_env_absent(monkeypatch, tmp_path):
    monkeypatch.delenv("LLM_MEMORY_REAL_NAMES", raising=False)
    path = tmp_path / ".real-names"
    path.write_text("alpha\nbeta\n\n")
    monkeypatch.setattr(tca, "_REAL_NAMES_FILE", path)
    assert tca._real_names() == ("alpha", "beta")


def test_loader_prefers_env_over_file(monkeypatch, tmp_path):
    path = tmp_path / ".real-names"
    path.write_text("from-file\n")
    monkeypatch.setattr(tca, "_REAL_NAMES_FILE", path)
    monkeypatch.setenv("LLM_MEMORY_REAL_NAMES", "from-env")
    assert tca._real_names() == ("from-env",)


def test_loader_empty_file_is_absent(monkeypatch, tmp_path):
    monkeypatch.delenv("LLM_MEMORY_REAL_NAMES", raising=False)
    path = tmp_path / ".real-names"
    path.write_text("\n\n")
    monkeypatch.setattr(tca, "_REAL_NAMES_FILE", path)
    assert tca._real_names() is None


def test_named_guard_skips_with_clear_message_when_absent(monkeypatch, tmp_path):
    monkeypatch.delenv("LLM_MEMORY_REAL_NAMES", raising=False)
    monkeypatch.setattr(tca, "_REAL_NAMES_FILE", tmp_path / "missing")
    with pytest.raises(pytest.skip.Exception, match="no real-name list available"):
        tca.test_no_real_project_name_ships_in_a_fixture()
    assert "LLM_MEMORY_REAL_NAMES" in tca._NO_NAMES
    assert "tests/.real-names" in tca._NO_NAMES


def test_named_guard_runs_when_list_present_via_env(monkeypatch, tmp_path):
    monkeypatch.setattr(tca, "_REAL_NAMES_FILE", tmp_path / "missing")
    monkeypatch.setenv("LLM_MEMORY_REAL_NAMES", "this-token-is-not-in-any-fixture")
    tca.test_no_real_project_name_ships_in_a_fixture()


def test_named_guard_runs_when_list_present_via_file(monkeypatch, tmp_path):
    monkeypatch.delenv("LLM_MEMORY_REAL_NAMES", raising=False)
    path = tmp_path / ".real-names"
    path.write_text("this-token-is-not-in-any-fixture\n")
    monkeypatch.setattr(tca, "_REAL_NAMES_FILE", path)
    tca.test_no_real_project_name_ships_in_a_fixture()


def test_untracked_file_enables_named_guard_on_this_machine(real_names_file, monkeypatch):
    """Maintainer path: tests/.real-names is present, so the F-03 guard runs.

    Skips on a public clone via the conftest fixture rather than embedding
    the list in this file.
    """
    _path, names = real_names_file
    monkeypatch.delenv("LLM_MEMORY_REAL_NAMES", raising=False)
    assert tca._real_names() == names
    tca.test_no_real_project_name_ships_in_a_fixture()
