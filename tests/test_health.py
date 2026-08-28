"""EW-89: catch the failures that look like success.

"It is installed, it records nothing, and nobody notices for four weeks" is the
failure that costs a measurement wave, because from the outside it is
indistinguishable from everything working. These tests pin each detector,
including the cases where it must stay quiet - a participant who learns to
ignore RiftRec's notifications is worse off than one who never got any.
"""

from __future__ import annotations

from riftrec.rte.health import (
    Issue,
    SEVERITY,
    Signals,
    Thresholds,
    active_issues,
    notification_for,
    worst,
)

T = Thresholds()
NOW = 10_000.0


def t_up() -> float:
    """Long enough for the game to count as blind."""
    return T.game_silence_s + 1


def test_healthy_match_reports_nothing() -> None:
    assert active_issues(Signals(
        now=NOW, match_live=True, match_started=NOW - 600,
        last_hr=NOW - 1, last_rr=NOW - 1, last_game_data=NOW - 1,
    )) == set()


def test_idle_between_matches_reports_nothing() -> None:
    """No match, League not running - the normal state most of the time."""
    assert active_issues(Signals(now=NOW, match_live=False, league_running=False)) == set()


# -- strap on the desk / dry electrodes -------------------------------------

def test_no_heart_rate_during_a_match_is_flagged() -> None:
    s = Signals(now=NOW, match_live=True, match_started=NOW - T.hr_silence_s - 1)
    assert Issue.NO_HEART_RATE in active_issues(s)


def test_a_fresh_match_is_given_time_before_complaining() -> None:
    """Recording starts the moment the match does; the strap may connect a few
    seconds later. Alarming instantly would be wrong every single game."""
    s = Signals(now=NOW, match_live=True, match_started=NOW - 5)
    assert active_issues(s) == set()


def test_a_dropped_link_is_not_reported_as_missing_heart_rate() -> None:
    """The link supervisor already says something more useful ("check the strap
    is still on" rather than "put it on"), and gaps it. Two alarms for one fact
    is how notifications get ignored."""
    s = Signals(now=NOW, match_live=True, match_started=NOW - 600,
                strap_connected=False)
    assert active_issues(s) == set()


def test_heart_rate_missing_only_matters_during_a_match() -> None:
    s = Signals(now=NOW, match_live=False, last_hr=None, league_running=False)
    assert Issue.NO_HEART_RATE not in active_issues(s)


# -- lost skin contact: the frozen-HR trap ----------------------------------

def test_heart_rate_without_rr_is_lost_skin_contact() -> None:
    """The H10 keeps sending a frozen but plausible HR after losing contact and
    only drops to 0 about ten seconds later, so "a number is arriving" proves
    nothing. Absence of RR is the honest criterion (verified 21.08.2026)."""
    s = Signals(now=NOW, match_live=True, match_started=NOW - 600,
                last_hr=NOW - 1, last_rr=NOW - T.rr_silence_s - 1)
    assert Issue.NO_SKIN_CONTACT in active_issues(s)


def test_brief_rr_hiccup_is_not_reported() -> None:
    s = Signals(now=NOW, match_live=True, match_started=NOW - 600,
                last_hr=NOW - 1, last_rr=NOW - 5)
    assert active_issues(s) == set()


def test_contact_is_not_questioned_while_no_heart_rate_arrives_at_all() -> None:
    """NO_HEART_RATE already covers it; both at once would be noise."""
    s = Signals(now=NOW, match_live=True, match_started=NOW - 600, last_hr=None)
    issues = active_issues(s)
    assert issues == {Issue.NO_HEART_RATE}


# -- the deaf recorder: League up, API silent -------------------------------

def test_league_running_without_game_data_is_flagged() -> None:
    """The scariest one: the tray says "ready, waiting for a match" while
    matches are being played. Nothing else distinguishes it from idling."""
    s = Signals(now=NOW, match_live=False, league_running=True,
                league_up_since=NOW - t_up(), last_game_data=None)
    assert Issue.GAME_NOT_VISIBLE in active_issues(s)


def test_league_just_started_is_given_time() -> None:
    """`League of Legends.exe` is up during loading, before the API answers."""
    s = Signals(now=NOW, match_live=False, league_running=True,
                league_up_since=NOW - 10, last_game_data=None)
    assert active_issues(s) == set()


