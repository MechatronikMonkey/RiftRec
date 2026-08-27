"""EW-89: a run that recorded nothing must not leave a file behind.

A mis-start - strap not put on, game never launched - could leave an empty
.sqlite in the participant's folder. Over weeks those pile up and make it
impossible to tell which files are worth sending back. The counterpart rule is
older and harder (EW-52): nothing that was recorded may ever be deleted, so
these tests pin both directions.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from riftrec import SCHEMA_VERSION, __version__
from riftrec.model import SessionMeta
from riftrec.storage.sqlite_sink import SqliteSink, discard_if_unused


def _open_real_session(db: Path) -> SqliteSink:
    sink = SqliteSink(db)
    sink.open_session(SessionMeta(
        session_id="s1", participant_id="P01", session_index=1,
        started_utc="2026-08-27T10:00:00+00:00", mono_anchor_ns=0,
        app_version=__version__, schema_version=SCHEMA_VERSION,
    ))
    return sink


def test_missing_file_is_not_an_error(tmp_path) -> None:
    """The usual case: no match ever started, so no file was created."""
    assert discard_if_unused(tmp_path / "never-created.sqlite") is False


def test_schema_without_sessions_is_removed(tmp_path) -> None:
    """Tables created, then the run ended before a session row was written."""
    db = tmp_path / "empty.sqlite"
    sink = SqliteSink(db)
    sink.open_session(SessionMeta(
        session_id="s1", participant_id="P01", session_index=1,
        started_utc="2026-08-27T10:00:00+00:00", mono_anchor_ns=0,
        app_version=__version__, schema_version=SCHEMA_VERSION,
    ))
    sink.close_session("2026-08-27T10:00:01+00:00")
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM session")
    conn.commit()
    conn.close()

    assert discard_if_unused(db) is True
    assert not db.exists()


def test_recorded_session_is_never_deleted(tmp_path) -> None:
    """The hard rule from EW-52 - a real recording stays, always."""
    db = tmp_path / "real.sqlite"
    sink = _open_real_session(db)
    sink.close_session("2026-08-27T10:40:00+00:00")

    assert discard_if_unused(db) is False
    assert db.exists()
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM session").fetchone()[0] == 1
    finally:
        conn.close()


def test_unreadable_file_is_left_alone(tmp_path) -> None:
    """If we cannot prove a file is empty, we keep it. Deleting is the risk."""
    db = tmp_path / "not-sqlite.sqlite"
    db.write_bytes(b"this is not a database at all, but it is somebody's file")

    assert discard_if_unused(db) is False
    assert db.exists()


def test_sidecar_files_go_with_the_database(tmp_path) -> None:
    """WAL mode leaves -wal/-shm next to the file; they are clutter too."""
    db = tmp_path / "empty.sqlite"
    sink = SqliteSink(db)
    sink.open_session(SessionMeta(
        session_id="s1", participant_id="P01", session_index=1,
        started_utc="2026-08-27T10:00:00+00:00", mono_anchor_ns=0,
        app_version=__version__, schema_version=SCHEMA_VERSION,
    ))
    sink.close_session("2026-08-27T10:00:01+00:00")
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM session")
    conn.commit()
    conn.close()
    wal = db.with_name(db.name + "-wal")
    wal.write_bytes(b"")

    assert discard_if_unused(db) is True
    assert not db.exists()
    assert not wal.exists()
