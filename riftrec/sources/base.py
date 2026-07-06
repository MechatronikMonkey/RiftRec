"""SignalSource-Protocol.

Eine Quelle ist ein async Task. Sie stempelt jeden Record über die injizierte
`SessionClock` bei Ankunft der Daten und übergibt ihn per `emit` an die
Runtime-Queue. `emit` ist nicht-blockierend (put_nowait auf unbeschränkter
Queue), damit ein BLE-Notify-Callback nie blockiert. `run` läuft bis zum
Abbruch (Task-Cancel) oder bis die Quelle natürlich endet.
"""

from __future__ import annotations

from typing import Callable, Protocol

from ..clock import SessionClock
from ..model import Record

# Von der Runtime bereitgestellt; typischerweise queue.put_nowait.
EmitFn = Callable[[Record], None]


class SignalSource(Protocol):
    name: str

    async def run(self, emit: EmitFn, clock: SessionClock) -> None: ...
