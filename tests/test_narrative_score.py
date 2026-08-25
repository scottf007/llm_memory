"""Tests for tools/narrative_score.py — the C3 certificate-aware contract.
Spec: SPEC-rev2-certification-cascade.md §10, §14.

FALSE_ID is the pre-existing (renamed, unchanged) computation, kept as a
regression fixture. With a certificate present, FALSE is replaced by the
certificate's own CONTRADICTION count (FALSE_CHECKED) -- exact, not a
heuristic string match. The score must never report `checked_clean=True`
while `not_checked` is non-empty (the "clean while unverified regions
exist" defect this contract exists to prevent), and a missing certificate
falls back to FALSE_ID rather than silently reporting a clean FALSE=0.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import narrative_score  # noqa: E402


def _write_project(home: Path, project: str, **kinds) -> Path:
    projects_dir = home / ".claude" / "memory" / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "project": project,
        "decisions": [], "goals": [], "suggestions": [],
        "learnings": [], "done": [], "sessions": [],
    }
    state.update(kinds)
    path = projects_dir / f"{project}.json"
    path.write_text(json.dumps(state))
    return path


def _write_certificate(home: Path, project: str, **over) -> Path:
    cert = {
        "project": project, "certified_at": "2026-06-01T00:00:00Z",
        "classifier_version": "leading-clause-v1", "matcher_version": "claim-match-v1",
        "verdict": "NO_KNOWN_FALSEHOOD",
        "counts": {"rendered_eligible": 10, "contradiction": 0, "suspect": 0,
                   "quarantined": 0, "abstained_parents": 0},
        "findings": [], "quarantine_ids": [], "not_checked": [],
        "uncertified_regions": [], "resolution_backlog": {"open_reviews": 0, "oldest_render_age": 0},
        "fuse_reason": None,
    }
    cert.update(over)
    path = home / ".claude" / "memory" / "projects" / f"{project}.certificate.json"
    path.write_text(json.dumps(cert))
    return path


def test_false_id_regression_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(narrative_score, "HOME", str(tmp_path / ".claude" / "memory"))
    home = tmp_path
    _write_project(home, "falseidproj", decisions=[
        {"id": "dec-old1", "text": "the old thing", "status": "archived",
         "archived_in": "sess1",
         "archived_reason": "superseded by the new approach"},
        {"id": "dec-new1", "text": "references dec-old1 directly", "status": "active"},
    ])

    r = narrative_score.score("falseidproj")
    assert r["FALSE_ID"] == 1


def test_certificate_aware_false_replaces_headline(tmp_path, monkeypatch):
    monkeypatch.setattr(narrative_score, "HOME", str(tmp_path / ".claude" / "memory"))
    _write_project(tmp_path, "certproj")
    _write_certificate(tmp_path, "certproj", verdict="CONTRADICTION",
                        counts={"rendered_eligible": 10, "contradiction": 3, "suspect": 1,
                                "quarantined": 3, "abstained_parents": 0})

    r = narrative_score.score("certproj")
    assert r["FALSE_CHECKED"] == 3
    assert r["FALSE"] == 3 == r["FALSE_CHECKED"]


def test_never_bare_clean_while_not_checked_nonempty(tmp_path, monkeypatch):
    monkeypatch.setattr(narrative_score, "HOME", str(tmp_path / ".claude" / "memory"))
    _write_project(tmp_path, "dirtyproj")
    _write_certificate(tmp_path, "dirtyproj", not_checked=[
        {"class": "unclassified_parents", "count": 3, "ids": ["dec-a", "dec-b", "dec-c"],
         "checked_as": "suspect_only", "not_checked_for": ["contradiction", "archive"]},
    ])

    r = narrative_score.score("dirtyproj")
    assert r["checked_clean"] is False

    # Non-trigger: empty not_checked + FALSE==0 -> checked_clean True.
    _write_project(tmp_path, "cleanproj")
    _write_certificate(tmp_path, "cleanproj", not_checked=[])
    r_clean = narrative_score.score("cleanproj")
    assert r_clean["FALSE"] == 0
    assert r_clean["checked_clean"] is True


def test_missing_certificate_falls_back_not_silently_clean(tmp_path, monkeypatch):
    monkeypatch.setattr(narrative_score, "HOME", str(tmp_path / ".claude" / "memory"))
    _write_project(tmp_path, "nocertproj", decisions=[
        {"id": "dec-old2", "text": "the retired thing", "status": "archived",
         "archived_in": "sess1", "archived_reason": "no longer current"},
        {"id": "dec-new2", "text": "cites dec-old2 directly", "status": "active"},
    ])

    r = narrative_score.score("nocertproj")
    assert r["checked_clean"] is None
    assert r["FALSE"] == r["FALSE_ID"] == 1
