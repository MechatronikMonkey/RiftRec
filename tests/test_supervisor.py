"""EW-38 supervisor: multi-match auto-sessions + per-session notes.

Drives the synchronous session-management methods directly (no hardware, no
match, no async timing) and checks the resulting SQLite file.
"""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from pathlib import Path

from riftrec.config import RecorderConfig
from riftrec.rte.supervisor import SupervisorService
from riftrec.storage import sqlite_sink as _sink_mod


def _hr(bpm: int) -> bytes:
    return bytes([0x00, bpm])  # flags=0 (uint8 HR, no RR)


class _FakeTransport:
    """No-op BLE transport so run() can be driven without hardware."""

    async def connect(self, device) -> None:
        pass

    async def subscribe(self, uuid, callback) -> None:
        self.callback = callback

    async def disconnect(self) -> None:
        pass


def _riot_frame(kill_id: int) -> dict:
    return {
        "gameData": {"gameTime": 30.0},
        "activePlayer": {"summonerName": "P"},
        "allPlayers": [{"summonerName": "P",
                        "scores": {"kills": 1, "deaths": 0, "assists": 0, "creepScore": 20}}],
        "events": {"Events": [{"EventID": kill_id, "EventName": "ChampionKill", "EventTime": 30.0}]},
    }


def test_two_matches_accumulate_in_one_file_with_notes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "sitting.sqlite"
        svc = SupervisorService(RecorderConfig(participant_id="P01", db_path=db,
                                               snapshot_interval_s=0))

        # Match 1
        sid1 = svc._open_session()
        svc._on_hr(_hr(80))
        svc._record_riot(_riot_frame(1))
        assert svc.add_note("felt tilted after that gank") is True
        svc._close_session()

        # Between matches: HR is discarded (no open session)
        svc._on_hr(_hr(70))
        # A note between matches attaches to the just-finished session
        assert svc.add_note("post-game: bad connection") is True

        # Match 2
        sid2 = svc._open_session()
        svc._on_hr(_hr(82))
        svc._close_session()

        conn = sqlite3.connect(db)
        try:
            sessions = conn.execute(
                "SELECT session_id, session_index, notes, active_riot_id FROM session "
                "ORDER BY session_index"
            ).fetchall()
            assert [s[1] for s in sessions] == [1, 2]
            assert sessions[0][0] == sid1 and sessions[1][0] == sid2

            # Match 1 saw a Riot poll -> active_riot_id captured; match 2 never
            # received a Riot frame (only HR) -> stays unset.
            assert sessions[0][3] == "P"
            assert sessions[1][3] is None

            # HR: one per match; the between-match sample was discarded -> 2 total
            (hr_total,) = conn.execute("SELECT COUNT(*) FROM hr_sample").fetchone()
            assert hr_total == 2
            (hr1,) = conn.execute("SELECT COUNT(*) FROM hr_sample WHERE session_id=?", (sid1,)).fetchone()
            (hr2,) = conn.execute("SELECT COUNT(*) FROM hr_sample WHERE session_id=?", (sid2,)).fetchone()
            assert hr1 == 1 and hr2 == 1

            # Event went to match 1
            (ev1,) = conn.execute("SELECT COUNT(*) FROM game_event WHERE session_id=?", (sid1,)).fetchone()
            assert ev1 == 1

            # Both notes attached to session 1 (two lines)
            notes1 = sessions[0][2]
            assert "felt tilted" in notes1 and "bad connection" in notes1
            assert notes1.count("\n") == 1
        finally:
            conn.close()


def test_add_note_without_session_returns_false() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        svc = SupervisorService(RecorderConfig(db_path=Path(tmp) / "x.sqlite"))
        assert svc.add_note("nothing recorded yet") is False


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK - {name}")
    print("OK - all supervisor tests passed")
