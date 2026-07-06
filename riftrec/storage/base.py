"""SessionSink-Protocol: das Ziel, in das die Runtime Records schreibt.

Bewusst schmal gehalten, damit neben SQLite später andere Senken (z. B. ein
direkter Supabase/Postgres-Writer) ohne Änderung an Runtime oder Quellen
angebunden werden können.
"""

from __future__ import annotations

from typing import Protocol

from ..model import Gap, Record, SessionMeta


class SessionSink(Protocol):
    def open_session(self, meta: SessionMeta) -> None:
        """Session anlegen (Kopfzeile schreiben, Ziel vorbereiten)."""
        ...

    def write(self, record: Record) -> None:
        """Einen Record einreihen (darf puffern; siehe flush)."""
        ...

    def flush(self) -> None:
        """Gepufferte Records dauerhaft schreiben."""
        ...

    def mark_gap(self, gap: Gap) -> None:
        """Verbindungslücke einer Quelle festhalten (EW-39)."""
        ...

    def close_session(self, ended_utc: str) -> None:
        """Session abschließen (Rest flushen, ended_utc setzen, schließen)."""
        ...
