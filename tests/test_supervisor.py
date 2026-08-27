"""EW-38 supervisor: multi-match auto-sessions + per-session notes.

Drives the synchronous session-management methods directly (no hardware, no
match, no async timing) and checks the resulting SQLite file.
"""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from pathlib import Path

from riftrec.config import RecorderConfig
from riftrec.rte.supervisor import SupervisorService
from riftrec.storage import sqlite_sink as _sink_mod


def _hr(bpm: int) -> bytes:
    return bytes([0x00, bpm])  # flags=0 (uint8 HR, no RR)


class _FakeTransport:
    """No-op BLE transport so run() can be driven without hardware."""

    is_connected = True
    address = "FA:KE:00:00:00:00"
    name = "Polar H10 FAKE"

    def __init__(self, battery: int = 87) -> None:
        self._battery = battery

    async def connect(self, device) -> None:
        pass

    async def subscribe(self, uuid, callback) -> None:
        self.callback = callback

    async def read(self, uuid: str) -> bytes:
        if uuid.startswith("00002a19"):        # battery level
            return bytes([self._battery])
        if uuid.startswith("00002a25"):        # serial number
            return b"FAKESERIAL"
        raise RuntimeError("characteristic not available")

    async def disconnect(self) -> None:
        pass


class _FlakyTransport:
    """Toggleable transport to simulate an H10 dropout + reconnect (EW-42)."""

    def __init__(self) -> None:
        self._connected = False  # loop performs the initial connect
        self.connect_calls = 0
        self.subscribe_calls = 0

    @property
    def is_connected(self) -> bool:
        return self._connected

    def drop(self) -> None:
        self._connected = False

    async def connect(self, device) -> None:
        self.connect_calls += 1
        self._connected = True  # (re)connect succeeds

    async def subscribe(self, uuid, callback) -> None:
        self.subscribe_calls += 1
        self.callback = callback

    async def disconnect(self) -> None:
        self._connected = False


class _LateTransport:
    """Fails the first `fail_first` connects (H10 not worn yet), then succeeds."""

    def __init__(self, fail_first: int = 2) -> None:
        self._connected = False
        self._fail = fail_first
        self.connect_calls = 0

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self, device) -> None:
        self.connect_calls += 1
        if self.connect_calls <= self._fail:
            raise RuntimeError("H10 not found (not worn yet)")
        self._connected = True

    async def subscribe(self, uuid, callback) -> None:
        self.callback = callback

    async def disconnect(self) -> None:
        self._connected = False


def _riot_frame(kill_id: int) -> dict:
    return {
        "gameData": {"gameTime": 30.0},
        "activePlayer": {"summonerName": "P"},
        "allPlayers": [{"summonerName": "P",
                        "scores": {"kills": 1, "deaths": 0, "assists": 0, "creepScore": 20}}],
        "events": {"Events": [{"EventID": kill_id, "EventName": "ChampionKill", "EventTime": 30.0}]},
    }


def test_two_matches_accumulate_in_one_file_with_notes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "sitting.sqlite"
        svc = SupervisorService(RecorderConfig(participant_id="P01", db_path=db,
                                               snapshot_interval_s=0))

        # Match 1
        sid1 = svc._open_session()
        svc._on_hr(_hr(80))
        svc._record_riot(_riot_frame(1))
        assert svc.add_note("felt tilted after that gank") is True
        svc._close_session()

        # Between matches: HR is discarded (no open session)
        svc._on_hr(_hr(70))
        # A note between matches attaches to the just-finished session
        assert svc.add_note("post-game: bad connection") is True

        # Match 2
        sid2 = svc._open_session()
        svc._on_hr(_hr(82))
        svc._close_session()

        conn = sqlite3.connect(db)
        try:
            sessions = conn.execute(
                "SELECT session_id, session_index, notes, active_riot_id FROM session "
                "ORDER BY session_index"
            ).fetchall()
            assert [s[1] for s in sessions] == [1, 2]
            assert sessions[0][0] == sid1 and sessions[1][0] == sid2

            # Match 1 saw a Riot poll -> active_riot_id captured; match 2 never
            # received a Riot frame (only HR) -> stays unset.
            assert sessions[0][3] == "P"
            assert sessions[1][3] is None

            # HR: one per match; the between-match sample was discarded -> 2 total
            (hr_total,) = conn.execute("SELECT COUNT(*) FROM hr_sample").fetchone()
            assert hr_total == 2
            (hr1,) = conn.execute("SELECT COUNT(*) FROM hr_sample WHERE session_id=?", (sid1,)).fetchone()
            (hr2,) = conn.execute("SELECT COUNT(*) FROM hr_sample WHERE session_id=?", (sid2,)).fetchone()
            assert hr1 == 1 and hr2 == 1

            # Event went to match 1
            (ev1,) = conn.execute("SELECT COUNT(*) FROM game_event WHERE session_id=?", (sid1,)).fetchone()
            assert ev1 == 1

            # Both notes attached to session 1 (two lines)
            notes1 = sessions[0][2]
            assert "felt tilted" in notes1 and "bad connection" in notes1
            assert notes1.count("\n") == 1
        finally:
            conn.close()


