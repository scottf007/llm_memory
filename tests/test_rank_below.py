"""Rank-below: archived hits stay reachable but never outrank active ones.

Covers the two retrieval paths (project_lookup, memory_search/search_items).
The 8-query panel is tools/narrative_score.py PANEL — the DIRTY metric source.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import indexer
import server

def test_panel_matches_narrative_score_source():
    """Refuse a substitute panel; these are the measured DIRTY queries."""
    ns: dict = {}
    src = Path(__file__).resolve().parent.parent / "tools" / "narrative_score.py"
    exec(compile(src.read_text(), str(src), "exec"), ns)
    assert PANEL == ns["PANEL"]


# Must match tools/narrative_score.py PANEL (the measured 8-query set).
PANEL = [
    "renderer budget",
    "adapter codex client",
    "narrative decay scoring",
    "installer hooks",
    "merger archive",
    "min_user_turns filter",
    "session start hook",
    "syncthing sync",
]

LIVE_DB = Path.home() / ".claude" / "memory" / "memory.db"
LIVE_JSON = Path.home() / ".claude" / "memory" / "projects" / "llm_memory.json"


def _archived_above_active_count(rows: list[dict]) -> int:
    """How many archived rows sit above at least one later active row."""
    statuses = [(r.get("status") or "active") == "archived" for r in rows]
    return sum(
        1
        for i, is_arch in enumerate(statuses)
        if is_arch and any(not s for s in statuses[i + 1 :])
    )


def _index_items(tmp_path: Path, rows: list[dict]) -> Path:
    items_root = tmp_path / "items"
    db_path = tmp_path / "memory.db"
    for row in rows:
        path = items_root / row["project"] / row["kind"] / f"{row['id']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(row))
    indexer.rebuild_items_index(items_root=items_root, db_path=db_path)
    return db_path


def _item(iid, text, status="active", kind="decisions", importance="standard", **kw):
    row = {
        "id": iid,
        "project": kw.get("project", "example_project"),
        "kind": kind,
        "text": text,
        "rationale": kw.get("rationale", ""),
        "quote": kw.get("quote", ""),
        "status": status,
        "importance": importance,
        "last_touched_at": kw.get("last_touched_at", "2026-01-01T00:00:00Z"),
    }
    row.update({k: v for k, v in kw.items() if k not in row})
    return row


def _write_ledger(tmp_path: Path, project: str, items: list[dict]) -> None:
    state = {
        "schema_version": "0.1",
        "project": project,
        "decisions": [],
        "learnings": [],
        "done": [],
        "goals": [],
        "suggestions": [],
    }
    for item in items:
        kind = item.get("kind", "decisions")
        state.setdefault(kind, []).append(item)
    proj_dir = tmp_path / "projects"
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / f"{project}.json").write_text(json.dumps(state))


def _lookup(monkeypatch, tmp_path, query, project="example_project", **args):
    monkeypatch.setattr(server, "DB_DIR", tmp_path)
    raw = server._handle_project_lookup({"project": project, "query": query, **args})
    payload = json.loads(raw[0].text)
    assert "results" in payload, payload
    return payload


def _search_handler(monkeypatch, db_path, query, **args):
    monkeypatch.setattr(server, "DB_PATH", db_path)
    raw = server._handle_search({"query": query, **args})
    return json.loads(raw[0].text)


def test_project_lookup_archived_does_not_outrank_active(monkeypatch, tmp_path):
    """Archived can have a higher token score and still sort below active."""
    _write_ledger(tmp_path, "example_project", [
        _item("dec-active", "wuzzle", status="active", importance="minor"),
        _item(
            "dec-arch",
            "wuzzle wuzzle wuzzle wuzzle wuzzle",
            status="archived",
            importance="load_bearing",
        ),
    ])
    payload = _lookup(monkeypatch, tmp_path, "wuzzle")
    ids = [r["id"] for r in payload["results"]]
    assert ids[0] == "dec-active"
    assert "dec-arch" in ids
    assert _archived_above_active_count(payload["results"]) == 0


def test_search_items_archived_does_not_outrank_active(tmp_path):
    db = _index_items(tmp_path, [
        _item("dec-active", "wuzzle", status="active", importance="minor"),
        _item(
            "dec-arch",
            "wuzzle wuzzle wuzzle wuzzle wuzzle",
            status="archived",
            importance="load_bearing",
        ),
    ])
    rows = indexer.search_items("wuzzle", db_path=db, limit=10)
    ids = [r["id"] for r in rows]
    assert ids[0] == "dec-active"
    assert "dec-arch" in ids
    assert _archived_above_active_count(rows) == 0


def test_memory_search_default_includes_archived_below_active(monkeypatch, tmp_path):
    db = _index_items(tmp_path, [
        _item("dec-active", "wuzzle", status="active"),
        _item("dec-arch", "wuzzle wuzzle wuzzle", status="archived"),
    ])
    rows = _search_handler(monkeypatch, db, "wuzzle")
    ids = [r["id"] for r in rows]
    assert ids[0] == "dec-active"
    assert "dec-arch" in ids


def test_archive_only_match_returned_within_limit_project_lookup(monkeypatch, tmp_path):
    _write_ledger(tmp_path, "example_project", [
        _item("dec-arch", "zzzxonly token", status="archived"),
        _item("dec-other", "unrelated active item", status="active"),
    ])
    payload = _lookup(monkeypatch, tmp_path, "zzzxonly", limit=10)
    ids = [r["id"] for r in payload["results"]]
    assert ids == ["dec-arch"]


def test_archive_only_match_returned_within_limit_search_items(monkeypatch, tmp_path):
    db = _index_items(tmp_path, [
        _item("dec-arch", "zzzxonly token", status="archived"),
        _item("dec-other", "unrelated active item", status="active"),
    ])
    rows = indexer.search_items("zzzxonly", db_path=db, limit=10)
    assert [r["id"] for r in rows] == ["dec-arch"]
    handler = _search_handler(monkeypatch, db, "zzzxonly", limit=10)
    assert [r["id"] for r in handler] == ["dec-arch"]


def test_status_filter_active_excludes_archived(monkeypatch, tmp_path):
    items = [
        _item("dec-active", "wuzzle", status="active"),
        _item("dec-arch", "wuzzle", status="archived"),
    ]
    _write_ledger(tmp_path, "example_project", items)
    db = _index_items(tmp_path, items)

    lookup = _lookup(monkeypatch, tmp_path, "wuzzle", status="active")
    assert [r["id"] for r in lookup["results"]] == ["dec-active"]

    rows = indexer.search_items("wuzzle", status="active", db_path=db, limit=10)
    assert [r["id"] for r in rows] == ["dec-active"]

    handler = _search_handler(monkeypatch, db, "wuzzle", status="active")
    assert [r["id"] for r in handler] == ["dec-active"]


def test_status_filter_archived_excludes_active(monkeypatch, tmp_path):
    items = [
        _item("dec-active", "wuzzle", status="active"),
        _item("dec-arch", "wuzzle", status="archived"),
    ]
    _write_ledger(tmp_path, "example_project", items)
    db = _index_items(tmp_path, items)

    lookup = _lookup(monkeypatch, tmp_path, "wuzzle", status="archived")
    assert [r["id"] for r in lookup["results"]] == ["dec-arch"]

    rows = indexer.search_items("wuzzle", status="archived", db_path=db, limit=10)
    assert [r["id"] for r in rows] == ["dec-arch"]

    handler = _search_handler(monkeypatch, db, "wuzzle", status="archived")
    assert [r["id"] for r in handler] == ["dec-arch"]


def _panel_fixture_items() -> list[dict]:
    """One high-score archived + one low-score active hit per PANEL query."""
    items = []
    for i, query in enumerate(PANEL):
        items.append(_item(
            f"dec-active-{i}",
            query,
            status="active",
            importance="minor",
        ))
        items.append(_item(
            f"dec-arch-{i}",
            f"{query} {query} {query}",
            status="archived",
            importance="load_bearing",
        ))
    return items


def test_eight_query_panel_no_archived_above_active_both_tools(monkeypatch, tmp_path):
    items = _panel_fixture_items()
    _write_ledger(tmp_path, "example_project", items)
    db = _index_items(tmp_path, items)

    inversions = 0
    top10 = 0
    for query in PANEL:
        lookup = _lookup(monkeypatch, tmp_path, query, limit=10)
        rows_l = lookup["results"]
        top10 += len(rows_l)
        inversions += _archived_above_active_count(rows_l)
        assert rows_l, query
        assert rows_l[0]["status"] != "archived", query

        fts = " ".join(f'"{t}"' for t in query.split())
        rows_s = indexer.search_items(fts, db_path=db, limit=10)
        handler = _search_handler(monkeypatch, db, query, limit=10)
        top10 += len(rows_s) + len(handler)
        inversions += _archived_above_active_count(rows_s)
        inversions += _archived_above_active_count(handler)
        assert rows_s and rows_s[0]["status"] != "archived", query
        assert handler and handler[0]["status"] != "archived", query
        # Archived still present (rank-below, not hide) when mixed hits exist.
        assert any(r["status"] == "archived" for r in rows_l)
        assert any(r["status"] == "archived" for r in rows_s)

    assert inversions == 0
    assert top10 > 0


@pytest.mark.skipif(
    not LIVE_DB.is_file() or not LIVE_JSON.is_file(),
    reason="live store not present; synthetic panel already covers the invariant",
)
def test_live_eight_query_panel_copy_no_archived_above_active(monkeypatch, tmp_path):
    """Re-run the measured panel against copies of the live store (read-only)."""
    db_copy = tmp_path / "memory.db"
    shutil.copy2(LIVE_DB, db_copy)
    wal, shm = LIVE_DB.with_suffix(".db-wal"), LIVE_DB.with_suffix(".db-shm")
    if wal.is_file():
        shutil.copy2(wal, tmp_path / "memory.db-wal")
    if shm.is_file():
        shutil.copy2(shm, tmp_path / "memory.db-shm")
    proj_dir = tmp_path / "projects"
    proj_dir.mkdir()
    shutil.copy2(LIVE_JSON, proj_dir / "llm_memory.json")

    inversions = 0
    archived_in_top10 = 0
    total = 0
    for query in PANEL:
        lookup = _lookup(monkeypatch, tmp_path, query, project="llm_memory", limit=10)
        rows_l = lookup["results"]
        fts = " ".join(f'"{t}"' for t in query.split())
        rows_s = indexer.search_items(
            fts, project="llm_memory", db_path=db_copy, limit=10
        )
        for rows in (rows_l, rows_s):
            total += len(rows)
            archived_in_top10 += sum(
                1 for r in rows if (r.get("status") or "active") == "archived"
            )
            inversions += _archived_above_active_count(rows)
    assert inversions == 0
    assert total > 0
    # Recorded so a reviewer can see DIRTY (archived-in-top-10) separately
    # from the rank-below invariant (archived-above-active).
    print(
        f"live panel: {archived_in_top10}/{total} top-10 archived, "
        f"{inversions} archived-above-active"
    )
