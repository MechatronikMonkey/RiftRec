"""Gemeinsame Session-Uhr für alle Quellen.

Beim Anlegen wird ein Anker aus Wall-Clock (UTC) und Monotonic-Uhr
festgehalten. `now()` leitet den UTC-Zeitstempel aus dem Anker plus der
seither vergangenen Monotonic-Zeit ab — nicht aus einem frischen
`datetime.now()`. Dadurch ist die Ausrichtung immun gegen NTP-Sprünge
mitten in der Session, während `mono_ns` die präzise Ordnung liefert.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone


class SessionClock:
    def __init__(self) -> None:
        self._utc0 = datetime.now(timezone.utc)
        self._mono0 = time.perf_counter_ns()

    @property
    def mono_anchor_ns(self) -> int:
        return self._mono0

    @property
    def started_utc(self) -> str:
        return self._utc0.isoformat()

    def now(self) -> tuple[int, str]:
        """(mono_ns, utc_iso) für den aktuellen Moment."""
        mono = time.perf_counter_ns()
        utc = self._utc0 + timedelta(microseconds=(mono - self._mono0) / 1000)
        return mono, utc.isoformat()
