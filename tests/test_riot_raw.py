"""Raw game channel + pseudonymisation of foreign players (EW-86).

The fixtures here are synthetic on purpose. RiftRec is a public repository and
a real `allgamedata` capture carries the Riot IDs of nine people who never
consented to anything - see the data protection note in EW-86.
"""

from __future__ import annotations

import asyncio
import json

from riftrec.clock import SessionClock
from riftrec.model import GameEvent, GameRaw
from riftrec.sources.riot import (
    RiotSource,
    apply_pseudonyms,
    build_pseudonym_map,
    compress_game_data,
    decompress_game_data,
)


def _frame(game_time: float = 10.0, events: list | None = None) -> dict:
    """A minimal but structurally faithful allgamedata response."""
    return {
        "gameData": {"gameTime": game_time, "gameMode": "CLASSIC", "mapName": "Map11"},
        "activePlayer": {
            "summonerName": "Me",
            "riotId": "Me#EUW",
            "currentGold": 500.0,
            "level": 6,
        },
        "allPlayers": [
            {
                "summonerName": "Me",
                "riotId": "Me#EUW",
                "riotIdGameName": "Me",
                "riotIdTagLine": "EUW",
                "championName": "Ahri",
                "position": "MIDDLE",
                "team": "ORDER",
                "level": 6,
                "respawnTimer": 0.0,
                "scores": {"kills": 2, "deaths": 1, "assists": 3, "creepScore": 80},
            },
            {
                "summonerName": "Rival",
                "riotId": "Rival#EUW",
                "riotIdGameName": "Rival",
                "riotIdTagLine": "EUW",
                "championName": "Zed",
                "position": "MIDDLE",
                "team": "CHAOS",
                "level": 7,
                "respawnTimer": 34.5,
                "scores": {"kills": 4, "deaths": 0, "assists": 1, "creepScore": 95},
            },
        ],
        "events": {"Events": events or []},
    }


# -- Pseudonymisation ------------------------------------------------------


def test_foreign_names_are_replaced_own_name_is_kept() -> None:
    data = _frame()
    mapping = build_pseudonym_map(data, salt="session-1")
    cleaned = apply_pseudonyms(data, mapping)

    own, rival = cleaned["allPlayers"]
    assert own["summonerName"] == "Me"          # kept: RiftLab needs it
    assert own["riotIdTagLine"] == "EUW"
    assert rival["summonerName"].startswith("p_")
    assert rival["riotId"].startswith("p_")
    assert rival["riotIdTagLine"] == ""          # bare tag would still identify


def test_gameplay_fields_survive_pseudonymisation() -> None:
    """The whole point of EW-86: champion, position and team must remain."""
    data = _frame()
    cleaned = apply_pseudonyms(data, build_pseudonym_map(data, salt="s"))
    rival = cleaned["allPlayers"][1]
    assert rival["championName"] == "Zed"
    assert rival["position"] == "MIDDLE"
    assert rival["team"] == "CHAOS"
    assert rival["respawnTimer"] == 34.5         # inclusion criterion for HRR30
    assert rival["scores"]["kills"] == 4


def test_same_name_maps_consistently_across_payloads() -> None:
    """Events and raw payloads must agree, or kill attribution breaks."""
    data = _frame()
    mapping = build_pseudonym_map(data, salt="s")
    event = {"EventName": "ChampionKill", "KillerName": "Rival", "VictimName": "Me"}
    cleaned_event = apply_pseudonyms(event, mapping)
    cleaned_player = apply_pseudonyms(data, mapping)["allPlayers"][1]
    assert cleaned_event["KillerName"] == cleaned_player["summonerName"]
    assert cleaned_event["VictimName"] == "Me"


def test_pseudonyms_differ_between_sessions() -> None:
    data = _frame()
    a = build_pseudonym_map(data, salt="session-a")["Rival"]
    b = build_pseudonym_map(data, salt="session-b")["Rival"]
    assert a != b


def test_non_player_names_are_left_alone() -> None:
    """Turret and minion identifiers stay readable."""
    mapping = build_pseudonym_map(_frame(), salt="s")
    event = {"EventName": "TurretKilled", "TurretKilled": "Turret_T1_C_05_A", "KillerName": "Rival"}
    cleaned = apply_pseudonyms(event, mapping)
    assert cleaned["TurretKilled"] == "Turret_T1_C_05_A"
    assert cleaned["KillerName"].startswith("p_")


# -- Compression round trip ------------------------------------------------


def test_compress_roundtrip_and_size() -> None:
    data = _frame()
    mapping = build_pseudonym_map(data, salt="s")
    blob = compress_game_data(data, mapping)
    restored = decompress_game_data(blob)
    assert restored["allPlayers"][1]["championName"] == "Zed"
    assert len(blob) < len(json.dumps(data).encode("utf-8"))


# -- Source behaviour ------------------------------------------------------