def test_the_end_of_game_screen_does_not_raise_a_false_alarm() -> None:
    """The regression from 28.08.2026. After a match the game process lingers
    on the end-of-game screen while the API has already stopped answering, so
    the old "no data recently?" rule fired two minutes later and told a player
    who had just been recorded perfectly that nothing was being recorded.

    The question is per game process: has data arrived since *this* game
    started? Here it has.
    """
    s = Signals(now=NOW, match_live=False, league_running=True,
                league_up_since=NOW - 1800,      # process up since before the match
                last_game_data=NOW - 300)        # data flowed during the match
    assert active_issues(s) == set()


def test_a_blind_second_game_is_still_caught() -> None:
    """`League of Legends.exe` is one process per match, so a new game gets a
    fresh judgement - the data from the previous match does not vouch for it."""
    s = Signals(now=NOW, match_live=False, league_running=True,
                league_up_since=NOW - t_up(),    # new process, this game
                last_game_data=NOW - 1800)       # last data was the game before
    assert Issue.GAME_NOT_VISIBLE in active_issues(s)


def test_unknown_game_state_stays_quiet() -> None:
    """If the process list could not be read, we do not guess."""
    s = Signals(now=NOW, match_live=False, league_running=None, last_game_data=None)
    assert active_issues(s) == set()


def test_a_running_game_we_never_stamped_stays_quiet() -> None:
    """Without a start time there is nothing to measure against, and guessing
    would mean alarming on the very first tick after RiftRec starts."""
    s = Signals(now=NOW, match_live=False, league_running=True,
                league_up_since=None, last_game_data=None)
    assert active_issues(s) == set()


def test_no_game_complaint_while_a_match_is_being_recorded() -> None:
    s = Signals(now=NOW, match_live=True, match_started=NOW - 60,
                last_hr=NOW - 1, last_rr=NOW - 1,
                league_running=True, last_game_data=None)
    assert Issue.GAME_NOT_VISIBLE not in active_issues(s)


# -- storage and battery -----------------------------------------------------

def test_storage_error_is_flagged() -> None:
    s = Signals(now=NOW, match_live=False, storage_error="disk gone")
    assert Issue.STORAGE_FAILED in active_issues(s)


def test_low_battery_is_flagged_and_a_full_one_is_not() -> None:
    assert Issue.BATTERY_LOW in active_issues(
        Signals(now=NOW, match_live=False, battery_pct=T.battery_pct))
    assert Issue.BATTERY_LOW not in active_issues(
        Signals(now=NOW, match_live=False, battery_pct=T.battery_pct + 1))
    assert Issue.BATTERY_LOW not in active_issues(
        Signals(now=NOW, match_live=False, battery_pct=None))


# -- picking the one line the tray shows ------------------------------------

def test_worst_prefers_the_more_serious_issue() -> None:
    assert worst({Issue.NO_SKIN_CONTACT, Issue.STORAGE_FAILED}) is Issue.STORAGE_FAILED
    assert worst({Issue.BATTERY_LOW, Issue.NO_HEART_RATE}) is Issue.NO_HEART_RATE
    assert worst(set()) is None


def test_severity_covers_every_issue() -> None:
    """A new issue without a rank would silently never reach the tray line."""
    assert set(SEVERITY) == set(Issue)


# -- wording -----------------------------------------------------------------

def test_every_issue_has_words_for_starting_and_ending() -> None:
    for issue in Issue:
        for raised in (True, False):
            title, message = notification_for(issue, raised)
            assert title.strip(), (issue, raised)
            assert message.strip(), (issue, raised)


def test_the_strap_notifications_say_what_to_do() -> None:
    _title, message = notification_for(Issue.NO_HEART_RATE)
    assert "electrodes" in message
    _title, message = notification_for(Issue.NO_SKIN_CONTACT)
    assert "electrodes" in message


def test_the_blind_game_notification_asks_to_be_reported() -> None:
    """Nothing the participant can fix - but we need to hear about it."""
    _title, message = notification_for(Issue.GAME_NOT_VISIBLE)
    assert "nothing is being recorded" in message.lower()
    assert "tell us" in message.lower()
