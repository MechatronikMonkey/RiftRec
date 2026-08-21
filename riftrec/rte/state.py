"""Observable recorder state.

The state sequence separates "connected & ready" from "actively recording" -
exactly the distinction the later tray icon (EW-38) should show. The core holds
the front-end-free truth; a UI attaches via a callback.
"""

from __future__ import annotations

import enum
from typing import Any, Callable


class RecorderState(enum.Enum):
    IDLE = "idle"
    CONNECTING = "connecting"
    READY = "ready"          # sources connected, waiting for match start
    RECORDING = "recording"  # match detected, writing data
    STOPPED = "stopped"
    ERROR = "error"


StateListener = Callable[[Any], None]


class Observable:
    """Minimal state holder with listener notification.

    Deliberately untyped in what it carries: besides the recorder state it also
    carries the strap's battery level, so the tray can show both without a
    second mechanism.
    """

    def __init__(self, initial: Any = RecorderState.IDLE) -> None:
        self._state = initial
        self._listeners: list[StateListener] = []

    @property
    def state(self) -> Any:
        return self._state

    def subscribe(self, listener: StateListener) -> None:
        self._listeners.append(listener)

    def set(self, state: Any) -> None:
        # Equality rather than identity: enums compare the same either way, but
        # a battery percentage would not be reliably identical.
        if state == self._state:
            return
        self._state = state
        for listener in self._listeners:
            listener(state)
