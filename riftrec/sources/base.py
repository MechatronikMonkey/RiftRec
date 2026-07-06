"""SignalSource protocol.

A source is an async task. It timestamps every record via the injected
`SessionClock` when the data arrives and hands it to the runtime queue via
`emit`. `emit` is non-blocking (put_nowait on an unbounded queue) so a BLE
notify callback never blocks. `run` runs until cancelled (task cancel) or until
the source ends naturally.
"""

from __future__ import annotations

from typing import Callable, Protocol

from ..clock import SessionClock
from ..model import Record

# Provided by the runtime; typically queue.put_nowait.
EmitFn = Callable[[Record], None]


class SignalSource(Protocol):
    name: str

    async def run(self, emit: EmitFn, clock: SessionClock) -> None: ...