def _scripted(frames: list):
    it = iter(frames)

    async def fetch():
        try:
            return next(it)
        except StopIteration:
            return None

    return fetch


def _drive(source: RiotSource) -> list:
    emitted: list = []
    asyncio.run(source.run(emitted.append, SessionClock()))
    return emitted


def test_first_poll_is_always_stored_raw() -> None:
    source = RiotSource(poll_interval_s=0, raw_interval_s=3600, fetch=_scripted([_frame()]))
    raws = [r for r in _drive(source) if isinstance(r, GameRaw)]
    assert len(raws) == 1
    assert decompress_game_data(raws[0].payload_zlib)["gameData"]["gameMode"] == "CLASSIC"


def test_raw_is_throttled_between_polls() -> None:
    """Ten polls with a long raw interval must not produce ten raw rows."""
    frames = [_frame(game_time=float(i)) for i in range(10)]
    source = RiotSource(poll_interval_s=0, raw_interval_s=3600, fetch=_scripted(frames))
    raws = [r for r in _drive(source) if isinstance(r, GameRaw)]
    assert len(raws) == 1


def test_final_frame_is_stored_on_game_end() -> None:
    """The last scoreboard must survive even if the interval has not elapsed."""
    end = _frame(game_time=99.0, events=[{"EventID": 1, "EventName": "GameEnd"}])
    source = RiotSource(
        poll_interval_s=0, raw_interval_s=3600, fetch=_scripted([_frame(), end])
    )
    raws = [r for r in _drive(source) if isinstance(r, GameRaw)]
    assert len(raws) == 2
    assert decompress_game_data(raws[-1].payload_zlib)["gameData"]["gameTime"] == 99.0


def test_event_payloads_are_pseudonymised() -> None:
    kill = {"EventID": 1, "EventName": "ChampionKill", "KillerName": "Rival", "VictimName": "Me"}
    source = RiotSource(poll_interval_s=0, fetch=_scripted([_frame(events=[kill])]))
    events = [r for r in _drive(source) if isinstance(r, GameEvent)]
    payload = json.loads(events[0].payload_json)
    assert payload["KillerName"].startswith("p_")
    assert payload["VictimName"] == "Me"


# -- Death timer at snapshot resolution (EW-61 / P1) -----------------------


def test_snapshot_carries_death_state() -> None:
    """The pre-registered analysis needs the respawn timer at the moment of
    death. game_raw samples every 30 s and misses most timers (10-50 s), so the
    value has to ride on the 5 s snapshot instead."""
    from riftrec.sources.riot import extract_snapshot

    data = _frame()
    data["allPlayers"][0]["isDead"] = True
    data["allPlayers"][0]["respawnTimer"] = 34.5
    snap = extract_snapshot(data, mono_ns=1, utc="t")
    assert snap.is_dead is True
    assert snap.respawn_timer_s == 34.5


def test_snapshot_death_state_absent_is_none() -> None:
    """Fields missing from the API must not turn into False/0.0 - that would be
    indistinguishable from 'alive with no timer' in the analysis."""
    from riftrec.sources.riot import extract_snapshot

    data = _frame()
    data["allPlayers"][0].pop("isDead", None)
    data["allPlayers"][0].pop("respawnTimer", None)
    snap = extract_snapshot(data, mono_ns=1, utc="t")
    assert snap.is_dead is None
    assert snap.respawn_timer_s is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK - {name}")
    print("OK - all raw/pseudonymisation tests passed")


def test_one_pseudonym_per_person_across_every_spelling() -> None:
    """The regression from the 28.08. test match: `riotId` ("Rival#EUW") and
    `riotIdGameName` ("Rival") were hashed separately, so the same player
    appeared as one pseudonym in game_raw and a different one in game_event -
    whose Assisters carry the bare game name. An assist could then not be
    matched to the scoreboard row of the player who made it, and that linkage
    cannot be repaired afterwards: the salt is session-local and the plaintext
    is never stored.
    """
    mapping = build_pseudonym_map(_frame(), salt="s")
    ids = {mapping[name] for name in ("Rival", "Rival#EUW")}
    assert len(ids) == 1, mapping


def test_event_payloads_and_scoreboard_agree_on_the_same_player() -> None:
    """End to end: the two channels have to name the same person the same way."""
    data = _frame()
    mapping = build_pseudonym_map(data, salt="s")

    scoreboard = apply_pseudonyms(data, mapping)["allPlayers"][1]["riotId"]
    event = apply_pseudonyms(
        {"EventName": "ChampionKill", "KillerName": "Rival", "Assisters": ["Rival"]},
        mapping,
    )
    assert event["KillerName"] == scoreboard
    assert event["Assisters"] == [scoreboard]


def test_the_recording_player_is_still_never_pseudonymised() -> None:
    """Guard against the fix over-reaching: RiftLab splits own from enemy
    events on this name."""
    mapping = build_pseudonym_map(_frame(), salt="s")
    for spelling in ("Me", "Me#EUW"):
        assert spelling not in mapping
