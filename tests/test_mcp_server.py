"""MCP 2.x registration tests for the stdio memory server."""

from __future__ import annotations

import asyncio
import json

import pytest

import server


def test_server_registers_exactly_the_four_memory_tools():
    entry = server.app.get_request_handler("tools/list")

    assert entry is not None
    result = asyncio.run(entry.handler(None, None))
    assert [tool.name for tool in result.tools] == [
        "memory_search",
        "narrative_coverage",
        "resume",
        "project_lookup",
    ]


@pytest.mark.parametrize(
    ("tool_name", "handler_name"),
    [
        ("memory_search", "_handle_search"),
        ("narrative_coverage", "_handle_narrative_coverage"),
        ("resume", "_handle_resume"),
        ("project_lookup", "_handle_project_lookup"),
    ],
)
def test_registered_call_handler_dispatches_each_tool(monkeypatch, tool_name, handler_name):
    seen = []

    def fake_handler(arguments):
        seen.append(arguments)
        return server._text(tool_name)

    monkeypatch.setattr(server, handler_name, fake_handler)
    entry = server.app.get_request_handler("tools/call")

    assert entry is not None
    params = server.types.CallToolRequestParams(name=tool_name, arguments={"probe": True})
    result = asyncio.run(entry.handler(None, params))
    assert seen == [{"probe": True}]
    assert result.content[0].text == tool_name


def test_handlers_are_registered_with_mcp_2_constructor_api():
    assert not hasattr(server.app, "list_tools")
    assert not hasattr(server.app, "call_tool")
    assert server.app.get_request_handler("tools/list") is not None
    assert server.app.get_request_handler("tools/call") is not None


def test_resume_with_empty_conversation_path_reports_no_transcript(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "demo.json").write_text(json.dumps({
        "project": "demo",
        "sessions": [{
            "status": "active",
            "session_id": "session-1",
            "started": "2026-01-01T00:00:00Z",
            "conversation_md": "",
        }],
    }))
    monkeypatch.setattr(server, "DB_DIR", tmp_path)

    payload = json.loads(server._handle_resume({"project": "demo"})[0].text)

    assert payload["conversation_tail"] == "(no conversation transcript recorded)"


@pytest.mark.parametrize(
    "recorded_path",
    [
        "~/.claude/memory/conversations/session-1.md",
        "conversations/session-1.md",
    ],
)
def test_resume_resolves_conversation_at_current_memory_root(
    tmp_path, monkeypatch, recorded_path
):
    configured = tmp_path / "relocated"
    projects = configured / "projects"
    conversations = configured / "conversations"
    projects.mkdir(parents=True)
    conversations.mkdir()
    (conversations / "session-1.md").write_text("first line\nrelocated tail\n")
    (projects / "demo.json").write_text(json.dumps({
        "project": "demo",
        "sessions": [{
            "status": "active",
            "session_id": "session-1",
            "started": "2026-01-01T00:00:00Z",
            "conversation_md": recorded_path,
        }],
    }))
    monkeypatch.setenv("LLM_MEMORY_HOME", str(configured))
    monkeypatch.setattr(server, "DB_DIR", configured)

    payload = json.loads(server._handle_resume({"project": "demo", "lines": 1})[0].text)

    assert payload["conversation_tail"] == "relocated tail"
