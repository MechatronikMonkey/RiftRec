"""EW-89: the tray must name the reason, not just show a colour.

A start attempt with the strap on the desk produced 72 identical reconnect
attempts behind a single amber dot. These tests pin the wording that replaces
it - what the participant is told, and that Windows will actually display it.
"""

from __future__ import annotations

from riftrec.rte.state import RecorderState
from riftrec.rte.status import (
    Activity,
    StatusReport,
    TOOLTIP_LIMIT,
    classify_connect_error,
    detail_for,
    headline_for,
    tooltip_text,
)


def _report(activity: Activity, **kw) -> StatusReport:
    return StatusReport(state=kw.pop("state", RecorderState.CONNECTING),
                        activity=activity, **kw)


# -- classification: two failures, two different things to do ---------------

def test_missing_strap_is_classified_as_strap_not_found() -> None:
    """The real message from hal/ble_bleak.py's failed scan."""
    assert classify_connect_error(
        "No matching BLE device found. Is the H10 worn and are the electrodes "
        "moistened?"
    ) is Activity.STRAP_NOT_FOUND


def test_radio_off_is_classified_separately() -> None:
    """Switching Bluetooth on is different advice from putting the strap on."""
    for message in (
        "Bluetooth device is turned off",
        "Bluetooth adapter is disabled",
        "Bluetooth is not available on this system",
    ):
        assert classify_connect_error(message) is Activity.BLUETOOTH_UNAVAILABLE, message


def test_unknown_failure_falls_back_to_the_common_case() -> None:
    assert classify_connect_error("WinError -2147020577") is Activity.STRAP_NOT_FOUND
    assert classify_connect_error("") is Activity.STRAP_NOT_FOUND


# -- wording ----------------------------------------------------------------

def test_every_activity_has_a_headline_and_a_reason() -> None:
    """No activity may fall through to an empty line in the tray menu."""
    for activity in Activity:
        report = _report(activity)
        assert headline_for(report).strip(), activity
        assert detail_for(report).strip(), activity


def test_waiting_for_strap_says_what_to_do() -> None:
    text = detail_for(_report(Activity.STRAP_NOT_FOUND))
    assert "electrodes" in text and "H10" in text


def test_bluetooth_off_points_at_windows_settings_not_the_strap() -> None:
    text = detail_for(_report(Activity.BLUETOOTH_UNAVAILABLE))
    assert "Bluetooth" in text
    assert "electrodes" not in text


def test_retry_count_appears_once_it_is_really_retrying() -> None:
    """The 72-attempts case: a stuck recorder has to look stuck."""
    assert "attempt" not in detail_for(_report(Activity.STRAP_NOT_FOUND, attempts=1))
    assert "attempt 72" in detail_for(_report(Activity.STRAP_NOT_FOUND, attempts=72))


def test_strap_lost_mid_match_says_the_match_is_still_recorded() -> None:
    """Reassurance matters here: only HR pauses, the match keeps recording."""
    live = detail_for(_report(Activity.STRAP_LOST, attempts=3, match_index=2))
    between = detail_for(_report(Activity.STRAP_LOST, attempts=3))
    assert "still being recorded" in live
    assert "still being recorded" not in between


def test_recording_headline_names_the_match() -> None:
    report = _report(Activity.RECORDING, state=RecorderState.RECORDING, match_index=3)
    assert headline_for(report) == "Recording match 3"


def test_waiting_for_match_tells_them_to_just_start_a_game() -> None:
    text = detail_for(_report(Activity.WAITING_FOR_MATCH, state=RecorderState.READY))
    assert "connected" in text and "on its own" in text


# -- tooltip: Windows drops anything over the cap ---------------------------

def test_tooltip_never_exceeds_what_windows_will_show() -> None:
    for activity in Activity:
        for battery in (None, "Battery: 87%", "Battery: 12% - replace soon"):
            text = tooltip_text(_report(activity, attempts=999, match_index=99), battery)
            assert len(text) <= TOOLTIP_LIMIT, (activity, battery, len(text))


def test_tooltip_leads_with_the_headline_and_carries_the_reason() -> None:
    text = tooltip_text(_report(Activity.STRAP_NOT_FOUND, attempts=72), "Battery: 87%")
    lines = text.split("\n")
    assert lines[0] == "RiftRec — Chest strap not found"
    assert "electrodes" in lines[1]


def test_tooltip_keeps_the_reason_when_the_battery_would_crowd_it_out() -> None:
    """The reason outranks the battery line - dropping it loses nothing."""
    long_battery = "Battery: 100% " + "x" * 90
    text = tooltip_text(_report(Activity.STRAP_NOT_FOUND, attempts=5), long_battery)
    assert long_battery not in text
    assert "electrodes" in text
    assert len(text) <= TOOLTIP_LIMIT


# -- the facts block in the status window ---------------------------------

def test_the_battery_line_is_not_labelled_twice() -> None:
    """battery_text() already reads "Battery: 100%"; prefixing it produced
    "Strap battery: Battery: 100%" in the window a participant is asked to open.
    """
    from riftrec.app.status_window import _facts_text

    line = [l for l in _facts_text({"battery": "Battery: 100%"}).splitlines()
            if "attery" in l]
    assert line == ["Strap battery: 100%"], line


def test_the_battery_line_keeps_its_warning() -> None:
    from riftrec.app.status_window import _facts_text

    text = _facts_text({"battery": "Battery: 22% - replace soon"})
    assert "Strap battery: 22% - replace soon" in text


def test_an_unknown_battery_still_reads_as_a_sentence() -> None:
    from riftrec.app.status_window import _facts_text

    assert "Strap battery: unknown" in _facts_text({})
