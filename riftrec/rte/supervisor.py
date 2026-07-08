"""SupervisorService - hands-off multi-match recorder (EW-38/EW-39).

Start once, then run unattended: the Polar H10 stays connected, and the Riot
Live Client Data API is watched continuously. Each detected match is recorded as
its own session row (auto-incrementing session_index) into ONE SQLite file;
match start and end are detected automatically. HR/RR arriving between matches
is discarded.

The per-match session management (_open_session / _on_hr / _record_riot /
_close_session) is synchronous so it can be unit-tested deterministically
without hardware or a running match. Only the outer watch loop (run) is async.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from .. import SCHEMA_VERSION, __version__
from ..clock import SessionClock
from ..config import RecorderConfig
from ..hal.ble import BleTransport
from ..model import GameEvent, HrSample, RrInterval, SessionMeta
from ..sources.h10 import HR_MEASUREMENT_UUID, parse_hr_measurement
from ..sources.riot import DEFAULT_BASE_URL, active_riot_id, extract_snapshot, new_events
from ..storage.sqlite_sink import SqliteSink, append_session_note
from .state import Observable, RecorderState

_ALLGAMEDATA = "/liveclientdata/allgamedata"


class _Session:
    """Bookkeeping for the currently recording match."""

    def __init__(self, sink: SqliteSink, clock: SessionClock, session_id: str) -> None:
        self.sink = sink
        self.clock = clock
        self.session_id = session_id
        self.last_event_id: Optional[int] = None
        self.last_snapshot_mono = 0
        self.active_riot_id: Optional[str] = None


class SupervisorService:
    def __init__(
        self,
        config: RecorderConfig,
        *,
        transport: Optional[BleTransport] = None,
        riot_fetch=None,
    ) -> None:
        self._config = config
        self._transport = transport
        self._riot_fetch = riot_fetch
        self.status = Observable(RecorderState.IDLE)
        self._current: Optional[_Session] = None
        self._last_session_id: Optional[str] = None
        self._session_index = config.session_index or 0

    # -- per-match session management (synchronous, unit-testable) --------

    def _open_session(self) -> str:
        self._session_index += 1
        clock = SessionClock()
        sink = SqliteSink(self._config.db_path)
        session_id = str(uuid.uuid4())
        self._last_session_id = session_id
        sink.open_session(SessionMeta(
            session_id=session_id,
            participant_id=self._config.participant_id,
            session_index=self._session_index,
            started_utc=clock.started_utc,
            mono_anchor_ns=clock.mono_anchor_ns,
            app_version=__version__,
            schema_version=SCHEMA_VERSION,
            notes=self._config.notes,
        ))
        self._current = _Session(sink, clock, session_id)
        self.status.set(RecorderState.RECORDING)
        return session_id

    def _on_hr(self, payload: bytes) -> None:
        """H10 notify callback. Between matches (no session) HR is discarded."""
        cur = self._current
        if cur is None:
            return
        hr, rr_list = parse_hr_measurement(payload)
        mono, utc = cur.clock.now()
        cur.sink.write(HrSample(mono_ns=mono, utc=utc, hr_bpm=hr))
        for rr_ms in rr_list:
            cur.sink.write(RrInterval(mono_ns=mono, utc=utc, rr_ms=rr_ms))

    def _record_riot(self, data: dict) -> None:
        cur = self._current
        if cur is None:
            return
        if cur.active_riot_id is None:
            rid = active_riot_id(data)
            if rid:
                cur.active_riot_id = rid
                cur.sink.set_active_riot_id(rid)
        mono, utc = cur.clock.now()
        events = (data.get("events") or {}).get("Events") or []
        for event in new_events(events, cur.last_event_id):
            cur.last_event_id = event.get("EventID", cur.last_event_id)
            cur.sink.write(GameEvent(
                mono_ns=mono, utc=utc, game_time_s=event.get("EventTime"),
                event_id=event.get("EventID"), event_type=event.get("EventName", "Unknown"),
                payload_json=json.dumps(event),
            ))
        if mono - cur.last_snapshot_mono >= self._config.snapshot_interval_s * 1e9:
            cur.sink.write(extract_snapshot(data, mono, utc))
            cur.last_snapshot_mono = mono

    def _close_session(self) -> None:
        cur = self._current
        if cur is None:
            return
        cur.sink.close_session(datetime.now(timezone.utc).isoformat())
        self._current = None
        self.status.set(RecorderState.READY)

    def add_note(self, text: str) -> bool:
        """Attach a note to the current session, or the last one between matches.

        Returns False if there is nothing to attach to yet (no match recorded).
        """
        text = (text or "").strip()
        if not text:
            return False
        sid = self._current.session_id if self._current else self._last_session_id
        if sid is None:
            return False
        append_session_note(self._config.db_path, sid, text)
        return True

    # -- async watch loop -------------------------------------------------

    async def run(self, stop: asyncio.Event) -> None:
        """Connect the H10 once, then watch for matches until `stop` is set."""
        transport = self._transport
        if transport is None:
            from ..hal.ble_bleak import BleakTransport

            transport = BleakTransport()

        self.status.set(RecorderState.CONNECTING)
        try:
            await transport.connect(self._config.device)
            await transport.subscribe(HR_MEASUREMENT_UUID, self._on_hr)
        except Exception as exc:  # H10 not found / not worn
            print(f"[error] H10 connect failed: {exc}")
            self.status.set(RecorderState.ERROR)
            return
        self.status.set(RecorderState.READY)

        fetch, close = self._make_riot_fetch()
        try:
            while not stop.is_set():
                data = await fetch()
                if data is None:
                    if self._current is not None:
                        self._close_session()      # match ended
                    await asyncio.sleep(self._config.poll_interval_s)
                    continue
                if self._current is None:
                    self._open_session()           # match started
                self._record_riot(data)
                self._current.sink.flush()
                await asyncio.sleep(self._config.poll_interval_s)
        finally:
            if self._current is not None:
                self._close_session()
            await close()
            try:
                await transport.disconnect()
            except Exception:
                pass
            self.status.set(RecorderState.STOPPED)

    def _make_riot_fetch(self):
        """Return (fetch, close). With an injected fetch, close is a no-op."""
        if self._riot_fetch is not None:
            async def _noop() -> None:
                return None

            return self._riot_fetch, _noop

        import httpx

        client = httpx.AsyncClient(verify=False, timeout=2.0)
        url = DEFAULT_BASE_URL + _ALLGAMEDATA

        async def _fetch() -> Optional[dict]:
            try:
                resp = await client.get(url)
            except httpx.RequestError:
                return None
            if resp.status_code != 200:
                return None
            try:
                return resp.json()
            except json.JSONDecodeError:
                return None

        async def _close() -> None:
            await client.aclose()

        return _fetch, _close
