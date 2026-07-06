"""Beobachtbarer Zustand des Recorders.

Die Zustandsfolge trennt "verbunden & bereit" von "nimmt aktiv auf" — genau
die Unterscheidung, die das spätere Tray-Icon (EW-38) anzeigen soll. Der
Kern hält die Front-End-freie Wahrheit; eine UI hängt sich per Callback dran.
"""

from __future__ import annotations

import enum
from typing import Callable


class RecorderState(enum.Enum):
    IDLE = "idle"
    CONNECTING = "connecting"
    READY = "ready"          # Quellen verbunden, wartet auf Match-Start
    RECORDING = "recording"  # Match erkannt, schreibt Daten
    STOPPED = "stopped"
    ERROR = "error"


StateListener = Callable[[RecorderState], None]


class Observable:
    """Minimaler Zustandshalter mit Listener-Benachrichtigung."""

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
