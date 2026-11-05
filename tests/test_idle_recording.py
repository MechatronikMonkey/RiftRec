"""Schema 5: heart rate is kept between matches, as kind='idle' sessions.

Drives the real watch loop (run) with a fake strap and a scripted Live Client
API, so the order of open/close across idle -> match -> idle is exercised the
way it happens on a participant's PC.
"""

from __future__ import annotations

import asyncio
import sqlite3

from riftrec.config import RecorderConfig
from riftrec.rte.supervisor import SupervisorService


def _beat(bpm: int = 70) -> bytes:
    """flags=0x10 (RR present, uint8 HR) + one 1000 ms interval."""
    return bytes([0x10, bpm, 0x00, 0x04])


class _Strap:
    """Strap that connects on the first try; the test pushes its beats."""

    is_connected = False
    address = "FA:KE:00:00:00:01"
    name = "Polar H10 FAKE"

    async def connect(self, device) -> None:
        self.is_connected = True

    async def subscribe(self, uuid, callback) -> None:
        self.callback = callback

    async def read(self, uuid: str) -> bytes:
        if uuid.startswith("00002a19"):
            return bytes([90])
        raise RuntimeError("characteristic not available")

    async def disconnect(self) -> None:
        pass


def _frame(event_id: int) -> dict:
    return {
        "gameData": {"gameTime": 30.0},
        "activePlayer": {"summonerName": "P"},
        "allPlayers": [{"summonerName": "P",
                        "scores": {"kills": 0, "deaths": 0, "assists": 0, "creepScore": 0}}],
        "events": {"Events": [{"EventID": event_id, "EventName": "GameStart",
                               "EventTime": 0.0}]},
    }


def _run(db, script, on_ready=None):
    """Run the supervisor through `script`: one entry per watch tick.

    Each entry is the Live Client API answer for that tick (None = no match);
    a beat is pushed on every tick, as a worn strap would.
    """
    strap = _Strap()
    stop = asyncio.Event()
    ticks = {"n": 0}

    async def fetch():
        n = ticks["n"]
        ticks["n"] += 1
        if hasattr(strap, "callback"):
            strap.callback(_beat())
        if n >= len(script) - 1:
            stop.set()
        return script[min(n, len(script) - 1)]

    svc = SupervisorService(
        RecorderConfig(participant_id="P01", db_path=db, poll_interval_s=0.0,
                       reconnect_backoff_s=0.0, flush_interval_s=0.0,
                       snapshot_interval_s=0.0),
        transport=strap, riot_fetch=fetch, game_probe=lambda: False)
    if on_ready is not None:
        svc.on_ready_for_checkin = on_ready
    asyncio.run(svc.run(stop))
    return svc


def _sessions(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT session_id, kind, session_index, utc_offset_min, ended_utc "
            "FROM session ORDER BY started_utc").fetchall()
    finally:
        conn.close()


def _count(db, table, session_id):
    conn = sqlite3.connect(db)
    try:
        (n,) = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE session_id=?", (session_id,)
        ).fetchone()
        return n
    finally:
        conn.close()


def test_beats_between_matches_are_kept_as_idle_sessions(tmp_path) -> None:
    db = tmp_path / "evening.sqlite"
    svc = _run(db, [None, None, _frame(1), _frame(1), None, None])

    rows = _sessions(db)
    assert [r[1] for r in rows] == ["idle", "match", "idle"]
    # Only the match counts: "match 1" stays match 1 however long the lobby.
    assert [r[2] for r in rows] == [None, 1, None]
    assert all(r[3] is not None for r in rows)       # EW-101 offset everywhere
    assert all(r[4] is not None for r in rows)       # every row was closed
    assert svc.matches_recorded == 1

    idle1, match, idle2 = (r[0] for r in rows)
    for sid in (idle1, match, idle2):
        assert _count(db, "hr_sample", sid) > 0
        assert _count(db, "rr_interval", sid) > 0
    # Game data belongs to the match only.
    assert _count(db, "game_event", match) == 1
    assert _count(db, "game_event", idle1) == 0
    assert _count(db, "game_raw", idle2) == 0


def test_a_match_right_at_the_start_opens_no_empty_idle_row(tmp_path) -> None:
    """Started RiftRec mid-game: no zero-length idle stretch before it."""
    db = tmp_path / "late.sqlite"
    _run(db, [_frame(1), _frame(1), None])
    assert [r[1] for r in _sessions(db)] == ["match", "idle"]


def test_checkin_is_offered_once_per_idle_stretch_and_never_mid_match(tmp_path) -> None:
    db = tmp_path / "offer.sqlite"
    offers = []
    script = [None, None, None, _frame(1), _frame(1), None, None]

    def on_ready():
        offers.append(len(offers))

    _run(db, script, on_ready=on_ready)
    # Once before the match, once after it - not on every idle tick.
    assert len(offers) == 2


def test_checkin_waits_for_beats(tmp_path) -> None:
    """No RR yet -> no offer: a resting minute without beats is wasted."""
    from riftrec.rte.supervisor import SupervisorService as S

    svc = S(RecorderConfig(participant_id="P01", db_path=tmp_path / "x.sqlite"))
    svc._h10_up = True
    offers = []
    svc.on_ready_for_checkin = lambda: offers.append(1)
    svc._offer_checkin()
    assert offers == []
    svc._open_session("idle")
    svc._on_hr(_beat())
    svc._offer_checkin()
    svc._offer_checkin()
    assert offers == [1]
    svc._close_session()


def test_a_note_between_matches_goes_to_the_last_match(tmp_path) -> None:
    db = tmp_path / "note.sqlite"
    svc = SupervisorService(RecorderConfig(participant_id="P01", db_path=db))
    match = svc._open_session()
    svc._close_session()
    svc._open_session("idle")
    assert svc.add_note("post-game: lag spike") is True
    svc._close_session()

    conn = sqlite3.connect(db)
    try:
        notes = dict(conn.execute("SELECT session_id, notes FROM session"))
    finally:
        conn.close()
    assert "lag spike" in notes[match]
    assert sum(1 for n in notes.values() if n) == 1


def test_closing_an_idle_stretch_does_not_count_as_a_match(tmp_path) -> None:
    from riftrec.rte.status import Activity

    svc = SupervisorService(
        RecorderConfig(participant_id="P01", db_path=tmp_path / "i.sqlite"))
    svc._open_session("idle")
    assert svc.match_live is False
    assert svc.current_session_id is not None
    svc._close_session()
    assert svc.matches_recorded == 0
    assert svc.report.state.activity is not Activity.RECORDING