def test_run_refuses_without_participant_id() -> None:
    """EW-41 backstop: no participant id -> ERROR, nothing recorded."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "untagged.sqlite"
        svc = SupervisorService(RecorderConfig(participant_id=None, db_path=db),
                                transport=_FakeTransport())
        asyncio.run(svc.run(asyncio.Event()))
        from riftrec.rte.state import RecorderState
        assert svc.status.state is RecorderState.ERROR
        assert not db.exists()  # no session file created


def test_reconnects_and_logs_gap_on_h10_dropout() -> None:
    """EW-42: an H10 outage mid-match is gapped and the link is re-established,
    while Riot recording keeps running through the outage."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "gap.sqlite"
        tr = _FlakyTransport()
        stop = asyncio.Event()
        state = {"i": 0}

        async def fetch():
            i = state["i"]
            state["i"] += 1
            if i == 1:
                tr.drop()           # H10 drops after the first frame
            if i >= 4:
                stop.set()
                return None
            return _riot_frame(i + 1)

        svc = SupervisorService(
            RecorderConfig(participant_id="P01", db_path=db, snapshot_interval_s=0,
                           poll_interval_s=0.0, flush_interval_s=0.0,
                           reconnect_backoff_s=0.0),
            transport=tr, riot_fetch=fetch, game_probe=lambda: None)
        asyncio.run(svc.run(stop))

        conn = sqlite3.connect(db)
        try:
            gaps = conn.execute(
                "SELECT source, start_utc, end_utc FROM gap").fetchall()
            (events,) = conn.execute("SELECT COUNT(*) FROM game_event").fetchone()
        finally:
            conn.close()

        assert len(gaps) == 1, gaps
        assert gaps[0][0] == "h10"
        assert gaps[0][1] and gaps[0][2]      # gap has both a start and an end
        assert tr.connect_calls >= 2          # initial connect + >=1 reconnect
        assert tr.subscribe_calls >= 2        # re-subscribed after reconnect
        assert events >= 3                    # Riot kept recording through the drop


