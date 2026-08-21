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


# -- Battery display (EW-86 follow-up) -------------------------------------


def test_observable_carries_non_enum_values() -> None:
    """The battery level rides on the same mechanism as the recorder state.

    Uses equality rather than identity: percentages outside CPython's small-int
    cache would otherwise never compare equal and would re-notify endlessly.
    """
    from riftrec.rte.state import Observable

    seen: list = []
    obs = Observable(None)
    obs.subscribe(seen.append)
    obs.set(87)
    obs.set(87)          # unchanged -> no second notification
    obs.set(1000)
    obs.set(1000)
    obs.set(None)
    assert seen == [87, 1000, None]


def test_battery_text_wording() -> None:
    from riftrec.app.tray_icons import BATTERY_WARN_PCT, battery_text

    assert battery_text(None) == "Battery: unknown"
    assert battery_text(87) == "Battery: 87%"
    assert "replace soon" in battery_text(BATTERY_WARN_PCT)
    assert "replace soon" in battery_text(5)
    assert "replace soon" not in battery_text(BATTERY_WARN_PCT + 1)
