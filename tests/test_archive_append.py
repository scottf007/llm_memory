"""archive_transcript must not re-copy a whole growing transcript on every run.

Transcripts are append-only JSONL.  When the archived copy is still a byte
prefix of the source, only the new tail is appended.  On 8 Nov 2026 the
full re-copy of every growing live seat transcript was most of the 1.7 GB
one startup sweep wrote.  Anything that is not a clean prefix (truncated,
rewritten, diverged) still gets a full copy.
"""

import os

import process_transcripts as pt


def _setup(tmp_path, monkeypatch):
    archive = tmp_path / "archive"
    monkeypatch.setattr(pt, "ARCHIVE_DIR", archive)
    src = tmp_path / "live" / "s1.jsonl"
    src.parent.mkdir()
    return archive, src


def _no_full_copy(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("full copy used where an append was enough")
    monkeypatch.setattr(pt.shutil, "copy2", refuse)


def test_growing_transcript_appends_only_the_tail(tmp_path, monkeypatch):
    archive, src = _setup(tmp_path, monkeypatch)
    src.write_bytes(b'{"n": 1}\n' * 20000)
    pt.archive_transcript(src, "s1")
    with src.open("ab") as fh:
        fh.write(b'{"n": 2}\n' * 10)
    os.utime(src, (1_700_000_000, 1_700_000_000))
    _no_full_copy(monkeypatch)
    dest = pt.archive_transcript(src, "s1")
    assert dest.read_bytes() == src.read_bytes()
    # mtime follows the source, as copy2 did: ensure_conversation_md
    # re-extracts by comparing it.
    assert dest.stat().st_mtime == src.stat().st_mtime


def test_unchanged_transcript_is_not_touched(tmp_path, monkeypatch):
    archive, src = _setup(tmp_path, monkeypatch)
    src.write_bytes(b'{"n": 1}\n' * 100)
    dest = pt.archive_transcript(src, "s1")
    before = dest.stat().st_mtime_ns
    _no_full_copy(monkeypatch)
    pt.archive_transcript(src, "s1")
    assert dest.stat().st_mtime_ns == before


def test_diverged_archive_gets_a_full_copy(tmp_path, monkeypatch):
    """Same-or-shorter archive with different bytes near its end is not a
    prefix; appending would corrupt it."""
    archive, src = _setup(tmp_path, monkeypatch)
    archive.mkdir()
    (archive / "s1.jsonl").write_bytes(b'{"old": true}\n' * 50)
    src.write_bytes(b'{"new": true}\n' * 100)
    dest = pt.archive_transcript(src, "s1")
    assert dest.read_bytes() == src.read_bytes()


def test_diverged_head_with_identical_tail_gets_a_full_copy(tmp_path, monkeypatch):
    """llm-u1a-judge2 F1 on 7c02e6d: the first record changed while more
    than 64 KiB of following bytes stayed identical.  A tail-only check
    appended onto the stale head and produced a corrupt archive.  The whole
    archived prefix must match before anything is appended."""
    archive, src = _setup(tmp_path, monkeypatch)
    body = b'{"n": 1}\n' * 20000  # ~180 KiB, well past any tail window
    archive.mkdir()
    (archive / "s1.jsonl").write_bytes(b'{"first": "OLD"}\n' + body)
    src.write_bytes(b'{"first": "NEW"}\n' + body + b'{"n": 2}\n' * 10)
    dest = pt.archive_transcript(src, "s1")
    assert dest.read_bytes() == src.read_bytes()


def test_diverged_middle_gets_a_full_copy(tmp_path, monkeypatch):
    archive, src = _setup(tmp_path, monkeypatch)
    head, tail = b'{"n": 1}\n' * 10000, b'{"n": 3}\n' * 10000
    archive.mkdir()
    (archive / "s1.jsonl").write_bytes(head + b'{"mid": "OLD"}\n' + tail)
    src.write_bytes(head + b'{"mid": "NEW"}\n' + tail + b'{"n": 4}\n')
    dest = pt.archive_transcript(src, "s1")
    assert dest.read_bytes() == src.read_bytes()


def test_new_transcript_is_copied(tmp_path, monkeypatch):
    archive, src = _setup(tmp_path, monkeypatch)
    src.write_bytes(b'{"n": 1}\n')
    dest = pt.archive_transcript(src, "s1")
    assert dest.read_bytes() == src.read_bytes()


# Ported verbatim from llm-u1a-judge2's verdict 622908ff (F1 reproduction).
def test_divergence_before_matching_tail_is_replaced(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, 'ARCHIVE_DIR', tmp_path / 'archive')
    src = tmp_path / 's.jsonl'
    tail = b'{"same":true}\n' * 10000
    src.write_bytes(b'{"owner":"old"}\n' + tail)
    dest = pt.archive_transcript(src, 's')
    src.write_bytes(b'{"owner":"new"}\n' + tail + b'{"new":true}\n')
    pt.archive_transcript(src, 's')
    assert dest.read_bytes() == src.read_bytes(), 'archive retains old owner and appends new source tail'
