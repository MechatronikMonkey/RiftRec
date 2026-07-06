"""FakeSource - synthetische Quelle, um die Pipe ohne Hardware zu prüfen.

Emittiert pro Tick einen HR-Wert plus das zugehörige RR-Intervall und streut
ein paar Game-Events ein. So lässt sich das gesamte Runtime→Sink→SQLite-
Zusammenspiel testen, ohne H10 oder laufendes LoL-Match.
"""

from __future__ import annotations

import asyncio
import json
import math

from ..clock import SessionClock
from ..model import GameEvent, HrSample, RrInterval
from .base import EmitFn

# Ein kleines Skript aus (Tick, EventTyp), an dem sich die Zeit-Ausrichtung
# später manuell prüfen lässt (Death sollte auf einen HR-Ausschlag fallen).
_SCRIPTED_EVENTS = {3: "ChampionKill", 6: "TurretKilled", 8: "ChampionKill"}


class FakeSource:
    name = "fake"

    def __init__(self, ticks: int = 10, tick_s: float = 1.0) -> None:
        self._ticks = ticks
        self._tick_s = tick_s

    async def run(self, emit: EmitFn, clock: SessionClock) -> None:
        for i in range(self._ticks):
            mono, utc = clock.now()
            # HR schwingt um 78 bpm, mit einem Ausschlag rund um die Events.
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
