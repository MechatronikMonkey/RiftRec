"""Milestone-3 test: two sources -> one joinable session, Riot end stops it.

Proves without hardware/match that the HR stream (FakeSource) and game events
(RiotSource via injected fetch) land under one session_id on one clock in the
same DB (the "merge" = join), and that the RiotSource's GameEnd ends the session
even though the HR source keeps running forever.
"""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from pathlib import Path

from riftrec.rte.runtime import RecorderRuntime
from riftrec.sources.fake import FakeSource
from riftrec.sources.riot import RiotSource
from riftrec.storage.sqlite_sink import SqliteSink


def _scripted_fetch(frames: list):
    it = iter(frames)

    async def fetch():
        try:
            return next(it)
        except StopIteration:
            return None

    return fetch


def test_two_sources_one_session() -> None:
    kill = {"EventID": 1, "EventName": "ChampionKill", "EventTime": 30.0}
    frame1 = {"gameData": {"gameTime": 30.0}, "activePlayer": {"summonerName": "P"},
              "allPlayers": [{"summonerName": "P", "scores": {"kills": 1, "deaths": 0,
                              "assists": 0, "creepScore": 20}}],
              "events": {"Events": [kill]}}
    frame2 = {**frame1, "events": {"Events": [kill,
              {"EventID": 2, "EventName": "GameEnd", "EventTime": 31.0}]}}

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "merge.sqlite"
        sink = SqliteSink(db_path)
        runtime = RecorderRuntime(
            [
                # HR source runs "forever" (many fast ticks); must NOT decide the end
                FakeSource(ticks=10_000, tick_s=0.002),
                RiotSource(poll_interval_s=0.0, snapshot_interval_s=0.0,
                           fetch=_scripted_fetch([frame1, frame2])),
            ],
            sink,
            participant_id="P",
            session_index=1,
        )
        session_id = asyncio.run(asyncio.wait_for(runtime.run(), timeout=5.0))

        conn = sqlite3.connect(db_path)
        try:
            (hr_count,) = conn.execute("SELECT COUNT(*) FROM hr_sample").fetchone()
            event_types = [r[0] for r in conn.execute(
                "SELECT event_type FROM game_event ORDER BY event_id").fetchall()]
            (snap_count,) = conn.execute("SELECT COUNT(*) FROM game_snapshot").fetchone()
            assert hr_count > 0, "no HR samples despite a running HR source"
            assert event_types == ["ChampionKill", "GameEnd"]
            assert snap_count == 2

            # The merge: HR and events share the same session_id
            (hr_sid,) = conn.execute(
                "SELECT DISTINCT session_id FROM hr_sample").fetchone()
            (ev_sid,) = conn.execute(
                "SELECT DISTINCT session_id FROM game_event").fetchone()
            assert hr_sid == ev_sid == session_id

            # Session cleanly closed (Riot end stopped it, HR was cancelled)
            (ended,) = conn.execute(
                "SELECT ended_utc FROM session WHERE session_id=?", (session_id,)).fetchone()
            assert ended is not None
        finally:
            conn.close()


if __name__ == "__main__":
    test_two_sources_one_session()
    print("OK - merge test passed (two sources, one session)")
