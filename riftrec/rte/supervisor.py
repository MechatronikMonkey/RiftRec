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
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from .. import SCHEMA_VERSION, __version__
from ..clock import SessionClock
from ..config import RecorderConfig
from ..hal.ble import BleTransport
from ..model import Gap, GameEvent, HrSample, RrInterval, SessionMeta
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
        # H10 link supervision (EW-42): whether the link is currently up, and
        # the start_utc of an ongoing outage (None while healthy / never up yet).
        self._h10_up = False
        self._h10_gap_start: Optional[str] = None

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
        # If the H10 is still out when the match ends, record the gap up to now
        # on this session before we close it. A fresh gap starts next tick if
        # the link is still down (but between matches HR is discarded anyway).
        if self._h10_gap_start is not None:
            cur.sink.mark_gap(Gap(source="h10", start_utc=self._h10_gap_start,
                                  end_utc=datetime.now(timezone.utc).isoformat()))
            self._h10_gap_start = None
        cur.sink.close_session(datetime.now(timezone.utc).isoformat())
        self._current = None
        self.status.set(RecorderState.READY)

    # -- H10 link supervision (EW-42) -------------------------------------

    def _close_h10_gap(self) -> None:
        """Close an open outage gap on the current session (if any)."""
        if self._h10_gap_start is None:
            return
        if self._current is not None:
            self._current.sink.mark_gap(Gap(
                source="h10", start_utc=self._h10_gap_start,
                end_utc=datetime.now(timezone.utc).isoformat()))
        self._h10_gap_start = None

    def _mark_h10_up(self) -> None:
        """Link is up (first connect or reconnect): close any gap, restore state."""
        self._h10_up = True
        self._close_h10_gap()
        self.status.set(RecorderState.RECORDING if self._current else RecorderState.READY)

    async def _keep_h10_connected(self, transport: BleTransport) -> None:
        """Establish and keep the H10 link, retrying until it is up.

        Handles both the initial connect and mid-session reconnects with one
        path: bleak does NOT reconnect on its own, so once the strap is out of
        range (or not yet worn at start) the link stays down until we connect
        again. The HR service needs no pairing, so a (re)connect is just a fresh
        connect + subscribe. Called every watch-loop tick; Riot recording keeps
        running through an outage, only HR is paused (and gapped while a match
        is live).
        """
        if transport.is_connected:
            if not self._h10_up:
                print("[info] H10 connected")
                self._mark_h10_up()
            return

        if self._h10_up:  # up -> down: a real mid-session drop
            self._h10_up = False
            print("[warn] H10 disconnected - HR paused, reconnecting...")

        # While the link is down and a match is live, surface it as CONNECTING
        # (HR paused) and open a gap - regardless of whether the H10 was ever up
        # yet, so a match that started before the strap connected doesn't show a
        # green RECORDING with no HR behind it.
        if self._current is not None:
            if self.status.state is not RecorderState.CONNECTING:
                self.status.set(RecorderState.CONNECTING)
            if self._h10_gap_start is None:
                self._h10_gap_start = datetime.now(timezone.utc).isoformat()

        try:
            await transport.connect(self._config.device)
            await transport.subscribe(HR_MEASUREMENT_UUID, self._on_hr)
        except Exception as exc:
            print(f"[warn] H10 connect failed: {exc}; retrying in "
                  f"{self._config.reconnect_backoff_s}s")
            await asyncio.sleep(self._config.reconnect_backoff_s)
            return

        print("[info] H10 connected")
        self._mark_h10_up()

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
        """Watch for matches until `stop` is set, keeping the H10 linked."""
        # Backstop for EW-41: never record an untagged session. The GUI already
        # requires a participant id, but guard here too so any caller of the
        # supervisor produces attributable pilot data.
        if not (self._config.participant_id or "").strip():
            print("[error] no participant id set - refusing to record (EW-41)")
            self.status.set(RecorderState.ERROR)
            return

        transport = self._transport
        if transport is None:
            from ..hal.ble_bleak import BleakTransport

            transport = BleakTransport()

        # No hard initial connect: the watch loop's link supervisor establishes
        # the connection and retries, exactly like a mid-session reconnect. So a
        # fire-and-forget start waits for the strap to be put on instead of
        # dying with ERROR if the H10 isn't worn yet (EW-42).
        self.status.set(RecorderState.CONNECTING)

        fetch, close = self._make_riot_fetch()
        last_flush = time.monotonic()
        try:
            while not stop.is_set():
                await self._keep_h10_connected(transport)   # reconnect + gap (EW-42)
                data = await fetch()
                if data is None:
                    if self._current is not None:
                        self._close_session()      # match ended (close flushes)
                    await asyncio.sleep(self._config.poll_interval_s)
                    continue
                if self._current is None:
                    self._open_session()           # match started
                    last_flush = time.monotonic()
                self._record_riot(data)
                # Throttle commits: buffer rows across poll ticks and flush on a
                # fixed cadence, so an event burst doesn't fan out into a burst
                # of synchronous commits (EW-51). Buffered-but-unflushed rows are
                # only at risk on a hard crash, and _close_session flushes on any
                # clean stop or match end.
                now = time.monotonic()
                if now - last_flush >= self._config.flush_interval_s:
                    self._current.sink.flush()
                    last_flush = now
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
