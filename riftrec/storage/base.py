"""SessionSink protocol: the target the runtime writes records into.

Kept deliberately narrow so that, besides SQLite, other sinks (e.g. a direct
Supabase/Postgres writer) can be plugged in later without changing the runtime
or the sources.
"""

from __future__ import annotations

from typing import Protocol

from ..model import Gap, Record, SessionMeta


class SessionSink(Protocol):
    def open_session(self, meta: SessionMeta) -> None:
        """Create the session (write header row, prepare the target)."""
        ...

    def write(self, record: Record) -> None:
        """Queue a record (may buffer; see flush)."""
        ...

    def flush(self) -> None:
        """Persist buffered records durably."""
        ...

    def mark_gap(self, gap: Gap) -> None:
        """Record a connection gap of a source (EW-39)."""
        ...

    def close_session(self, ended_utc: str) -> None:
        """Finish the session (flush the rest, set ended_utc, close)."""
        ...
