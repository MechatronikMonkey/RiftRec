"""RecorderRuntime - orchestration of one recording session.

Owns the lifecycle: create the session clock and header, start every source as
an async task, write their records through a shared queue into the sink, and
close cleanly at the end. Because all sources write under one session_id on one
clock into the same sink, the "merge" of the streams is a join at analysis time
- with no separate step.

Stop conditions: optional runtime (`duration_s`), all sources finishing
naturally, or cancellation (Ctrl+C / task cancel). In every case the rest of the
queue is drained and the session is closed.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Optional, Sequence

from .. import SCHEMA_VERSION, __version__
from ..clock import SessionClock
from ..model import GameEvent, GameSnapshot, HrSample, Record, RrInterval, SessionMeta
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
        """Run one session end-to-end and return its session_id."""
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
        # State is advanced from the record stream (EW-38): first physio sample
        # -> READY (connected), first Riot game record -> RECORDING (match live).

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
                    print(f"[warn] source {task.get_name()} ended with error: {res!r}")
            stop.set()
            await writer_task
            ended_utc = datetime.now(timezone.utc).isoformat()
            self._sink.close_session(ended_utc)
            self.status.set(RecorderState.STOPPED)
        return self.session_id

    async def _supervise(self, source_tasks: list[asyncio.Task]) -> None:
        """Wait until the FIRST source ends or the runtime elapses.

        "First source" instead of "all": when the Riot source ends (match over)
        the session should end, even if the H10 keeps streaming forever.
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
        """Drain the queue into the sink and flush periodically."""
        last_flush = time.monotonic()
        while True:
            try:
                record = await asyncio.wait_for(queue.get(), timeout=0.2)
                self._sink.write(record)
                self._advance_state(record)
            except asyncio.TimeoutError:
                pass
            now = time.monotonic()
            if now - last_flush >= self._flush_interval_s:
                self._sink.flush()
                last_flush = now
            if stop.is_set() and queue.empty():
                break
        self._sink.flush()

    def _advance_state(self, record: Record) -> None:
        """Drive READY/RECORDING from the record stream (EW-38).

        Game data (from Riot) means the match is live -> RECORDING. Physio data
        alone means we are connected and waiting -> READY. Only ever moves
        forward (never downgrades RECORDING back to READY).
        """
        state = self.status.state
        if isinstance(record, (GameEvent, GameSnapshot)):
            if state is not RecorderState.RECORDING:
                self.status.set(RecorderState.RECORDING)
        elif isinstance(record, (HrSample, RrInterval)):
            if state is RecorderState.CONNECTING:
                self.status.set(RecorderState.READY)
