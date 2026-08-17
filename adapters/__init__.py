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

from . import claude, codex
from .base import ClientAdapter, SessionMeta, SessionRef, Turn, conforms, project_from_cwd
from .envelope import render_envelope, verify_envelope, write_envelope
from .render import render_conversation, render_subagent

_REGISTRY: dict[str, ModuleType] = {
    claude.client_name(): claude,
    codex.client_name(): codex,
}

for _name, _module in _REGISTRY.items():
    if not conforms(_module):
        raise ImportError(f"adapter {_name!r} does not implement the adapter protocol")

DEFAULT = claude.client_name()

# Session-id prefixes for foreign clients. Two jobs: one flat transcripts/
# directory means a cross-client id collision would be a silent overwrite, and
# a prefix lets a caller route a bare session id back to the adapter that owns
# it — which is what stops the claude adapter re-extracting a codex envelope
# that is, by design, Claude-shaped.
#
# Deliberately disjoint from `agent-` and `audit-`, which are already
# load-bearing filters in conversations.py and process_transcripts.py.
_PREFIXES: dict[str, str] = {
    codex.ID_PREFIX: codex.client_name(),
}

RESERVED_PREFIXES = ("agent-", "audit-")

__all__ = [
    "ClientAdapter",
    "SessionMeta",
    "SessionRef",
    "Turn",
    "DEFAULT",
    "RESERVED_PREFIXES",
    "client_for_session_id",
    "conforms",
    "extract_session",
    "get",
    "names",
    "prefixes",
    "project_from_cwd",
    "render",
    "render_conversation",
    "render_envelope",
    "render_subagent",
    "verify_envelope",
    "write_envelope",
]


def names() -> list[str]:
    """Registered client names."""
    return sorted(_REGISTRY)


def prefixes() -> dict[str, str]:
    """Session-id prefix -> client name, for every foreign client."""
    return dict(_PREFIXES)


def client_for_session_id(session_id: str) -> str:
    """Which adapter owns a bare session id.

    Unprefixed ids are Claude's, which is what every id in the existing corpus
    is. This is the routing that keeps the claude adapter from re-extracting a
    foreign client's envelope: the envelope is Claude-shaped on purpose, so
    shape cannot be the discriminator — the id is.

    Matching is case-insensitive. Adapters emit lowercase ids, but this routes
    *whatever is on disk*: a file that arrived through a case-insensitive
    filesystem, a rename, or a hand-edit would otherwise fall through to Claude
    and be re-extracted into a `client: claude` conversation. Failing open to
    the wrong client is the expensive direction.
    """
    lowered = session_id.lower()
    for prefix, client in _PREFIXES.items():
        if lowered.startswith(prefix.lower()):
            return client
    return DEFAULT


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
