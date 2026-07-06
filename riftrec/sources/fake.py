"""FakeSource - synthetic source to exercise the pipe without hardware.

Emits one HR value plus its RR interval per tick and sprinkles in a few game
events. Lets us test the whole runtime->sink->SQLite interplay without an H10
or a running LoL match.
"""

from __future__ import annotations

import asyncio
import json
import math

from ..clock import SessionClock
from ..model import GameEvent, HrSample, RrInterval
from .base import EmitFn

# A small script of (tick, event type) so the time alignment can later be
# checked by hand (a death should land on an HR spike).
_SCRIPTED_EVENTS = {3: "ChampionKill", 6: "TurretKilled", 8: "ChampionKill"}


class FakeSource:
    name = "fake"

    def __init__(self, ticks: int = 10, tick_s: float = 1.0) -> None:
        self._ticks = ticks
        self._tick_s = tick_s

    async def run(self, emit: EmitFn, clock: SessionClock) -> None:
        for i in range(self._ticks):
            mono, utc = clock.now()
            # HR oscillates around 78 bpm, with a bump around the events.
            hr = 78 + int(12 * math.sin(i / 2.0))
            emit(HrSample(mono_ns=mono, utc=utc, hr_bpm=hr))
            emit(RrInterval(mono_ns=mono, utc=utc, rr_ms=60000.0 / hr))
            etype = _SCRIPTED_EVENTS.get(i)
            if etype is not None:
                emit(GameEvent(
                    mono_ns=mono, utc=utc, game_time_s=float(i),
                    event_id=i, event_type=etype,
                    payload_json=json.dumps({"EventID": i, "EventName": etype}),
                ))
            await asyncio.sleep(self._tick_s)
