"""SQLite-Session-Sink (WAL).

Puffert Records nach Tabelle und schreibt sie gebündelt per executemany.
WAL-Modus sorgt dafür, dass committete Daten einen Absturz oder BLE-Dropout
mitten in der Session überleben (EW-39). Ein Sink schreibt genau eine Session
in genau eine .sqlite-Datei; diese Datei ist der Vertrag zu RiftLab.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..model import Gap, GameEvent, GameSnapshot, HrSample, Record, RrInterval, SessionMeta

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class SqliteSink:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._session_id: str | None = None
        # Puffer je Zieltabelle: Liste von Wert-Tupeln für executemany.
        self._buf: dict[str, list[tuple]] = {
            "hr_sample": [],
            "rr_interval": [],
            "game_event": [],
            "game_snapshot": [],
        }

    # -- Lifecycle ---------------------------------------------------------

    def open_session(self, meta: SessionMeta) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO session (session_id, participant_id, session_index, "
            "started_utc, ended_utc, mono_anchor_ns, app_version, schema_version, notes) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                meta.session_id, meta.participant_id, meta.session_index,
                meta.started_utc, meta.ended_utc, meta.mono_anchor_ns,
                meta.app_version, meta.schema_version, meta.notes,
            ),
        )
        conn.commit()
        self._conn = conn
        self._session_id = meta.session_id

    def write(self, record: Record) -> None:
        sid = self._session_id
        if isinstance(record, HrSample):
            self._buf["hr_sample"].append((sid, record.mono_ns, record.utc, record.hr_bpm))
        elif isinstance(record, RrInterval):
            self._buf["rr_interval"].append((sid, record.mono_ns, record.utc, record.rr_ms))
        elif isinstance(record, GameEvent):
            self._buf["game_event"].append((
                sid, record.mono_ns, record.utc, record.game_time_s,
                record.event_id, record.event_type, record.payload_json,
            ))
        elif isinstance(record, GameSnapshot):
            self._buf["game_snapshot"].append((
                sid, record.mono_ns, record.utc, record.game_time_s,
                record.kills, record.deaths, record.assists,
                record.cs, record.gold, record.level,
            ))
        else:  # pragma: no cover - Schutz gegen versehentlich neue Record-Typen
            raise TypeError(f"Unbekannter Record-Typ: {type(record).__name__}")

    def flush(self) -> None:
        if self._conn is None:
            return
        wrote = False
        for table, rows in self._buf.items():
            if not rows:
                continue
            placeholders = ",".join("?" * len(rows[0]))
            self._conn.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)
            rows.clear()
            wrote = True
        if wrote:
            self._conn.commit()

    def mark_gap(self, gap: Gap) -> None:
        if self._conn is None:
            return
        self._conn.execute(
            "INSERT INTO gap (session_id, source, start_utc, end_utc) VALUES (?,?,?,?)",
            (self._session_id, gap.source, gap.start_utc, gap.end_utc),
        )
        self._conn.commit()

    def close_session(self, ended_utc: str) -> None:
        if self._conn is None:
            return
        self.flush()
        self._conn.execute(
            "UPDATE session SET ended_utc=? WHERE session_id=?",
            (ended_utc, self._session_id),
        )
        self._conn.commit()
        self._conn.close()
        self._conn = None
