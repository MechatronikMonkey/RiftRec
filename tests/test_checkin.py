"""Daily check-in (schema 5): the questions, when they are asked, what is stored.

No display needed: the window is replaced by a function that plays the
participant.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest

from riftrec.app import checkin_questions as cq
from riftrec.app.checkin import CheckinController
from riftrec.app.prefs import Prefs
from riftrec.config import RecorderConfig
from riftrec.storage.sqlite_sink import CHECKIN_ANSWER_COLUMNS, write_checkin
from riftrec.model import Checkin

FULL = {
    "sleep_quality": 2, "sleep_hours": 6, "alcohol": "none", "caffeine": "1",
    "nicotine": "no", "last_meal": "1-3h", "exercise": "hard",
    "exercise_ended": "2-6h", "ill": "no", "feeling": 3, "adhd_medication": "yes",
}


# -- the questionnaire ------------------------------------------------------


def test_question_ids_are_exactly_the_table_columns() -> None:
    assert cq.QUESTION_IDS == CHECKIN_ANSWER_COLUMNS


def test_every_question_says_what_counts_except_the_follow_up() -> None:
    """The point of the wording: no question without its time window/help."""
    for q in cq.QUESTIONS:
        if q.hidden_if is None:
            assert q.help, q.id


def test_exercise_follow_up_only_when_there_was_exercise() -> None:
    answers = dict(FULL, exercise="none")
    del answers["exercise_ended"]
    assert cq.missing(answers) == []
    assert "exercise_ended" in cq.missing({**answers, "exercise": "light"})


def test_a_stale_follow_up_is_dropped_when_exercise_is_set_back_to_none() -> None:
    stored = cq.cleaned(dict(FULL, exercise="none"))
    assert "exercise_ended" not in stored


def test_unknown_answer_codes_are_refused() -> None:
    with pytest.raises(ValueError):
        cq.cleaned(dict(FULL, alcohol="lots"))


def test_scales_keep_their_integer_codes() -> None:
    stored = cq.cleaned(FULL)
    assert stored["sleep_quality"] == 2 and isinstance(stored["sleep_hours"], int)


@pytest.mark.parametrize("clock, day", [
    ("2026-10-26 19:30", "2026-10-26"),
    ("2026-10-27 01:30", "2026-10-26"),   # still Monday's evening
    ("2026-10-27 05:00", "2026-10-27"),
])
def test_the_study_day_starts_at_five(clock, day) -> None:
    assert cq.study_day(datetime.fromisoformat(clock)).isoformat() == day


# -- when it is asked -------------------------------------------------------


class _Store:
    def __init__(self, prefs: Prefs | None = None) -> None:
        self.prefs = prefs or Prefs(participant_id="P01")

    def load(self) -> Prefs:
        return self.prefs

    def save(self, prefs: Prefs) -> None:
        self.prefs = prefs


class _Service:
    match_live = False
    current_session_id = None


def _controller(tmp_path, store, now="2026-10-26 19:30", play=None):
    shown = []
    done = threading.Event()

    def show(run):
        shown.append(run)
        if play is not None:
            play(run)
        done.set()

    ctl = CheckinController(
        RecorderConfig(participant_id="P01", db_path=tmp_path / "c.sqlite"),
        _Service(), show=show, now=lambda: datetime.fromisoformat(now),
        load=store.load, save=store.save)
    return ctl, shown, done


def _wait(done, ctl=None) -> None:
    """Until the window ran and - with `ctl` - its thread fully wound down."""
    import time

    assert done.wait(5)
    if ctl is not None:
        deadline = time.monotonic() + 5
        while ctl._open and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not ctl._open


def test_asked_once_a_day_and_not_again_after_submitting(tmp_path) -> None:
    store = _Store()
    ctl, shown, done = _controller(tmp_path, store, play=lambda r: r.submit(FULL))
    ctl.on_ready()
    _wait(done, ctl)
    assert len(shown) == 1
    assert store.prefs.last_checkin_day == "2026-10-26"
    done.clear()
    ctl.on_ready()                     # after the next match: already done
    assert not done.wait(0.2)
    assert len(shown) == 1


def test_closed_unanswered_it_comes_back_once_more_then_rests(tmp_path) -> None:
    store = _Store()
    ctl, shown, done = _controller(tmp_path, store)   # closes without answering
    for expect_window in (True, True, False):
        done.clear()
        ctl.on_ready()
        if expect_window:
            _wait(done, ctl)
    assert len(shown) == cq.MAX_PROMPTS_PER_DAY == 2


def test_a_new_study_day_asks_again(tmp_path) -> None:
    store = _Store(Prefs(participant_id="P01", last_checkin_day="2026-10-25",
                         prompt_day="2026-10-25", prompts=2))
    ctl, shown, done = _controller(tmp_path, store)
    ctl.on_ready()
    _wait(done)
    assert len(shown) == 1


def test_the_tray_can_open_it_even_when_done(tmp_path) -> None:
    store = _Store(Prefs(participant_id="P01", last_checkin_day="2026-10-26"))
    ctl, shown, done = _controller(tmp_path, store)
    ctl.open_manual()
    _wait(done)
    assert len(shown) == 1


# -- what is stored ---------------------------------------------------------


def _rows(db):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM checkin")]
    finally:
        conn.close()


def test_answers_and_resting_window_end_up_in_one_row(tmp_path) -> None:
    def play(run):
        run.submit(FULL)
        run.rest_started()
        run.rest_finished("completed")

    store = _Store()
    ctl, _shown, done = _controller(tmp_path, store, play=play)
    ctl.on_ready()
    _wait(done, ctl)

    (row,) = _rows(tmp_path / "c.sqlite")
    assert row["participant_id"] == "P01"
    assert row["questionnaire_version"] == cq.QUESTIONNAIRE_VERSION
    assert row["sleep_quality"] == 2 and row["adhd_medication"] == "yes"
    assert row["exercise_ended"] == "2-6h"
    assert row["rest_status"] == "completed"
    start = datetime.fromisoformat(row["rest_start_utc"])
    end = datetime.fromisoformat(row["rest_end_utc"])
    assert start <= end
    assert row["submitted_utc"] is not None and row["utc_offset_min"] is not None


def test_closing_without_answers_leaves_a_row_that_says_so(tmp_path) -> None:
    ctl, _shown, done = _controller(tmp_path, _Store())
    ctl.on_ready()
    _wait(done, ctl)                   # dismissed() runs after show() returns
    (row,) = _rows(tmp_path / "c.sqlite")
    assert row["submitted_utc"] is None and row["sleep_quality"] is None


def test_any_question_may_be_skipped(tmp_path) -> None:
    """The participant information promises it; a blank is stored as NULL."""
    partial = {k: v for k, v in FULL.items() if k != "adhd_medication"}
    store = _Store()
    ctl, _shown, done = _controller(tmp_path, store,
                                    play=lambda r: r.submit(partial))
    ctl.on_ready()
    _wait(done, ctl)
    (row,) = _rows(tmp_path / "c.sqlite")
    assert row["adhd_medication"] is None and row["sleep_quality"] == 2
    assert row["submitted_utc"] is not None
    assert store.prefs.last_checkin_day == "2026-10-26"


def test_a_file_holding_only_a_checkin_is_kept(tmp_path) -> None:
    from riftrec.storage.sqlite_sink import discard_if_unused

    db = tmp_path / "only_checkin.sqlite"
    write_checkin(db, Checkin(checkin_id="c", questionnaire_version=1,
                              opened_utc=datetime.now(timezone.utc).isoformat()))
    assert discard_if_unused(db) is False and db.exists()


def test_write_checkin_rejects_answers_the_table_has_no_column_for(tmp_path) -> None:
    rec = Checkin(checkin_id="x", questionnaire_version=1,
                  opened_utc=datetime.now(timezone.utc).isoformat(),
                  answers={"mood_ring": 1})
    with pytest.raises(ValueError):
        write_checkin(tmp_path / "x.sqlite", rec)


def test_a_schema_4_file_gains_the_new_columns(tmp_path) -> None:
    """Additive migration, like every schema step before it."""
    from riftrec.model import SessionMeta
    from riftrec.storage.sqlite_sink import SqliteSink

    db = tmp_path / "old.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE session (session_id TEXT PRIMARY KEY, participant_id TEXT,"
        " session_index INTEGER, started_utc TEXT NOT NULL, ended_utc TEXT,"
        " mono_anchor_ns INTEGER NOT NULL, app_version TEXT NOT NULL,"
        " schema_version INTEGER NOT NULL, notes TEXT, active_riot_id TEXT);")
    conn.close()

    sink = SqliteSink(db)
    sink.open_session(SessionMeta(
        session_id="s", participant_id="P01", session_index=None,
        started_utc=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        mono_anchor_ns=0, app_version="0.2.0", schema_version=5,
        kind="idle", utc_offset_min=60))
    sink.close_session(datetime.now(timezone.utc).isoformat())

    conn = sqlite3.connect(db)
    try:
        assert conn.execute(
            "SELECT kind, utc_offset_min FROM session").fetchone() == ("idle", 60)
    finally:
        conn.close()


def test_schema_sql_alone_creates_the_schema_5_columns(tmp_path) -> None:
    """Without the migration in open_session: schema.sql itself must be right.

    A missing comma once turned `kind` into part of active_riot_id's type -
    no error, and the migration quietly papered over it.
    """
    from riftrec.storage import sqlite_sink

    conn = sqlite3.connect(tmp_path / "fresh.sqlite")
    try:
        conn.executescript(sqlite_sink._SCHEMA_PATH.read_text(encoding="utf-8"))
        cols = {r[1]: r[2] for r in conn.execute("PRAGMA table_info(session)")}
    finally:
        conn.close()
    assert cols["kind"] == "TEXT"
    assert cols["utc_offset_min"] == "INTEGER"
    assert cols["active_riot_id"] == "TEXT"
