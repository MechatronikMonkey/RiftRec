"""Pipe-Test ohne Hardware: FakeSource -> Runtime -> SqliteSink -> SQLite.

Beweist, dass das Milestone-0-Gerüst eine vollständige Session erzeugt und die
Streams über die gemeinsame Uhr joinbar in einer DB landen. Läuft ohne H10 und
ohne LoL-Match. Startbar per `python -m pytest` ODER direkt `python tests/test_pipe.py`.
"""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from pathlib import Path

from riftrec import SCHEMA_VERSION
from riftrec.rte.runtime import RecorderRuntime
from riftrec.rte.state import RecorderState
from riftrec.sources.fake import FakeSource
from riftrec.storage.sqlite_sink import SqliteSink


def _run_session(db_path: Path) -> str:
    sink = SqliteSink(db_path)
    # Schnelle Ticks, damit der Test in ~0,1 s durchläuft.
    runtime = RecorderRuntime(
        [FakeSource(ticks=10, tick_s=0.01)],
        sink,
        participant_id="TEST01",
        session_index=1,
    )
    seen_states: list[RecorderState] = []
    runtime.status.subscribe(seen_states.append)
    session_id = asyncio.run(runtime.run())
    assert RecorderState.RECORDING in seen_states
    assert seen_states[-1] is RecorderState.STOPPED
    return session_id


def test_pipe() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "session.sqlite"
        session_id = _run_session(db_path)

        conn = sqlite3.connect(db_path)
        try:
            # Session-Kopf korrekt und abgeschlossen
            row = conn.execute(
                "SELECT participant_id, session_index, schema_version, "
                "ended_utc, mono_anchor_ns FROM session WHERE session_id=?",
                (session_id,),
            ).fetchone()
            assert row is not None, "keine Session-Zeile"
            participant_id, session_index, schema_version, ended_utc, mono_anchor = row
            assert participant_id == "TEST01"
            assert session_index == 1
            assert schema_version == SCHEMA_VERSION
            assert ended_utc is not None, "Session wurde nicht geschlossen"
            assert isinstance(mono_anchor, int)

            # Sample-Zahlen: 10 Ticks -> 10 HR + 10 RR; Events bei Tick 3,6,8 -> 3
            (hr_count,) = conn.execute("SELECT COUNT(*) FROM hr_sample").fetchone()
            (rr_count,) = conn.execute("SELECT COUNT(*) FROM rr_interval").fetchone()
            (ev_count,) = conn.execute("SELECT COUNT(*) FROM game_event").fetchone()
            assert hr_count == 10, hr_count
            assert rr_count == 10, rr_count
            assert ev_count == 3, ev_count

            # Join über die gemeinsame Session-ID muss funktionieren (der "Merge")
            (joined,) = conn.execute(
                "SELECT COUNT(*) FROM hr_sample h JOIN session s "
                "USING (session_id) WHERE s.session_id=?",
                (session_id,),
            ).fetchone()
            assert joined == 10

            # mono_ns streng aufsteigend (präzise Ordnung erhalten)
            monos = [r[0] for r in conn.execute(
                "SELECT mono_ns FROM hr_sample ORDER BY rowid"
            ).fetchall()]
            assert monos == sorted(monos)
            assert len(set(monos)) == len(monos)

            # Event fällt in das HR-Zeitfenster (Ausrichtungs-Sanity)
            (ev_mono,) = conn.execute(
                "SELECT mono_ns FROM game_event ORDER BY mono_ns LIMIT 1"
            ).fetchone()
            assert monos[0] <= ev_mono <= monos[-1]
        finally:
            conn.close()


if __name__ == "__main__":
    test_pipe()
    print("OK - Pipe-Test bestanden")
