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
import sqlite3
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable, Optional

from .. import SCHEMA_VERSION, __version__
from ..clock import SessionClock
from ..config import RecorderConfig
from ..hal.ble import BleTransport
from ..model import (
    DeviceInfo, Gap, GameEvent, GameRaw, HrRaw, HrSample, RrInterval, SessionMeta,
)
from ..sources.game_process import is_game_running
from ..sources.h10 import HR_MEASUREMENT_UUID, parse_hr_measurement, read_device_info
from ..sources.riot import (
    DEFAULT_BASE_URL,
    active_riot_id,
    apply_pseudonyms,
    build_pseudonym_map,
    compress_game_data,
    extract_snapshot,
    new_events,
)
from ..storage.sqlite_sink import (
    SqliteSink, append_session_note, discard_if_unused,
)
from .health import Issue, Signals, Thresholds, active_issues, worst
from .state import Observable, RecorderState
from .status import Activity, StatusReport, classify_connect_error

_ALLGAMEDATA = "/liveclientdata/allgamedata"

# Written into the existing `gap` table rather than a new one: a lost skin
# contact is an outage of the signal, exactly like a dropped BLE link, and
# RiftLab does not read `gap` at all today - so a new source value costs no
# schema change and breaks nothing (EW-89).
CONTACT_GAP_SOURCE = "h10_contact"

# Which health issue replaces the tray line, and with which words. BATTERY_LOW
# is deliberately absent: the battery already has its own line in the menu, and
# overwriting "Recording match 3" with a battery notice would hide the more
# important fact that recording is fine.
_ISSUE_ACTIVITY: dict[Issue, Activity] = {
    Issue.STORAGE_FAILED: Activity.STORAGE_FAILED,
    Issue.GAME_NOT_VISIBLE: Activity.GAME_NOT_VISIBLE,
    Issue.NO_HEART_RATE: Activity.NO_HEART_RATE,
    Issue.NO_SKIN_CONTACT: Activity.NO_SKIN_CONTACT,
}
_HEALTH_ACTIVITIES = frozenset(_ISSUE_ACTIVITY.values())


class _Session:
    """Bookkeeping for the currently recording match."""

    def __init__(self, sink: SqliteSink, clock: SessionClock, session_id: str) -> None:
        self.sink = sink
        self.clock = clock
        self.session_id = session_id
        self.last_event_id: Optional[int] = None
        self.last_snapshot_mono = 0
        self.active_riot_id: Optional[str] = None
        self.last_raw_mono: Optional[int] = None
        # name -> session-local pseudonym for the nine other players (EW-86)
        self.pseudonyms: dict[str, str] = {}


