"""Observable recorder state.

The state sequence separates "connected & ready" from "actively recording" -
exactly the distinction the later tray icon (EW-38) should show. The core holds
the front-end-free truth; a UI attaches via a callback.
"""

from __future__ import annotations

import enum
from typing import Callable


class RecorderState(enum.Enum):
    IDLE = "idle"
    CONNECTING = "connecting"
    READY = "ready"          # sources connected, waiting for match start
    RECORDING = "recording"  # match detected, writing data
    STOPPED = "stopped"
    ERROR = "error"


StateListener = Callable[[RecorderState], None]


class Observable:
    """Minimal state holder with listener notification."""

    def __init__(self, initial: RecorderState = RecorderState.IDLE) -> None:
        self._state = initial
        self._listeners: list[StateListener] = []

    @property
    def state(self) -> RecorderState:
        return self._state

    def subscribe(self, listener: StateListener) -> None:
        self._listeners.append(listener)

    def set(self, state: RecorderState) -> None:
        if state is self._state:
            return
        self._state = state
        for listener in self._listeners:
            listener(state)