def test_waits_for_h10_at_start_instead_of_erroring() -> None:
    """EW-42: if the H10 isn't worn yet at start, the recorder retries the
    connect instead of going to ERROR, and records once the strap is on."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "late.sqlite"
        tr = _LateTransport(fail_first=2)
        stop = asyncio.Event()
        state = {"i": 0}

        async def fetch():
            i = state["i"]
            state["i"] += 1
            if i >= 5:
                stop.set()
                return None
            return _riot_frame(i + 1)

        svc = SupervisorService(
            RecorderConfig(participant_id="P01", db_path=db, snapshot_interval_s=0,
                           poll_interval_s=0.0, flush_interval_s=0.0,
                           reconnect_backoff_s=0.0),
            transport=tr, riot_fetch=fetch, game_probe=lambda: None)
        asyncio.run(svc.run(stop))

        from riftrec.rte.state import RecorderState
        assert svc.status.state is not RecorderState.ERROR
        assert tr.connect_calls > 2          # retried past the initial failures
        conn = sqlite3.connect(db)
        try:
            (sessions,) = conn.execute("SELECT COUNT(*) FROM session").fetchone()
        finally:
            conn.close()
        assert sessions >= 1                 # recorded once the H10 came up


def test_add_note_without_session_returns_false() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        svc = SupervisorService(RecorderConfig(db_path=Path(tmp) / "x.sqlite"))
        assert svc.add_note("nothing recorded yet") is False


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK - {name}")
    print("OK - all supervisor tests passed")


# -- Battery level surfaced to the UI (EW-86 follow-up) --------------------


def test_refresh_device_info_publishes_battery_and_records_it(tmp_path) -> None:
    """The tray reads this observable; a session must also keep the value."""
    import asyncio

    db = tmp_path / "battery.sqlite"
    svc = SupervisorService(RecorderConfig(db_path=db), transport=_FakeTransport(battery=42))
    svc._open_session()
    asyncio.run(svc._refresh_device_info(_FakeTransport(battery=42)))
    svc._close_session()

    assert svc.battery.state == 42

    con = sqlite3.connect(db)
    serial, battery = con.execute(
        "SELECT serial, battery_pct FROM device_info ORDER BY utc DESC LIMIT 1"
    ).fetchone()
    assert (serial, battery) == ("FAKESERIAL", 42)


def test_battery_unknown_when_sensor_will_not_say(tmp_path) -> None:
    """A strap that refuses the read must not break recording - just no value."""
    import asyncio

    class _Mute(_FakeTransport):
        async def read(self, uuid: str) -> bytes:
            raise RuntimeError("nope")

    svc = SupervisorService(
        RecorderConfig(db_path=tmp_path / "mute.sqlite"), transport=_Mute()
    )
    svc._open_session()
    asyncio.run(svc._refresh_device_info(_Mute()))
    assert svc.battery.state is None


# -- Plain-language status + no leftover file (EW-89) ----------------------


class _NeverTransport:
    """A strap that is never found - the desk-drawer case that produced 72
    silent reconnect attempts behind a single amber tray dot."""

    is_connected = False

    def __init__(self) -> None:
        self.connect_calls = 0

    async def connect(self, device) -> None:
        self.connect_calls += 1
        raise RuntimeError(
            "No matching BLE device found. "
            "Is the H10 worn and are the electrodes moistened?")

    async def subscribe(self, uuid, callback) -> None:
        pass

    async def disconnect(self) -> None:
        pass


def _run_without_strap_or_match(db):
    """Drive run() with no strap and no match, and collect what it published."""
    stop = asyncio.Event()
    ticks = {"n": 0}

    async def fetch():
        ticks["n"] += 1
        if ticks["n"] >= 4:
            stop.set()
        return None                      # no match ever starts

    svc = SupervisorService(
        RecorderConfig(participant_id="P01", db_path=db, poll_interval_s=0.0,
                       reconnect_backoff_s=0.0),
        transport=_NeverTransport(), riot_fetch=fetch, game_probe=lambda: None)
    reports = []
    svc.report.subscribe(reports.append)
    asyncio.run(svc.run(stop))
    return svc, reports


def test_missing_strap_is_reported_in_plain_language_with_a_count(tmp_path) -> None:
    """EW-89: the recorder knows the reason, so it has to say it - and say how
    long it has been saying it."""
    from riftrec.rte.state import RecorderState
    from riftrec.rte.status import Activity

    _svc, reports = _run_without_strap_or_match(tmp_path / "nostrap.sqlite")

    strap = [r for r in reports if r.activity is Activity.STRAP_NOT_FOUND]
    assert strap, [r.activity for r in reports]
    assert strap[-1].state is RecorderState.CONNECTING
    assert max(r.attempts for r in strap) >= 2   # the count climbs, visibly
    assert "electrodes" in strap[-1].detail
    assert "No matching BLE device" in (strap[-1].cause or "")


def test_a_run_without_a_match_leaves_no_file_behind(tmp_path) -> None:
    """EW-89: mis-starts must not litter the participant's folder."""
    db = tmp_path / "nostrap.sqlite"
    _run_without_strap_or_match(db)
    assert not db.exists()


