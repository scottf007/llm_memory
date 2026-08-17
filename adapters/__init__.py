"""Per-client session adapters.

Register a new client by adding its module here. Nothing else in the pipeline
should ever need to know the client list: `conversations/<sid>.md` is the
interface, and everything downstream of it is client-agnostic already.

    from adapters import get, extract_session

    md = extract_session(Path("~/.claude/memory/transcripts/<sid>.jsonl"))
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

from . import claude
from .base import ClientAdapter, SessionMeta, SessionRef, Turn, conforms, project_from_cwd
from .render import render_conversation, render_subagent

_REGISTRY: dict[str, ModuleType] = {
    claude.client_name(): claude,
}

for _name, _module in _REGISTRY.items():
    if not conforms(_module):
        raise ImportError(f"adapter {_name!r} does not implement the adapter protocol")

DEFAULT = claude.client_name()

__all__ = [
    "ClientAdapter",
    "SessionMeta",
    "SessionRef",
    "Turn",
    "DEFAULT",
    "conforms",
    "extract_session",
    "get",
    "names",
    "project_from_cwd",
    "render",
    "render_conversation",
    "render_subagent",
]


def names() -> list[str]:
    """Registered client names."""
    return sorted(_REGISTRY)


def get(client: str = DEFAULT) -> ModuleType:
    """Return the adapter module for `client`."""
    try:
        return _REGISTRY[client]
    except KeyError:
        raise KeyError(f"unknown client {client!r}; known: {', '.join(names())}") from None


def render(ref: SessionRef, client: str | None = None) -> str:
    """Render one session ref to the conversations/<sid>.md contract."""
    adapter = get(client or ref.client)
    return render_conversation(adapter.session_meta(ref), adapter.turns(ref))


def extract_session(path: Path, client: str = DEFAULT) -> str:
    """Render the session stored at `path`, as that client stores it."""
    adapter = get(client)
    return render(adapter.ref_for_path(Path(path)), client)