class SupervisorService:
    def __init__(
        self,
        config: RecorderConfig,
        *,
        transport: Optional[BleTransport] = None,
        riot_fetch=None,
        thresholds: Optional[Thresholds] = None,
        game_probe: Optional[Callable[[], Optional[bool]]] = None,
    ) -> None:
        self._config = config
        self._transport = transport
        self._riot_fetch = riot_fetch
        self.status = Observable(RecorderState.IDLE)
        # Plain-language reason behind the state (EW-89). Mirrors the
        # battery observable: the tray subscribes and renders a sentence,
        # so an amber dot is never the only thing a participant gets.
        self.report = Observable(StatusReport())
        self._activity = Activity.STARTING
        self._connect_attempts = 0
        self.matches_recorded = 0
        self._current: Optional[_Session] = None
        self._last_session_id: Optional[str] = None
        self._session_index = config.session_index or 0
        # H10 link supervision (EW-42): whether the link is currently up, and
        # the start_utc of an ongoing outage (None while healthy / never up yet).
        self._h10_up = False
        self._h10_gap_start: Optional[str] = None
        # Identity of the connected strap, read once per BLE connect. One link
        # spans several matches, so it is cached here and written into every
        # session that opens under it (EW-86).
        self._device_info: Optional[DeviceInfo] = None
        # Battery level of the strap, surfaced to the tray so a participant can
        # replace the cell before it dies mid-study rather than after.
        self.battery = Observable(None)
        self._battery_checked_mono = 0.0
        # Health monitoring (EW-89): the failures that look like success.
        # Timestamps are monotonic seconds; None means 'not since this match'.
        self._thresholds = thresholds or Thresholds()
        self._game_probe = game_probe or is_game_running
        self._issues: set[Issue] = set()
        self._last_hr_mono: Optional[float] = None
        self._last_rr_mono: Optional[float] = None
        self._last_game_mono: Optional[float] = None
        self._match_started_mono: Optional[float] = None
        self._league_running: Optional[bool] = None
        self._league_checked_mono: Optional[float] = None
        self._league_up_since: Optional[float] = None
        self._storage_error: Optional[str] = None
        self._contact_gap_start: Optional[str] = None
        # Set by the runner to the tray's notifier: these situations lose data
        # while nobody is looking, so they push instead of waiting to be read.
        self.on_alert: Optional[Callable[[Issue, bool], None]] = None

    # -- status publishing (EW-89) ----------------------------------------

    def _publish(
        self,
        state: RecorderState,
        activity: Activity,
        *,
        cause: Optional[str] = None,
    ) -> None:
        """Set the coloured state and the sentence explaining it together.

        One call for both so they cannot drift apart - a colour without a
        reason is exactly the failure this ticket was raised for.
        """
        self._activity = activity
        self.status.set(state)
        self.report.set(StatusReport(
            state=state,
            activity=activity,
            attempts=self._connect_attempts,
            match_index=self._session_index if self._current else None,
            cause=cause,
        ))

    # After this many consecutive failures, log only every Nth. The cause
    # does not change between them and the tray now carries the attempt
    # count, so 72 identical lines add nothing - but the count and the
    # message itself stay in the log for remote diagnosis.
    _LOG_EVERY = 10

    def _log_connect_failure(self, exc: Exception) -> None:
        n = self._connect_attempts
        if n == 1 or n % self._LOG_EVERY == 0:
            print(f"[warn] H10 connect failed (attempt {n}): {exc}; "
                  f"retrying in {self._config.reconnect_backoff_s}s")

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
        # Judge this match on its own: heart rate from the previous one must
        # not count as 'recent', or a fresh match would look healthy for a
        # minute (or alarm instantly) on stale timestamps.
        self._match_started_mono = time.monotonic()
        self._last_hr_mono = None
        self._last_rr_mono = None
        self._write_device_info()
        self._publish(RecorderState.RECORDING, Activity.RECORDING)
        return session_id

    def _write_device_info(self) -> None:
        """Record which strap is producing this session, if both are known.

        Called from two directions because either can come first: a match may
        start before the strap connects, or the strap may already be connected
        when the match starts. Writing on a later reconnect too is intentional -
        each row is timestamped, so repeated rows document the battery level
        over the course of a long session.
        """
        cur, info = self._current, self._device_info
        if cur is None or info is None:
            return
        _, utc = cur.clock.now()
        cur.sink.write(replace(info, utc=utc))

    def _on_hr(self, payload: bytes) -> None:
        """H10 notify callback. Between matches (no session) HR is discarded."""
        cur = self._current
        if cur is None:
            return
        hr, rr_list, contact = parse_hr_measurement(payload)
        # Health timestamps (EW-89). RR is tracked separately from HR because
        # the H10 keeps sending a frozen HR after losing skin contact, while
        # RR stops - so 'a number is arriving' proves nothing on its own.
        self._last_hr_mono = time.monotonic()
        if rr_list:
            self._last_rr_mono = self._last_hr_mono
        mono, utc = cur.clock.now()
        # Raw first: if parsing ever proves wrong, the payload is still there.
        cur.sink.write(HrRaw(mono_ns=mono, utc=utc, payload=bytes(payload)))
        cur.sink.write(HrSample(mono_ns=mono, utc=utc, hr_bpm=hr, contact=contact))
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
        # Built once per session, from the first response carrying a player
        # list, and reused for raw and event payloads so that the kill/death
        # attribution stays consistent (EW-86).
        if not cur.pseudonyms and data.get("allPlayers"):
            cur.pseudonyms = build_pseudonym_map(data, cur.clock.started_utc)
        mono, utc = cur.clock.now()
        events = (data.get("events") or {}).get("Events") or []
        end_seen = False
        for event in new_events(events, cur.last_event_id):
            cur.last_event_id = event.get("EventID", cur.last_event_id)
            cur.sink.write(GameEvent(
                mono_ns=mono, utc=utc, game_time_s=event.get("EventTime"),
                event_id=event.get("EventID"), event_type=event.get("EventName", "Unknown"),
                payload_json=json.dumps(apply_pseudonyms(event, cur.pseudonyms)),
            ))
            if event.get("EventName") == "GameEnd":
                end_seen = True
        if mono - cur.last_snapshot_mono >= self._config.snapshot_interval_s * 1e9:
            cur.sink.write(extract_snapshot(data, mono, utc))
            cur.last_snapshot_mono = mono
        # First poll always, then at the coarse raw interval; plus the last
        # response before the match ends, so the final scoreboard is kept.
        due = (
            cur.last_raw_mono is None
            or mono - cur.last_raw_mono >= self._config.raw_interval_s * 1e9
        )
        if due or end_seen:
            cur.sink.write(GameRaw(
                mono_ns=mono, utc=utc,
                game_time_s=(data.get("gameData") or {}).get("gameTime"),
                payload_zlib=compress_game_data(data, cur.pseudonyms),
            ))
            cur.last_raw_mono = mono

    def _close_session(self) -> None:
        cur = self._current
        if cur is None:
            return
        now_utc = datetime.now(timezone.utc).isoformat()
        try:
            # If the H10 is still out when the match ends, record the gap up to
            # now on this session before we close it. A fresh gap starts next
            # tick if the link is still down (but between matches HR is
            # discarded anyway). Same for an unresolved skin-contact gap.
            if self._h10_gap_start is not None:
                cur.sink.mark_gap(Gap(source="h10", start_utc=self._h10_gap_start,
                                      end_utc=now_utc))
                self._h10_gap_start = None
            if self._contact_gap_start is not None:
                cur.sink.mark_gap(Gap(source=CONTACT_GAP_SOURCE,
                                      start_utc=self._contact_gap_start,
                                      end_utc=now_utc))
                self._contact_gap_start = None
            cur.sink.close_session(now_utc)
        except (sqlite3.Error, OSError) as exc:
            # A failing close must not take the recorder down in the middle of
            # a study: everything already committed stays in the file, and the
            # next match opens a fresh sink.
            print(f"[warn] could not close the session cleanly: {exc}")
            self._storage_error = str(exc)
        finally:
            self._current = None
            self.matches_recorded += 1
            self._publish(RecorderState.READY, Activity.WAITING_FOR_MATCH)

    # -- health monitoring: the failures that look like success (EW-89) ---

    def _check_health(self) -> None:
        """Judge the situation, tell the participant what changed.

        Called every watch tick. Only the *edges* are announced - an issue
        that starts and an issue that ends - so a strap that stays off does
        not produce a notification per second.
        """
        now = time.monotonic()
        issues = active_issues(
            Signals(
                now=now,
                match_live=self._current is not None,
                last_hr=self._last_hr_mono,
                last_rr=self._last_rr_mono,
                last_game_data=self._last_game_mono,
                match_started=self._match_started_mono,
                strap_connected=self._h10_up,
                league_running=self._league_state(now),
                league_up_since=self._league_up_since,
                storage_error=self._storage_error,
                battery_pct=self.battery.state,
            ),
            self._thresholds,
        )
        for issue in sorted(issues - self._issues, key=lambda i: i.value):
            self._on_issue(issue, raised=True)
        for issue in sorted(self._issues - issues, key=lambda i: i.value):
            self._on_issue(issue, raised=False)
        self._issues = issues
        self._publish_health()

    def _league_state(self, now: float) -> Optional[bool]:
        """Is the game running? Cached, and never asked during a match.

        While a match is being recorded the answer is trivially yes. Between
        matches it is the only way to tell 'nobody is playing' from 'somebody
        is playing and we cannot see it'.
        """
        if self._current is not None:
            return True
        due = (
            self._league_checked_mono is None
            or now - self._league_checked_mono >= self._config.league_poll_s
        )
        if due:
            self._league_checked_mono = now
            was = self._league_running
            try:
                self._league_running = self._game_probe()
            except Exception as exc:  # never let a process listing matter
                print(f"[warn] could not check for the game process: {exc}")
                self._league_running = None
            # `League of Legends.exe` is the match process: one per game. Stamp
            # when it appeared so the health check can ask "has any game data
            # arrived since *this* game started?" rather than "recently?".
            if self._league_running and not was:
                self._league_up_since = now
            elif not self._league_running:
                self._league_up_since = None
        return self._league_running

    def _on_issue(self, issue: Issue, raised: bool) -> None:
        """Log it, notify the participant, and gap it where that applies."""
        print(f"[health] {issue.value} {'started' if raised else 'resolved'}")
        if issue is Issue.NO_SKIN_CONTACT:
            if raised:
                self._open_contact_gap()
            else:
                self._close_contact_gap()
        if self.on_alert is not None:
            try:
                self.on_alert(issue, raised)
            except Exception as exc:
                print(f"[warn] could not raise the alert: {exc}")

    def _open_contact_gap(self) -> None:
        """Mark the start of a stretch where the strap read no heartbeat."""
        if self._current is not None and self._contact_gap_start is None:
            self._contact_gap_start = datetime.now(timezone.utc).isoformat()

    def _close_contact_gap(self) -> None:
        if self._contact_gap_start is None:
            return
        if self._current is not None:
            self._guard_storage(
                lambda: self._current.sink.mark_gap(Gap(
                    source=CONTACT_GAP_SOURCE,
                    start_utc=self._contact_gap_start,
                    end_utc=datetime.now(timezone.utc).isoformat())),
                "recording a contact gap",
            )
        self._contact_gap_start = None

    def _publish_health(self) -> None:
        """Let the worst active issue own the tray line, then hand it back."""
        issue = worst(self._issues)
        activity = _ISSUE_ACTIVITY.get(issue) if issue is not None else None
        if activity is not None:
            self._publish(RecorderState.WARNING, activity)
        elif self._activity in _HEALTH_ACTIVITIES:
            # The last thing shown was a warning that no longer applies.
            if not self._h10_up:
                return   # the link supervisor states the truth every tick
            if self._current is not None:
                self._publish(RecorderState.RECORDING, Activity.RECORDING)
            else:
                self._publish(RecorderState.READY, Activity.WAITING_FOR_MATCH)

    # -- storage that may go away mid-study (EW-89) -----------------------

    def _guard_storage(self, action: Callable[[], None], what: str) -> bool:
        """Run a storage operation; on failure flag it instead of dying.

        An unplugged drive or a signed-out cloud folder used to raise straight
        out of the watch loop and end the recording silently. Now it becomes a
        visible issue, rows keep buffering in memory, and the next attempt
        writes everything once the folder is back.
        """
        try:
            action()
        except (sqlite3.Error, OSError) as exc:
            if self._storage_error is None:
                print(f"[warn] {what} failed: {exc}")
            self._storage_error = str(exc)
            return False
        self._storage_error = None
        return True

    def _try_open_session(self) -> bool:
        """Open a session for a match that just started, tolerating storage."""
        return self._guard_storage(self._open_session, "opening the recording")

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
        self._connect_attempts = 0
        self._close_h10_gap()
        if self._current is not None:
            self._publish(RecorderState.RECORDING, Activity.RECORDING)
        else:
            self._publish(RecorderState.READY, Activity.WAITING_FOR_MATCH)

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
            # Periodic battery refresh while the link stays up.
            if (
                time.monotonic() - self._battery_checked_mono
                >= self._config.battery_poll_s
            ):
                try:
                    await self._refresh_device_info(transport)
                except Exception as exc:
                    print(f"[warn] battery read failed: {exc}")
                    self._battery_checked_mono = time.monotonic()  # don't hammer
            return

        if self._h10_up:  # up -> down: a real mid-session drop
            self._h10_up = False
            self._connect_attempts = 0
            self._activity = Activity.STRAP_LOST
            print("[warn] H10 disconnected - HR paused, reconnecting...")

        # Surface the outage as CONNECTING plus the reason behind it, whether
        # or not a match is live - a strap that is off is worth saying out loud
        # between matches too. A live match keeps recording through it (only HR
        # pauses) and gets a gap row, regardless of whether the H10 was ever up,
        # so a match that started before the strap connected doesn't show a
        # green RECORDING with no HR behind it.
        self._publish(RecorderState.CONNECTING, self._activity)
        if self._current is not None and self._h10_gap_start is None:
            self._h10_gap_start = datetime.now(timezone.utc).isoformat()

        try:
            await transport.connect(self._config.device)
            await transport.subscribe(HR_MEASUREMENT_UUID, self._on_hr)
        except Exception as exc:
            self._connect_attempts += 1
            activity = classify_connect_error(str(exc))
            # A strap that dropped mid-run keeps that wording: 'check it is
            # still on' is different advice from 'put it on'.
            if (activity is Activity.STRAP_NOT_FOUND
                    and self._activity is Activity.STRAP_LOST):
                activity = Activity.STRAP_LOST
            self._publish(RecorderState.CONNECTING, activity, cause=str(exc))
            self._log_connect_failure(exc)
            await asyncio.sleep(self._config.reconnect_backoff_s)
            return

        # Read identity/battery once per link, then attach it to the running
        # session (or to the next one that opens).
        try:
            await self._refresh_device_info(transport)
        except Exception as exc:  # never let this stop a recording
            print(f"[warn] could not read device info: {exc}")

        print("[info] H10 connected")
        self._mark_h10_up()

    async def _refresh_device_info(self, transport: BleTransport) -> None:
        """Re-read identity and battery, publish the level, and record it.

        Called on every connect and periodically afterwards: one BLE link can
        stay up for hours, so a value read only at connect time would be stale
        exactly when a participant needs the warning. Each refresh also writes a
        timestamped device_info row, which gives a battery curve over long
        sessions for free.
        """
        self._device_info = await read_device_info(
            transport, datetime.now(timezone.utc).isoformat()
        )
        self.battery.set(self._device_info.battery_pct)
        self._battery_checked_mono = time.monotonic()
        self._write_device_info()

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
            self._publish(RecorderState.ERROR, Activity.NO_PARTICIPANT_ID)
            return

        transport = self._transport
        if transport is None:
            from ..hal.ble_bleak import BleakTransport

            transport = BleakTransport()

        # No hard initial connect: the watch loop's link supervisor establishes
        # the connection and retries, exactly like a mid-session reconnect. So a
        # fire-and-forget start waits for the strap to be put on instead of
        # dying with ERROR if the H10 isn't worn yet (EW-42).
        self._publish(RecorderState.CONNECTING, Activity.WAITING_FOR_STRAP)

        fetch, close = self._make_riot_fetch()
        last_flush = time.monotonic()
        try:
            while not stop.is_set():
                await self._keep_h10_connected(transport)   # reconnect + gap (EW-42)
                data = await fetch()
                if data is None:
                    if self._current is not None:
                        self._close_session()      # match ended (close flushes)
                    self._check_health()           # is League up and we are blind?
                    await asyncio.sleep(self._config.poll_interval_s)
                    continue
                self._last_game_mono = time.monotonic()
                if self._current is None:
                    if not self._try_open_session():   # match started
                        self._check_health()           # storage gone - say so
                        await asyncio.sleep(self._config.poll_interval_s)
                        continue
                    last_flush = time.monotonic()
                self._record_riot(data)
                # Throttle commits: buffer rows across poll ticks and flush on a
                # fixed cadence, so an event burst doesn't fan out into a burst
                # of synchronous commits (EW-51). Buffered-but-unflushed rows are
                # only at risk on a hard crash, and _close_session flushes on any
                # clean stop or match end.
                now = time.monotonic()
                if now - last_flush >= self._config.flush_interval_s:
                    self._guard_storage(self._current.sink.flush,
                                        "writing the recording")
                    last_flush = now
                self._check_health()
                await asyncio.sleep(self._config.poll_interval_s)
        finally:
            if self._current is not None:
                self._close_session()
            await close()
            try:
                await transport.disconnect()
            except Exception:
                pass
            self._publish(RecorderState.STOPPED, Activity.STOPPED)
            # A run that never saw a match can leave a .sqlite holding
            # nothing. Those pile up in a participant's folder and make it
            # impossible to tell which files are worth sending back (EW-89).
            discard_if_unused(self._config.db_path)

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
