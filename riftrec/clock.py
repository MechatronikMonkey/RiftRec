"""Shared session clock for all sources.

On creation it captures an anchor from wall-clock (UTC) and the monotonic clock.
`now()` derives the UTC timestamp from the anchor plus the monotonic time
elapsed since - not from a fresh `datetime.now()`. This keeps alignment immune
to NTP jumps mid-session, while `mono_ns` provides precise ordering.
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
        """(mono_ns, utc_iso) for the current moment."""
        mono = time.perf_counter_ns()
        utc = self._utc0 + timedelta(microseconds=(mono - self._mono0) / 1000)
        return mono, utc.isoformat()


def local_utc_offset_min() -> int:
    """The local UTC offset right now, in minutes east of UTC (EW-101).

    Stored per session rather than once per file: a run that crosses the
    switch to or from daylight saving time gets the right hour on both sides.
    """
    offset = datetime.now().astimezone().utcoffset()
    return int(offset.total_seconds() // 60) if offset is not None else 0
