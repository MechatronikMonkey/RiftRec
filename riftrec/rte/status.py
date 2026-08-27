r"""Plain-language status: what the recorder is doing, and why (EW-89).

Until now the tray carried a single coloured dot. A start attempt with the strap
still on the desk produced 72 silent reconnect attempts - correct behaviour per
EW-42, but from the outside indistinguishable from "Bluetooth is off" or "the
game was never detected", and the explanation existed only in
``%APPDATA%\RiftRec\riftrec.log``, which no participant opens.

The recorder already knows the reason, so it publishes it: `SupervisorService`
fills in a `StatusReport`, the tray and the status window render it. This module
is pure and I/O-free - the wording lives in one place so both front-ends say the
same thing, and it can be unit-tested without hardware or a running match.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional

from .state import RecorderState


class Activity(enum.Enum):
    """What the recorder is waiting for or doing - finer than RecorderState.

    RecorderState drives the icon colour; Activity drives the sentence. Several
    activities share one state - CONNECTING covers "strap not on", "Bluetooth
    off" and "strap lost mid-match" - and those are exactly the cases a
    participant can only tell apart if we say which one it is.
    """

    STARTING = "starting"
    WAITING_FOR_STRAP = "waiting_for_strap"          # first connect not done yet
    STRAP_NOT_FOUND = "strap_not_found"              # scan found no Polar
    BLUETOOTH_UNAVAILABLE = "bluetooth_unavailable"  # radio off / no adapter
    STRAP_LOST = "strap_lost"                        # was connected, dropped
    WAITING_FOR_MATCH = "waiting_for_match"          # strap up, no game yet
    RECORDING = "recording"
    NO_HEART_RATE = "no_heart_rate"          # match live, nothing arriving
    NO_SKIN_CONTACT = "no_skin_contact"      # HR arriving, RR stopped
    GAME_NOT_VISIBLE = "game_not_visible"    # League up, API silent
    STORAGE_FAILED = "storage_failed"        # cannot write the file
    NO_PARTICIPANT_ID = "no_participant_id"
    STOPPED = "stopped"


@dataclass(frozen=True)
class StatusReport:
    """One rendering-ready snapshot of the recorder's situation."""

    state: RecorderState = RecorderState.IDLE
    activity: Activity = Activity.STARTING
    attempts: int = 0                  # consecutive failed connect attempts
    match_index: Optional[int] = None  # session_index of the live match, if any
    cause: Optional[str] = None        # raw technical message, for log/details

    @property
    def headline(self) -> str:
        return headline_for(self)

    @property
    def detail(self) -> str:
        return detail_for(self)


# -- classification ------------------------------------------------------

def classify_connect_error(message: str) -> Activity:
    """Map a BLE connect failure to something a participant can act on.

    Two failures need two different actions: putting the strap on, versus
    switching Bluetooth on. Everything else falls back to "strap not found",
    which is the overwhelmingly common case and whose advice (put it on, moisten
    the electrodes) is harmless when the real cause was something else.
    """
    text = (message or "").lower()
    if "bluetooth" in text and any(
        w in text
        for w in ("off", "disabled", "unavailable", "not available", "no adapter")
    ):
        return Activity.BLUETOOTH_UNAVAILABLE
    return Activity.STRAP_NOT_FOUND


# -- wording -------------------------------------------------------------

_HEADLINES: dict[Activity, str] = {
    Activity.STARTING: "Starting…",
    Activity.WAITING_FOR_STRAP: "Waiting for the chest strap",
    Activity.STRAP_NOT_FOUND: "Chest strap not found",
    Activity.BLUETOOTH_UNAVAILABLE: "Bluetooth is not available",
    Activity.STRAP_LOST: "Chest strap connection lost",
    Activity.WAITING_FOR_MATCH: "Ready — waiting for a match",
    Activity.RECORDING: "Recording",
    Activity.NO_HEART_RATE: "Recording — but no heart rate",
    Activity.NO_SKIN_CONTACT: "Recording — strap has lost contact",
    Activity.GAME_NOT_VISIBLE: "League is running, the game is not visible",
    Activity.STORAGE_FAILED: "Cannot save the recording",
    Activity.NO_PARTICIPANT_ID: "Not recording — no participant ID",
    Activity.STOPPED: "Stopped",
}


