"""SQLite session sink (WAL).

Buffers records per table and writes them in batches via executemany. WAL mode
ensures that committed data survives a crash or a BLE dropout mid-session
(EW-39). One sink writes exactly one session into exactly one .sqlite file; that
file is the contract to RiftLab.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..model import (
    DeviceInfo, Gap, GameEvent, GameRaw, GameSnapshot, HrRaw, HrSample, Record,
    RrInterval, SessionMeta,
)

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def unique_db_path(folder: str | Path, participant: str | None = None) -> Path:
    """Return a collision-proof .sqlite path inside `folder` for one recording run.

    Named by participant (or "session") plus a local timestamp down to the
    second. If a file with that name already exists (e.g. two runs started in
    the same second), a numeric suffix is appended until the name is free - so
    a previous recording is NEVER reused or overwritten. Matches within a single
    run still accumulate into this one file (session_index increments); only a
    fresh run gets a fresh file.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    stem = (participant or "").strip() or "session"
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", stem)  # filesystem-safe
    base = f"{stem}_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}"
    candidate = folder / f"{base}.sqlite"
    n = 2
    while candidate.exists():
        candidate = folder / f"{base}_{n}.sqlite"
        n += 1
    return candidate


def discard_if_unused(db_path: str | Path) -> bool:
    """Delete `db_path` when no session was ever written into it (EW-89).

    A run that ends before a match starts - the usual mis-start: strap not
    put on, game never launched - can leave a .sqlite behind that holds
    nothing. Over weeks of a study those accumulate in the participant's
    folder and make it impossible to tell which files are worth sending back.

    Deliberately conservative: only a file that provably contains zero
    sessions is removed. A file with sessions, an unreadable one, a locked
    one - all are left alone. Nothing recorded is ever deleted (EW-52).

    Returns True if a file was removed.
    """
    path = Path(db_path)
    if not path.exists():
        return False
    try:
        conn = sqlite3.connect(path, timeout=2.0)
        try:
            has_table = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='table' AND name='session'"
            ).fetchone()[0]
            if has_table:
                sessions = conn.execute(
                    "SELECT COUNT(*) FROM session"
                ).fetchone()[0]
                if sessions:
                    return False
        finally:
            conn.close()
    except sqlite3.Error:
        return False  # cannot prove it is empty -> keep it
    try:
        path.unlink()
    except OSError:
        return False
    for suffix in ("-wal", "-shm"):
        try:
            path.with_name(path.name + suffix).unlink(missing_ok=True)
        except OSError:
            pass
    return True


def append_session_note(db_path: str | Path, session_id: str, text: str) -> None:
    """Append a timestamped free-text note to a session's `notes` column.

    Uses its own short-lived connection so it works both for the currently
    recording session and for an already-closed one (EW-38 note feature).
    """
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
    line = f"[{stamp}] {text}"
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        conn.execute(
            "UPDATE session SET notes = CASE WHEN notes IS NULL OR notes = '' "
            "THEN ? ELSE notes || char(10) || ? END WHERE session_id = ?",
            (line, line, session_id),
        )
        conn.commit()
    finally:
        conn.close()


class SqliteSink:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._session_id: str | None = None
        # Buffer per target table: list of value tuples for executemany.
        self._buf: dict[str, list[tuple]] = {
            "hr_sample": [],
            "rr_interval": [],
            "game_event": [],
            "game_snapshot": [],
            "hr_raw": [],
            "game_raw": [],
            "device_info": [],
        }

    # -- Lifecycle ---------------------------------------------------------

    def open_session(self, meta: SessionMeta) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        # Additive migration: a `session` table created before active_riot_id
        # existed (schema.sql's CREATE TABLE IF NOT EXISTS won't add it).
        cols = {row[1] for row in conn.execute("PRAGMA table_info(session)")}
        if "active_riot_id" not in cols:
            conn.execute("ALTER TABLE session ADD COLUMN active_riot_id TEXT")
        # Same for the sensor contact column added with schema version 2 (EW-86).
        hr_cols = {row[1] for row in conn.execute("PRAGMA table_info(hr_sample)")}
        if "contact" not in hr_cols:
            conn.execute("ALTER TABLE hr_sample ADD COLUMN contact INTEGER")
        # Schema version 4: death state on the snapshot table (EW-61).
        snap_cols = {row[1] for row in conn.execute("PRAGMA table_info(game_snapshot)")}
        if "is_dead" not in snap_cols:
            conn.execute("ALTER TABLE game_snapshot ADD COLUMN is_dead INTEGER")
        if "respawn_timer_s" not in snap_cols:
            conn.execute("ALTER TABLE game_snapshot ADD COLUMN respawn_timer_s REAL")
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
            contact = None if record.contact is None else int(record.contact)
            self._buf["hr_sample"].append(
                (sid, record.mono_ns, record.utc, record.hr_bpm, contact)
            )
        elif isinstance(record, DeviceInfo):
            self._buf["device_info"].append((
                sid, record.source, record.utc, record.address, record.name,
                record.manufacturer, record.model, record.serial,
                record.hardware_rev, record.firmware_rev, record.software_rev,
                record.battery_pct,
            ))
        elif isinstance(record, HrRaw):
            self._buf["hr_raw"].append((sid, record.mono_ns, record.utc, record.payload))
        elif isinstance(record, GameRaw):
            self._buf["game_raw"].append((
                sid, record.mono_ns, record.utc, record.game_time_s, record.payload_zlib,
            ))
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
                None if record.is_dead is None else int(record.is_dead),
                record.respawn_timer_s,
            ))
        else:  # pragma: no cover - guard against accidentally new record types
            raise TypeError(f"Unknown record type: {type(record).__name__}")

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

    def set_active_riot_id(self, riot_id: str) -> None:
        """Record the recording player's Riot Name#TAG, once known (EW-41-adjacent).

        Kept separate from the pseudonymous `participant_id`; used by RiftLab to
        split kill/death/assist from enemy events instead of "Enemy kills" only.
        """
        if self._conn is None:
            return
        self._conn.execute(
            "UPDATE session SET active_riot_id=? WHERE session_id=?",
            (riot_id, self._session_id),
        )
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
