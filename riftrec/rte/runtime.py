"""RecorderRuntime - die Orchestrierung einer Aufnahme-Session.

Verantwortlich für den Lebenszyklus: Session-Uhr und -Kopf anlegen, alle
Quellen als async Tasks starten, ihre Records über eine gemeinsame Queue in
den Sink schreiben, und am Ende sauber abschließen. Weil alle Quellen unter
einer session_id auf einer Uhr in denselben Sink schreiben, entsteht der
"Merge" der Streams als Join zur Auswertungszeit — ohne eigenen Schritt.

Stop-Bedingungen: optionale Laufzeit (`duration_s`), alle Quellen natürlich
beendet, oder Abbruch (Ctrl+C / Task-Cancel). In jedem Fall wird der Rest der
Queue geleert und die Session geschlossen.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Optional, Sequence

from .. import SCHEMA_VERSION, __version__
from ..clock import SessionClock
from ..model import Record, SessionMeta
from ..sources.base import SignalSource
from ..storage.base import SessionSink
from .state import Observable, RecorderState


class RecorderRuntime:
    def __init__(
        self,
        sources: Sequence[SignalSource],
        sink: SessionSink,
        *,
        participant_id: Optional[str] = None,
        session_index: Optional[int] = None,
        duration_s: Optional[float] = None,
        flush_interval_s: float = 1.0,
        notes: Optional[str] = None,
    ) -> None:
        self._sources = list(sources)
        self._sink = sink
        self._participant_id = participant_id
        self._session_index = session_index
        self._duration_s = duration_s
        self._flush_interval_s = flush_interval_s
        self._notes = notes
        self.status = Observable(RecorderState.IDLE)
        self.session_id: Optional[str] = None

    async def run(self) -> str:
        """Führt eine Session end-to-end aus und gibt ihre session_id zurück."""
        clock = SessionClock()
        self.session_id = str(uuid.uuid4())
        meta = SessionMeta(
            session_id=self.session_id,
            participant_id=self._participant_id,
            session_index=self._session_index,
            started_utc=clock.started_utc,
            mono_anchor_ns=clock.mono_anchor_ns,
            app_version=__version__,
            schema_version=SCHEMA_VERSION,
            notes=self._notes,
        )
        self.status.set(RecorderState.CONNECTING)
        self._sink.open_session(meta)

        queue: asyncio.Queue[Record] = asyncio.Queue()
        stop = asyncio.Event()
        # RECORDING sobald geschrieben wird; die Trennung READY->RECORDING per
        # Match-Erkennung folgt mit der RiotSource (EW-38).
        self.status.set(RecorderState.RECORDING)

        source_tasks = [
            asyncio.create_task(src.run(queue.put_nowait, clock), name=src.name)
            for src in self._sources
        ]
        writer_task = asyncio.create_task(self._writer(queue, stop))

        try:
            await self._supervise(source_tasks)
        finally:
            for task in source_tasks:
                task.cancel()
            results = await asyncio.gather(*source_tasks, return_exceptions=True)
            for task, res in zip(source_tasks, results):
                if isinstance(res, BaseException) and not isinstance(res, asyncio.CancelledError):
                    print(f"[warn] Quelle {task.get_name()} endete mit Fehler: {res!r}")
            stop.set()
            await writer_task
            ended_utc = datetime.now(timezone.utc).isoformat()
            self._sink.close_session(ended_utc)
            self.status.set(RecorderState.STOPPED)
        return self.session_id

    async def _supervise(self, source_tasks: list[asyncio.Task]) -> None:
        """Wartet, bis die ERSTE Quelle endet oder die Laufzeit abläuft.

        "Erste Quelle" statt "alle": endet die Riot-Quelle (Match vorbei),
        soll die Session enden, auch wenn der H10 endlos weiterstreamt.
        """
        waiters: list[asyncio.Task] = list(source_tasks)
        duration_task: asyncio.Task | None = None
        if self._duration_s is not None:
            duration_task = asyncio.create_task(
                asyncio.sleep(self._duration_s), name="duration"
            )
            waiters.append(duration_task)
        await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        if duration_task is not None and not duration_task.done():
            duration_task.cancel()

    async def _writer(self, queue: asyncio.Queue[Record], stop: asyncio.Event) -> None:
        """Draint die Queue in den Sink und flusht periodisch."""
        last_flush = time.monotonic()
        while True:
            try:
                record = await asyncio.wait_for(queue.get(), timeout=0.2)
                self._sink.write(record)
            except asyncio.TimeoutError:
                pass
            now = time.monotonic()
            if now - last_flush >= self._flush_interval_s:
                self._sink.flush()
                last_flush = now
            if stop.is_set() and queue.empty():
                break
        self._sink.flush()
