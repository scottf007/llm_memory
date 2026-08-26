"""MCP 2.x registration tests for the stdio memory server."""

from __future__ import annotations

import asyncio

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
