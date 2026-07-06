"""Milestone-3-Test: zwei Quellen -> eine joinbare Session, Riot-Ende stoppt.

Beweist ohne Hardware/Match, dass HR-Strom (FakeSource) und Game-Events
(RiotSource via injiziertem Fetch) unter einer session_id auf einer Uhr in
derselben DB landen (der "Merge" = Join), und dass das GameEnd der Riot-Quelle
die Session beendet, obwohl die HR-Quelle endlos weiterläuft.
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
                # HR-Quelle läuft "endlos" (viele schnelle Ticks); soll NICHT das Ende bestimmen
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
            assert hr_count > 0, "keine HR-Samples trotz laufender HR-Quelle"
            assert event_types == ["ChampionKill", "GameEnd"]
            assert snap_count == 2

            # Der Merge: HR und Events teilen sich dieselbe session_id
            (hr_sid,) = conn.execute(
                "SELECT DISTINCT session_id FROM hr_sample").fetchone()
            (ev_sid,) = conn.execute(
                "SELECT DISTINCT session_id FROM game_event").fetchone()
            assert hr_sid == ev_sid == session_id

            # Session sauber geschlossen (Riot-Ende hat gestoppt, HR wurde gecancelt)
            (ended,) = conn.execute(
                "SELECT ended_utc FROM session WHERE session_id=?", (session_id,)).fetchone()
            assert ended is not None
        finally:
            conn.close()


if __name__ == "__main__":
    test_two_sources_one_session()
    print("OK - Merge-Test bestanden (zwei Quellen, eine Session)")
