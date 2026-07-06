"""Hardware-free pipe test: FakeSource -> Runtime -> SqliteSink -> SQLite.

Proves that the Milestone-0 skeleton produces a complete session and that the
streams land joinable in one DB on the shared clock. Runs without an H10 and
without a LoL match. Runnable via `python -m pytest` OR directly `python tests/test_pipe.py`.
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
    # Fast ticks so the test runs in ~0.1 s.
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
            # Session header correct and closed
            row = conn.execute(
                "SELECT participant_id, session_index, schema_version, "
                "ended_utc, mono_anchor_ns FROM session WHERE session_id=?",
                (session_id,),
            ).fetchone()
            assert row is not None, "no session row"
            participant_id, session_index, schema_version, ended_utc, mono_anchor = row
            assert participant_id == "TEST01"
            assert session_index == 1
            assert schema_version == SCHEMA_VERSION
            assert ended_utc is not None, "session was not closed"
            assert isinstance(mono_anchor, int)

            # Sample counts: 10 ticks -> 10 HR + 10 RR; events at ticks 3,6,8 -> 3
            (hr_count,) = conn.execute("SELECT COUNT(*) FROM hr_sample").fetchone()
            (rr_count,) = conn.execute("SELECT COUNT(*) FROM rr_interval").fetchone()
            (ev_count,) = conn.execute("SELECT COUNT(*) FROM game_event").fetchone()
            assert hr_count == 10, hr_count
            assert rr_count == 10, rr_count
            assert ev_count == 3, ev_count

            # Join over the shared session id must work (the "merge")
            (joined,) = conn.execute(
                "SELECT COUNT(*) FROM hr_sample h JOIN session s "
                "USING (session_id) WHERE s.session_id=?",
                (session_id,),
            ).fetchone()
            assert joined == 10

            # mono_ns strictly increasing (precise ordering preserved)
            monos = [r[0] for r in conn.execute(
                "SELECT mono_ns FROM hr_sample ORDER BY rowid"
            ).fetchall()]
            assert monos == sorted(monos)
            assert len(set(monos)) == len(monos)

            # Event falls within the HR time window (alignment sanity)
            (ev_mono,) = conn.execute(
                "SELECT mono_ns FROM game_event ORDER BY mono_ns LIMIT 1"
            ).fetchone()
            assert monos[0] <= ev_mono <= monos[-1]
        finally:
            conn.close()


if __name__ == "__main__":
    test_pipe()
    print("OK - pipe test passed")
