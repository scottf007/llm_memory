"""The client-adapter protocol.

One adapter per LLM client (Claude Code, Codex, Grok, ...). An adapter's only
job is to turn that client's on-disk session files into three neutral things:

    SessionRef   — "a session exists here"
    SessionMeta  — who/where/when
    Turn[]       — the conversation, in order

Everything after that is shared: `adapters.render` turns SessionMeta + Turn[]
into the `conversations/<sid>.md` contract, and every consumer downstream of
that file (delta-extractor, merger, renderer, indexer) is already
client-agnostic.

An adapter is a *module*, not a class. It must expose four functions:

    discover()          -> Iterable[SessionRef]
    session_meta(ref)   -> SessionMeta
    turns(ref)          -> Iterable[Turn]
    client_name()       -> str

`conforms()` checks that at import time so a half-written adapter fails loudly
rather than silently producing empty narratives.

Adapters for clients that store one session per file may also expose
`ref_for_path(path) -> SessionRef`, which lets callers address a single
transcript without walking `discover()`. It is a convenience, not part of the
protocol — a client that stores sessions some other way is free to omit it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable


@dataclass(frozen=True)
class SessionRef:
    """A pointer to one session as the client stores it.

    `path` is whatever that client's unit of storage is — a .jsonl file for
    Claude and Codex, a directory for Grok. Adapters are free to interpret it;
    nothing outside the adapter looks inside.
    """

    session_id: str
    path: Path
    client: str


@dataclass
class SessionMeta:
    """Session-level facts, already normalised across clients."""

    session_id: str
    client: str
    project: str = ""
    # The session's working directory as the client recorded it. `project` is
    # derived from it, but the envelope writer needs the original: a foreign
    # session's envelope has to carry a cwd the pipeline can re-derive the
    # same project from.
    cwd: str = ""
    started: str = ""
    ended: str = ""
    is_subagent: bool = False
    parent_session_id: str | None = None
    # Path recorded in the .md frontmatter as `raw:`. Always the archived
    # location, not the client's original file — the archive is what the rest
    # of the pipeline reads.
    raw: str = ""
    # The client's own file, when it differs from `raw` (foreign clients).
    # Recorded as `raw_source:` so provenance survives.
    raw_source: str = ""
    # Anything a client wants preserved in frontmatter that the protocol has
    # no opinion about. Rendered last, in insertion order.
    extra: dict[str, str] = field(default_factory=dict)


@dataclass
class Turn:
    """One conversational entry, in source order.

    Deliberately *not* pre-grouped: consecutive assistant entries are merged
    into a single rendered block by the shared renderer, so every client gets
    the same grouping rules for free.

    had_tool_use / raw_line exist so a rendered block can point back at the
    line of the source transcript where a tool call happened. A turn may carry
    `had_tool_use` with empty `text` (a tool-only entry): it contributes the
    line reference to its group but no prose.
    """

    role: str  # "user" | "assistant"
    timestamp: str
    text: str
    had_tool_use: bool = False
    raw_line: int | None = None


@runtime_checkable
class ClientAdapter(Protocol):
    """Structural type for an adapter module."""

    def discover(self) -> Iterable[SessionRef]: ...

    def session_meta(self, ref: SessionRef) -> SessionMeta: ...

    def turns(self, ref: SessionRef) -> Iterable[Turn]: ...

    def client_name(self) -> str: ...


REQUIRED = ("discover", "session_meta", "turns", "client_name")


def conforms(module) -> bool:
    """True if `module` exposes the four adapter functions."""
    return all(callable(getattr(module, name, None)) for name in REQUIRED)


def project_from_cwd(cwd: str) -> str:
    """Derive a project name from a working directory.

    The `.../projects/<name>/...` convention is shared across every client on
    this machine, so this stays one function rather than one per adapter.
    Returns "" when the path doesn't carry the convention — callers treat that
    as "unattributed", never as an error.
    """
    parts = Path(cwd).parts
    for i, part in enumerate(parts):
        if part == "projects" and i + 1 < len(parts):
            return parts[i + 1]
    return ""
