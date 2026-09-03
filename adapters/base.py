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
from typing import Callable, Iterable, Iterator, Protocol, runtime_checkable


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
    # Parse-time observations worth surfacing to a human — an ambiguous file,
    # a shape the adapter did not expect. Never rendered: this is for the
    # operator, not for the conversation. Empty is the normal case.
    notes: list[str] = field(default_factory=list)


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


ParseResult = tuple[SessionMeta, list[Turn]]
ParseImplementation = Callable[[SessionRef], ParseResult]
CacheKey = tuple[object, ...]
CacheKeyImplementation = Callable[[SessionRef], CacheKey | None]


def make_adapter_parser(
    parse_impl: ParseImplementation,
    cache_key: CacheKeyImplementation | None = None,
) -> tuple[
    Callable[[SessionRef], ParseResult],
    Callable[[SessionRef], SessionMeta],
    Callable[[SessionRef], Iterator[Turn]],
]:
    """Build a shared parse/session_meta/turns facade for an adapter.

    Each adapter owns ``parse_impl`` because transcript record formats vary by
    client. The facade owns a one-entry cache. File-backed adapters use their
    transcript stat by default; directory-backed adapters can supply a key
    spanning the source files they depend on.
    """
    cache: tuple[CacheKey, ParseResult] | None = None

    if cache_key is None:
        def cache_key(ref: SessionRef) -> CacheKey | None:
            try:
                stat = ref.path.stat()
            except OSError:
                return None
            return str(ref.path), stat.st_mtime_ns, stat.st_size

    def parse(ref: SessionRef) -> ParseResult:
        """Meta and turns together — one read when a caller wants both."""
        nonlocal cache
        key = cache_key(ref)
        if key is not None and cache is not None and cache[0] == key:
            return cache[1]

        result = parse_impl(ref)
        if key is not None:
            cache = (key, result)
        return result

    def session_meta(ref: SessionRef) -> SessionMeta:
        """Return the metadata half of the cached parse result."""
        return parse(ref)[0]

    def turns(ref: SessionRef) -> Iterator[Turn]:
        """Iterate over the turns half of the cached parse result."""
        return iter(parse(ref)[1])

    for fn, name in ((parse, "parse"), (session_meta, "session_meta"), (turns, "turns")):
        fn.__module__ = parse_impl.__module__
        fn.__qualname__ = name

    return parse, session_meta, turns


def archive_path(session_id: str) -> str:
    """Portable archive provenance for a transcript stored in memory root."""
    return f"transcripts/{session_id}.jsonl"


def project_from_cwd(cwd: str) -> str:
    """Derive a project name from a working directory.

    The `.../projects/<name>/...` convention is shared across every client on
    this machine, so this stays one function rather than one per adapter.
    Returns "" when the path doesn't carry the convention — callers treat that
    as "unattributed", never as an error.
    """
    parts = Path(cwd).parts
    for i, part in enumerate(parts):
        if part != "projects":
            continue
        for candidate in parts[i + 1:]:
            # Dotted directories under projects/ are infrastructure (for
            # example agent-messaging worktree roots), never project names.
            if not candidate.startswith("."):
                return candidate
        return ""
    return ""
