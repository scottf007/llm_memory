"""Shared fixtures for llm_memory tests.

The old `memories`-table-based fixtures were removed along with
tests/test_server.py when the narrative/note/session_log types were
retired. New tests for the current tooling (items-table search,
narrative_coverage, resume, project_lookup) manage their own setup
in test_hooks.py / test_agent_memory.py / test_install.py.
"""