def test_missing_participant_id_is_explained_not_just_coloured(tmp_path) -> None:
    from riftrec.rte.state import RecorderState
    from riftrec.rte.status import Activity

    svc = SupervisorService(
        RecorderConfig(participant_id=None, db_path=tmp_path / "untagged.sqlite"),
        transport=_FakeTransport())
    asyncio.run(svc.run(asyncio.Event()))

    report = svc.report.state
    assert report.state is RecorderState.ERROR
    assert report.activity is Activity.NO_PARTICIPANT_ID
    assert "participant ID" in report.detail


def test_recording_report_names_the_match(tmp_path) -> None:
    """The tray line a participant sees while a game is live."""
    from riftrec.rte.status import Activity

    svc = SupervisorService(
        RecorderConfig(participant_id="P01", db_path=tmp_path / "live.sqlite"),
        transport=_FakeTransport())
    svc._open_session()
    report = svc.report.state
    assert report.activity is Activity.RECORDING
    assert report.headline == "Recording match 1"
    svc._close_session()
    assert svc.report.state.activity is Activity.WAITING_FOR_MATCH
    assert svc.matches_recorded == 1


# -- Health monitoring: the failures that look like success (EW-89) --------


def _hr_with_rr(bpm: int) -> bytes:
    """flags=0x10 (RR present, uint8 HR) + one 1000 ms interval."""
    return bytes([0x10, bpm, 0x00, 0x04])


def _health_service(db, **kw):
    from riftrec.rte.health import Thresholds

    svc = SupervisorService(
        RecorderConfig(participant_id="P01", db_path=db),
        transport=_FakeTransport(),
        thresholds=kw.pop("thresholds", Thresholds()),
        game_probe=kw.pop("game_probe", lambda: None),
    )
    svc._h10_up = True          # the link is up; we are not driving the loop
    svc.alerts = []
    svc.on_alert = lambda issue, raised: svc.alerts.append((issue, raised))
    return svc


def test_frozen_heart_rate_without_rr_is_caught_and_gapped(tmp_path) -> None:
    """The H10 keeps sending a plausible HR after losing skin contact. Only the
    absence of RR gives it away - and the stretch has to end up in the file, or
    the analysis cannot tell that section from a clean one."""
    from riftrec.rte.health import Issue
    from riftrec.rte.supervisor import CONTACT_GAP_SOURCE

    db = tmp_path / "contact.sqlite"
    svc = _health_service(db)
    svc._open_session()

    svc._on_hr(_hr_with_rr(70))          # healthy: HR and RR
    svc._check_health()
    assert svc.alerts == []

    svc._on_hr(_hr(70))                  # frozen value: HR, no RR
    svc._last_rr_mono -= 60              # ...for a minute
    svc._check_health()
    assert (Issue.NO_SKIN_CONTACT, True) in svc.alerts

    svc._on_hr(_hr_with_rr(72))          # contact is back
    svc._check_health()
    assert (Issue.NO_SKIN_CONTACT, False) in svc.alerts

    svc._close_session()
    conn = sqlite3.connect(db)
    try:
        gaps = conn.execute(
            "SELECT source, start_utc, end_utc FROM gap").fetchall()
    finally:
        conn.close()
    assert len(gaps) == 1, gaps
    assert gaps[0][0] == CONTACT_GAP_SOURCE
    assert gaps[0][1] and gaps[0][2]


def test_contact_still_lost_at_match_end_is_gapped_up_to_the_end(tmp_path) -> None:
    from riftrec.rte.supervisor import CONTACT_GAP_SOURCE

    db = tmp_path / "contact2.sqlite"
    svc = _health_service(db)
    svc._open_session()
    svc._on_hr(_hr_with_rr(70))
    svc._on_hr(_hr(70))
    svc._last_rr_mono -= 60
    svc._check_health()
    svc._close_session()                 # match ends while still out of contact

    conn = sqlite3.connect(db)
    try:
        sources = [r[0] for r in conn.execute("SELECT source FROM gap")]
    finally:
        conn.close()
    assert sources == [CONTACT_GAP_SOURCE]