def headline_for(report: StatusReport) -> str:
    """Short line: tray menu title and the big line in the status window."""
    if report.activity is Activity.RECORDING and report.match_index:
        return f"Recording match {report.match_index}"
    return _HEADLINES.get(report.activity, "RiftRec")


def _tries(report: StatusReport) -> str:
    """Render " (attempt N)" once retrying, so a stuck state looks stuck."""
    return f" (attempt {report.attempts})" if report.attempts > 1 else ""


def detail_for(report: StatusReport) -> str:
    """One sentence naming the reason and what the participant should do."""
    a = report.activity
    if a is Activity.STARTING:
        return "RiftRec is starting up."
    if a is Activity.WAITING_FOR_STRAP:
        return ("Put the H10 on and moisten the electrodes — "
                "RiftRec connects to it by itself.")
    if a is Activity.STRAP_NOT_FOUND:
        return ("Put the H10 on and moisten the electrodes, then give it a moment — "
                f"RiftRec keeps trying{_tries(report)}.")
    if a is Activity.BLUETOOTH_UNAVAILABLE:
        return ("Switch Bluetooth on in the Windows settings — "
                f"RiftRec keeps trying{_tries(report)}.")
    if a is Activity.STRAP_LOST:
        tail = (" The match is still being recorded, only the heart rate is paused."
                if report.match_index else "")
        return ("Heart rate is paused. Check the strap is still on and stay near "
                f"the PC — RiftRec keeps trying{_tries(report)}.{tail}")
    if a is Activity.WAITING_FOR_MATCH:
        return ("The strap is connected. Start a game — "
                "recording begins on its own when the match does.")
    if a is Activity.RECORDING:
        return "Heart rate and game data are being saved."
    if a is Activity.NO_HEART_RATE:
        return ("The match is being recorded, but no heart rate is arriving — "
                "is the strap on, and are the electrodes moistened?")
    if a is Activity.NO_SKIN_CONTACT:
        return ("The strap is connected but no longer reading your heartbeat. "
                "Push it down against the skin and moisten the electrodes.")
    if a is Activity.GAME_NOT_VISIBLE:
        return ("League is running, but RiftRec cannot read the live game data — "
                "nothing is being recorded. Please report this.")
    if a is Activity.STORAGE_FAILED:
        return ("The folder RiftRec saves to is not reachable. Data is kept in "
                "memory and written as soon as the folder is back.")
    if a is Activity.NO_PARTICIPANT_ID:
        return ("Enter the participant ID you were given in the settings, "
                "then start RiftRec again.")
    if a is Activity.STOPPED:
        return "RiftRec is not recording. Start it again from the Start menu."
    return ""


# Windows' notification-area tooltip (Shell_NotifyIcon szTip) is capped at 128
# characters including the terminator; anything longer is dropped silently, so
# the whole tooltip would vanish rather than merely be cut off.
TOOLTIP_LIMIT = 127


def tooltip_text(
    report: StatusReport, battery: Optional[str] = None, limit: int = TOOLTIP_LIMIT
) -> str:
    """Hover text: headline, then as much of the reason as Windows will show.

    The reason outranks the battery line: the battery is only appended when it
    still leaves room for a useful chunk of the sentence, and the sentence is
    ellipsised rather than letting Windows drop the tooltip entirely.
    """
    head = f"RiftRec — {report.headline}"[:limit]
    budget = limit - len(head)
    batt = f"\n{battery}" if battery else ""
    if batt and budget - len(batt) < 21:   # battery would crowd out the reason
        batt = ""
    room = budget - len(batt) - 1          # -1 for the newline before the detail
    detail = report.detail
    if not detail or room < 12:
        return head + batt
    if len(detail) > room:
        detail = detail[: room - 1].rstrip() + "…"
    return f"{head}\n{detail}{batt}"
