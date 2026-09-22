"""Registration tests for the stdio memory server.

requirements.txt allows mcp>=1.0,<3 and server.py registers through whichever
API the installed mcp exposes: the 1.x list_tools()/call_tool() decorators, or
the 2.x on_list_tools=/on_call_tool= constructor. These tests used to reach
only for the 2.x accessor, so they failed on every machine running mcp 1.x --
six permanently red tests in a suite whose greenness is the signal that the
pipeline is healthy. A suite that is normally red teaches people to ignore it,
which is how a dead extraction worker went unnoticed for nine days.

They now assert BEHAVIOUR -- which tools are served, and where a call is
dispatched -- through the helpers below, so they hold on either version.
"""

from __future__ import annotations

import asyncio
import json

import pytest

import server


def _unwrap(result):
    """mcp 1.x wraps handler output in ServerResult; 2.x returns it directly."""
    return getattr(result, "root", result)


def _served_tools() -> list[str]:
    """Tool names the server advertises, on either mcp API."""
    get = getattr(server.app, "get_request_handler", None)
    if get is not None:                                     # mcp 2.x
        entry = get("tools/list")
        assert entry is not None, "tools/list is not registered"
        return [t.name for t in _unwrap(asyncio.run(entry.handler(None, None))).tools]
    handler = server.app.request_handlers.get(server.types.ListToolsRequest)
    assert handler is not None, "tools/list is not registered"
    request = server.types.ListToolsRequest(method="tools/list")
    return [t.name for t in _unwrap(asyncio.run(handler(request))).tools]


def _dispatch(tool_name: str, arguments: dict):
    """Invoke tools/call and return the CallToolResult, on either mcp API."""
    params = server.types.CallToolRequestParams(name=tool_name, arguments=arguments)
    get = getattr(server.app, "get_request_handler", None)
    if get is not None:                                     # mcp 2.x
        entry = get("tools/call")
        assert entry is not None, "tools/call is not registered"
        return _unwrap(asyncio.run(entry.handler(None, params)))
    handler = server.app.request_handlers.get(server.types.CallToolRequest)
    assert handler is not None, "tools/call is not registered"
    request = server.types.CallToolRequest(method="tools/call", params=params)
    return _unwrap(asyncio.run(handler(request)))


def test_server_registers_exactly_the_four_memory_tools():
    assert _served_tools() == [
        "memory_search",
        "narrative_coverage",
        "resume",
        "project_lookup",
    ]


# Arguments must satisfy each tool's declared inputSchema. mcp 1.x validates
# the schema BEFORE dispatching, so the previous probe argument ({"probe": True})
# was rejected with "Input validation error" and the handler was never reached.
# Using real arguments makes this test also prove the declared schemas accept
# the shape callers actually send.
VALID_ARGS = {
    "memory_search": {"query": "anything"},
    "narrative_coverage": {"project": "someproject"},
    "resume": {"project": "someproject"},
    "project_lookup": {"project": "someproject", "query": "anything"},
}


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
    arguments = VALID_ARGS[tool_name]

    result = _dispatch(tool_name, arguments)

    assert seen == [arguments], "the registered dispatcher did not reach the tool's handler"
    assert result.content[0].text == tool_name


@pytest.mark.parametrize("tool_name", sorted(VALID_ARGS))
def test_declared_schema_rejects_arguments_missing_a_required_field(tool_name):
    """The schema is enforced, not decorative.

    This is what the old probe argument was accidentally testing. Keeping it as
    its own case means a schema that stops being enforced fails here rather
    than silently turning the dispatch test above into a no-op.
    """
    result = _dispatch(tool_name, {"definitely_not_a_declared_field": True})
    assert getattr(result, "isError", False) or "error" in result.content[0].text.lower()


def test_handlers_are_registered_under_whichever_mcp_api_is_installed():
    """server.py picks its registration branch from hasattr(Server, 'list_tools').

    Asserting the branch actually taken -- rather than assuming 2.x -- is what
    makes this test portable across the mcp>=1.0,<3 range requirements.txt
    allows, and it still fails loudly if a future mcp changes the shape from
    under the branch that was chosen.
    """
    from mcp.server import Server

    if hasattr(Server, "list_tools"):                       # mcp 1.x
        assert server.types.ListToolsRequest in server.app.request_handlers
        assert server.types.CallToolRequest in server.app.request_handlers
    else:                                                    # mcp 2.x
        assert server.app.get_request_handler("tools/list") is not None
        assert server.app.get_request_handler("tools/call") is not None

    # Either way the observable contract is identical.
    assert _served_tools() == [
        "memory_search",
        "narrative_coverage",
        "resume",
        "project_lookup",
    ]


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
        "~/.llm-memory/conversations/session-1.md",
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