def test_the_tray_line_changes_when_the_data_stops_being_usable(tmp_path) -> None:
    """A red "recording" icon while nothing usable is recorded is the lie this
    ticket exists to remove."""
    from riftrec.rte.state import RecorderState
    from riftrec.rte.status import Activity

    svc = _health_service(tmp_path / "warn.sqlite")
    svc._open_session()
    assert svc.status.state is RecorderState.RECORDING

    svc._on_hr(_hr_with_rr(70))
    svc._on_hr(_hr(70))
    svc._last_rr_mono -= 60
    svc._check_health()
    assert svc.status.state is RecorderState.WARNING
    assert svc.report.state.activity is Activity.NO_SKIN_CONTACT
    assert "electrodes" in svc.report.state.detail

    svc._on_hr(_hr_with_rr(70))          # resolved -> back to the truth
    svc._check_health()
    assert svc.status.state is RecorderState.RECORDING
    assert svc.report.state.activity is Activity.RECORDING


def test_storage_failure_is_announced_instead_of_ending_the_run(tmp_path) -> None:
    from riftrec.rte.health import Issue
    from riftrec.rte.state import RecorderState

    svc = _health_service(tmp_path / "storage.sqlite")

    def boom() -> None:
        raise OSError("The device is not ready")

    assert svc._guard_storage(boom, "writing the recording") is False
    svc._check_health()
    assert (Issue.STORAGE_FAILED, True) in svc.alerts
    assert svc.status.state is RecorderState.WARNING
    assert "not reachable" in svc.report.state.detail

    assert svc._guard_storage(lambda: None, "writing the recording") is True
    svc._check_health()
    assert (Issue.STORAGE_FAILED, False) in svc.alerts


def test_a_match_with_unreachable_storage_does_not_crash_the_recorder(tmp_path) -> None:
    """An unplugged drive used to raise straight out of the watch loop and end
    the recording without a word."""
    from riftrec.rte.health import Issue

    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"")             # a file where a directory is needed
    stop = asyncio.Event()
    ticks = {"n": 0}

    async def fetch():
        ticks["n"] += 1
        if ticks["n"] >= 3:
            stop.set()
        return _riot_frame(ticks["n"])

    svc = SupervisorService(
        RecorderConfig(participant_id="P01", db_path=blocker / "deep" / "x.sqlite",
                       poll_interval_s=0.0, reconnect_backoff_s=0.0),
        transport=_FakeTransport(), riot_fetch=fetch, game_probe=lambda: None)
    alerts = []
    svc.on_alert = lambda issue, raised: alerts.append((issue, raised))

    asyncio.run(svc.run(stop))           # must return, not raise

    assert (Issue.STORAGE_FAILED, True) in alerts
    assert svc.matches_recorded == 0


def test_league_running_while_the_api_stays_silent_is_announced(tmp_path) -> None:
    """The deaf recorder: the tray would otherwise sit on a green "ready,
    waiting for a match" while matches are being played."""
    from riftrec.rte.health import Issue

    db = tmp_path / "blind.sqlite"
    stop = asyncio.Event()
    ticks = {"n": 0}

    async def fetch():
        ticks["n"] += 1
        if ticks["n"] >= 3:
            stop.set()
        return None                      # the API never answers

    svc = SupervisorService(
        RecorderConfig(participant_id="P01", db_path=db, poll_interval_s=0.0,
                       reconnect_backoff_s=0.0, league_poll_s=0.0),
        transport=_FakeTransport(), riot_fetch=fetch, game_probe=lambda: True)
    alerts = []
    svc.on_alert = lambda issue, raised: alerts.append((issue, raised))

    asyncio.run(svc.run(stop))

    assert (Issue.GAME_NOT_VISIBLE, True) in alerts
    assert not db.exists()               # nothing recorded -> no leftover file


def test_no_alarm_when_nobody_is_playing(tmp_path) -> None:
    """The normal idle evening must stay silent."""
    db = tmp_path / "idle.sqlite"
    stop = asyncio.Event()
    ticks = {"n": 0}

    async def fetch():
        ticks["n"] += 1
        if ticks["n"] >= 3:
            stop.set()
        return None

    svc = SupervisorService(
        RecorderConfig(participant_id="P01", db_path=db, poll_interval_s=0.0,
                       reconnect_backoff_s=0.0, league_poll_s=0.0),
        transport=_FakeTransport(), riot_fetch=fetch, game_probe=lambda: False)
    alerts = []
    svc.on_alert = lambda issue, raised: alerts.append((issue, raised))

    asyncio.run(svc.run(stop))
    assert alerts == []
