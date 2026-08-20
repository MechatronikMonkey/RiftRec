"""FakeSource - synthetic source to exercise the pipe without hardware.

Emits one HR value plus its RR interval per tick and sprinkles in a few game
events. Lets us test the whole runtime->sink->SQLite interplay without an H10
or a running LoL match.

Since EW-86 it also fills the raw channels (`hr_raw`, `game_raw`) with
synthetic but structurally faithful payloads, so the full schema can be
exercised offline. That matters twice over: the raw path is otherwise only
reachable with real hardware, and RiftRec is a public repository where real
captures must never be committed - they carry the Riot IDs of nine people who
never consented (see the data protection note in EW-86).
"""

from __future__ import annotations

import asyncio
import json
import math

from ..clock import SessionClock
from ..model import GameEvent, GameRaw, HrRaw, HrSample, RrInterval
from .base import EmitFn
from .riot import build_pseudonym_map, compress_game_data

# A small script of (tick, event type) so the time alignment can later be
# checked by hand (a death should land on an HR spike).
_SCRIPTED_EVENTS = {3: "ChampionKill", 6: "TurretKilled", 8: "ChampionKill"}

# Invented players. Deliberately obvious placeholders - never real Riot IDs.
_OWN_NAME = "TestSubject"
_FOE_NAME = "TestOpponent"


def _hr_payload(hr: int, rr_ms: float, contact: bool) -> bytes:
    """Build a spec-shaped 0x2A37 notification: contact supported + RR present."""
    flags = 0x10 | 0x04 | (0x02 if contact else 0x00)
    rr_units = int(round(rr_ms * 1024.0 / 1000.0))
    return bytes([flags, hr & 0xFF, rr_units & 0xFF, (rr_units >> 8) & 0xFF])


def _game_frame(tick: int, hr: int) -> dict:
    """A minimal allgamedata-shaped response with the fields EW-61 depends on."""
    return {
        "gameData": {"gameTime": float(tick), "gameMode": "CLASSIC", "mapName": "Map11"},
        "activePlayer": {"summonerName": _OWN_NAME, "riotId": f"{_OWN_NAME}#TEST",
                         "currentGold": 200.0 + 30 * tick, "level": 1 + tick // 3},
        "allPlayers": [
            {"summonerName": _OWN_NAME, "riotId": f"{_OWN_NAME}#TEST",
             "riotIdGameName": _OWN_NAME, "riotIdTagLine": "TEST",
             "championName": "Ahri", "position": "MIDDLE", "team": "ORDER",
             "level": 1 + tick // 3, "respawnTimer": 0.0,
             "scores": {"kills": 1, "deaths": tick // 4, "assists": 2,
                        "creepScore": 10 * tick}},
            {"summonerName": _FOE_NAME, "riotId": f"{_FOE_NAME}#TEST",
             "riotIdGameName": _FOE_NAME, "riotIdTagLine": "TEST",
             "championName": "Zed", "position": "MIDDLE", "team": "CHAOS",
             "level": 1 + tick // 3, "respawnTimer": 32.0 if tick % 5 == 0 else 0.0,
             "scores": {"kills": tick // 4, "deaths": 1, "assists": 0,
                        "creepScore": 11 * tick}},
        ],
        "events": {"Events": []},
    }


class FakeSource:
    name = "fake"

    def __init__(self, ticks: int = 10, tick_s: float = 1.0, raw_every: int = 5) -> None:
        self._ticks = ticks
        self._tick_s = tick_s
        self._raw_every = raw_every

    async def run(self, emit: EmitFn, clock: SessionClock) -> None:
        pseudonyms: dict[str, str] = {}
        for i in range(self._ticks):
            mono, utc = clock.now()
            # HR oscillates around 78 bpm, with a bump around the events.
            hr = 78 + int(12 * math.sin(i / 2.0))
            rr_ms = 60000.0 / hr
            # Drop skin contact for one tick so the discard criterion has
            # something to find in a synthetic file.
            contact = i != 7
            emit(HrRaw(mono_ns=mono, utc=utc, payload=_hr_payload(hr, rr_ms, contact)))
            emit(HrSample(mono_ns=mono, utc=utc, hr_bpm=hr, contact=contact))
            emit(RrInterval(mono_ns=mono, utc=utc, rr_ms=rr_ms))

            frame = _game_frame(i, hr)
            if not pseudonyms:
                pseudonyms = build_pseudonym_map(frame, clock.started_utc)

            etype = _SCRIPTED_EVENTS.get(i)
            if etype is not None:
                emit(GameEvent(
                    mono_ns=mono, utc=utc, game_time_s=float(i),
                    event_id=i, event_type=etype,
                    payload_json=json.dumps({"EventID": i, "EventName": etype}),
                ))
            if i % self._raw_every == 0:
                emit(GameRaw(
                    mono_ns=mono, utc=utc, game_time_s=float(i),
                    payload_zlib=compress_game_data(frame, pseudonyms),
                ))
            await asyncio.sleep(self._tick_s)
