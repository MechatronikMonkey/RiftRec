"""Milestone-2-Tests ohne laufendes Match: RiotSource via injiziertem Fetch."""

from __future__ import annotations

import asyncio

from riftrec.clock import SessionClock
from riftrec.model import GameEvent, GameSnapshot
from riftrec.sources.riot import RiotSource, extract_snapshot, new_events


def test_new_events_dedup() -> None:
    events = [{"EventID": 0, "EventName": "GameStart"},
              {"EventID": 1, "EventName": "ChampionKill"},
              {"EventID": 2, "EventName": "TurretKilled"}]
    assert [e["EventID"] for e in new_events(events, None)] == [0, 1, 2]
    assert [e["EventID"] for e in new_events(events, 0)] == [1, 2]
    assert new_events(events, 2) == []


def test_extract_snapshot_matches_active_player() -> None:
    data = {
        "gameData": {"gameTime": 123.4},
        "activePlayer": {"summonerName": "Faker", "currentGold": 1500.0, "level": 7},
        "allPlayers": [
            {"summonerName": "Other", "level": 6, "scores": {"kills": 0}},
            {"summonerName": "Faker", "level": 7,
             "scores": {"kills": 3, "deaths": 1, "assists": 5, "creepScore": 88}},
        ],
    }
    snap = extract_snapshot(data, mono_ns=10, utc="t")
    assert (snap.kills, snap.deaths, snap.assists, snap.cs) == (3, 1, 5, 88)
    assert snap.gold == 1500.0 and snap.level == 7 and snap.game_time_s == 123.4


def _scripted_fetch(frames: list):
    """frames: Liste von dicts oder None; nach Erschöpfung immer None."""
    it = iter(frames)

    async def fetch():
        try:
            return next(it)
        except StopIteration:
            return None

    return fetch


def test_riotsource_emits_events_and_snapshot_then_ends() -> None:
    kill = {"EventID": 1, "EventName": "ChampionKill", "EventTime": 65.0,
            "KillerName": "Faker", "VictimName": "Other"}
    frame1 = {
        "gameData": {"gameTime": 65.0},
        "activePlayer": {"summonerName": "Faker", "currentGold": 900.0, "level": 5},
        "allPlayers": [{"summonerName": "Faker", "level": 5,
                        "scores": {"kills": 1, "deaths": 0, "assists": 0, "creepScore": 40}}],
        "events": {"Events": [{"EventID": 0, "EventName": "GameStart", "EventTime": 0.0}, kill]},
    }
    # Zweiter Frame: gleiche Events (dürfen nicht doppelt kommen) + GameEnd -> Quelle endet
    frame2 = {
        "gameData": {"gameTime": 66.0},
        "activePlayer": {"summonerName": "Faker", "currentGold": 950.0, "level": 5},
        "allPlayers": frame1["allPlayers"],
        "events": {"Events": frame1["events"]["Events"] + [
            {"EventID": 2, "EventName": "GameEnd", "EventTime": 66.0}]},
    }

    source = RiotSource(poll_interval_s=0.0, snapshot_interval_s=0.0,
                        fetch=_scripted_fetch([frame1, frame2]))
    clock = SessionClock()
    emitted: list = []
    asyncio.run(asyncio.wait_for(source.run(emitted.append, clock), timeout=2.0))

    events = [r for r in emitted if isinstance(r, GameEvent)]
    snaps = [r for r in emitted if isinstance(r, GameSnapshot)]
    # GameStart, ChampionKill, GameEnd - jeweils genau einmal (Dedup über Polls)
    assert [e.event_type for e in events] == ["GameStart", "ChampionKill", "GameEnd"]
    assert [e.event_id for e in events] == [0, 1, 2]
    # snapshot_interval_s=0 -> je Frame ein Snapshot
    assert len(snaps) == 2
    assert snaps[0].kills == 1


def test_riotsource_waits_when_no_match_then_ends_when_gone() -> None:
    # Erst kein Match (None), dann ein Frame, dann weg -> sauberes Ende
    frame = {"gameData": {"gameTime": 1.0}, "activePlayer": {}, "allPlayers": [],
             "events": {"Events": [{"EventID": 0, "EventName": "GameStart", "EventTime": 0.0}]}}
    source = RiotSource(poll_interval_s=0.0, snapshot_interval_s=999,
                        fetch=_scripted_fetch([None, frame]))
    emitted: list = []
    asyncio.run(asyncio.wait_for(source.run(emitted.append, SessionClock()), timeout=2.0))
    assert [r.event_type for r in emitted if isinstance(r, GameEvent)] == ["GameStart"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK - {name}")
    print("OK - alle Riot-Tests bestanden")
