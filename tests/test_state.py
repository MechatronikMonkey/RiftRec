"""EW-38 state derivation: READY (connected) vs RECORDING (match live)."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from riftrec.clock import SessionClock
from riftrec.model import GameEvent, HrSample
from riftrec.rte.runtime import RecorderRuntime
from riftrec.rte.state import RecorderState
from riftrec.sources.base import EmitFn
from riftrec.storage.sqlite_sink import SqliteSink


class _HrOnly:
    name = "hr-only"

    async def run(self, emit: EmitFn, clock: SessionClock) -> None:
        for _ in range(3):
            mono, utc = clock.now()
            emit(HrSample(mono_ns=mono, utc=utc, hr_bpm=75))
            await asyncio.sleep(0.005)


class _HrThenGame:
    name = "hr-then-game"

    async def run(self, emit: EmitFn, clock: SessionClock) -> None:
        mono, utc = clock.now()
        emit(HrSample(mono_ns=mono, utc=utc, hr_bpm=75))
        await asyncio.sleep(0.005)
        mono, utc = clock.now()
        emit(GameEvent(mono_ns=mono, utc=utc, game_time_s=10.0,
                       event_id=1, event_type="ChampionKill", payload_json="{}"))
        await asyncio.sleep(0.005)


def _seen_states(source) -> list[RecorderState]:
    with tempfile.TemporaryDirectory() as tmp:
        sink = SqliteSink(Path(tmp) / "s.sqlite")
        runtime = RecorderRuntime([source], sink, duration_s=0.2)
        seen: list[RecorderState] = []
        runtime.status.subscribe(seen.append)
        asyncio.run(runtime.run())
        return seen


def test_hr_only_reaches_ready_not_recording() -> None:
    seen = _seen_states(_HrOnly())
    assert RecorderState.READY in seen
    assert RecorderState.RECORDING not in seen
    assert seen[-1] is RecorderState.STOPPED


def test_game_data_reaches_recording() -> None:
    seen = _seen_states(_HrThenGame())
    # Physio first -> READY, then game data -> RECORDING (in order)
    assert seen.index(RecorderState.READY) < seen.index(RecorderState.RECORDING)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK - {name}")
    print("OK - all state tests passed")
