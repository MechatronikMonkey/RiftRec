"""Detect the ways a recording fails without looking broken (EW-89).

The dangerous failure in an unattended study is not "it will not start" -
somebody reports that within minutes. It is **"it is installed, it records
nothing, and nobody notices for four weeks"**, discovered when the files come in
and the measurement wave is over. Those games cannot be replayed.

Every situation below looks completely normal from the outside: the app is
running, the icon is there, the participant is playing.

* the strap is not worn, or the electrodes are dry - no heart rate at all
* the strap is connected but has lost skin contact - the H10 keeps sending a
  *frozen* HR while RR intervals stop, so "there is a number" proves nothing
  (verified 21.08.2026, see schema.sql)
* League is running but the Live Client Data API never answers - a firewall, an
  overlay or a Riot change. The recorder sits on "ready, waiting for a match"
  while matches are being played
* the storage folder went away - an unplugged drive, a cloud folder that stopped
  syncing
* the strap battery is dying

This module only judges; it does not measure and does not act. The supervisor
collects the timestamps, calls `active_issues()` on every tick, and turns the
difference between two calls into a tray notification - the participant is told
while they can still fix it, instead of us finding out in November.

Pure and clock-free: `now` is passed in, so every threshold is testable without
waiting for it.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional


class Issue(enum.Enum):
    """A reason to believe the data being recorded is not usable."""

    STORAGE_FAILED = "storage_failed"
    GAME_NOT_VISIBLE = "game_not_visible"
    NO_HEART_RATE = "no_heart_rate"
    NO_SKIN_CONTACT = "no_skin_contact"
    BATTERY_LOW = "battery_low"


# Most severe first. Several issues can be true at once (a dead strap during a
# blind game), and the tray shows one line - it shows this one.
SEVERITY = [
    Issue.STORAGE_FAILED,
    Issue.GAME_NOT_VISIBLE,
    Issue.NO_HEART_RATE,
    Issue.NO_SKIN_CONTACT,
    Issue.BATTERY_LOW,
]


@dataclass(frozen=True)
class Thresholds:
    """How long a symptom has to persist before the participant is bothered.

    Generous on purpose. A false alarm during a ranked game costs trust, and a
    participant who learns to ignore the notifications is worse off than one who
    never got them.
    """

    # The H10 notifies about once a second. A minute of silence during a live
    # match is not a hiccup.
    hr_silence_s: float = 60.0

    # RR intervals arrive with every notification while contact is good. They
    # stop roughly 10 s before the frozen HR gives way to zero, so this is the
    # earliest honest signal that the electrodes lost the skin.
    rr_silence_s: float = 30.0

    # `League of Legends.exe` is already up during loading, before the Live
    # Client Data API starts answering, and lingers briefly after a game. Two
    # minutes clears both ends without missing a genuinely blind client.
    game_silence_s: float = 120.0

    # Matches the tray's existing warning level (tray_icons.BATTERY_WARN_PCT).
    battery_pct: int = 30


@dataclass(frozen=True)
class Signals:
    """Everything known at one moment. Times are `time.monotonic()` seconds."""

    now: float
    match_live: bool
    last_hr: Optional[float] = None          # None = none since the match began
    last_rr: Optional[float] = None
    last_game_data: Optional[float] = None
    match_started: Optional[float] = None    # so a fresh match is given time
    strap_connected: bool = True             # False while the BLE link is down
    league_running: Optional[bool] = None    # None = could not be determined
    storage_error: Optional[str] = None
    battery_pct: Optional[int] = None


def _silent_for(since: Optional[float], fallback: Optional[float], now: float) -> Optional[float]:
    """Seconds since the last event, counting from `fallback` if none arrived yet."""
    reference = since if since is not None else fallback
    if reference is None:
        return None
    return now - reference


def active_issues(s: Signals, t: Thresholds = Thresholds()) -> set[Issue]:
    """Everything currently wrong. Empty means nothing to tell the participant."""
    issues: set[Issue] = set()

    if s.storage_error:
        issues.add(Issue.STORAGE_FAILED)

    # Only meaningful between matches: while a match is being recorded we are
    # obviously receiving game data.
    if not s.match_live and s.league_running:
        blind = _silent_for(s.last_game_data, None, s.now)
        if blind is None or blind >= t.game_silence_s:
            # `None` means no game data has ever arrived in this run, which is
            # exactly the case worth shouting about once League is up.
            issues.add(Issue.GAME_NOT_VISIBLE)

    # A dropped BLE link is not a health issue - the link supervisor already
    # reports it, gaps it, and says something more specific than "no heart rate"
    # ("check the strap is still on" rather than "put it on").
    if s.match_live and s.strap_connected:
        hr_silence = _silent_for(s.last_hr, s.match_started, s.now)
        if hr_silence is not None and hr_silence >= t.hr_silence_s:
            issues.add(Issue.NO_HEART_RATE)
        elif s.last_hr is not None:
            # Only ask about skin contact while a heart rate is actually
            # arriving - otherwise NO_HEART_RATE already covers it, and the H10
            # would be reporting a frozen value with no RR behind it.
            rr_silence = _silent_for(s.last_rr, s.match_started, s.now)
            if rr_silence is not None and rr_silence >= t.rr_silence_s:
                issues.add(Issue.NO_SKIN_CONTACT)

    if s.battery_pct is not None and s.battery_pct <= t.battery_pct:
        issues.add(Issue.BATTERY_LOW)

    return issues


def worst(issues: set[Issue]) -> Optional[Issue]:
    """The one issue the tray line should name."""
    for issue in SEVERITY:
        if issue in issues:
            return issue
    return None


# -- wording -------------------------------------------------------------
#
# Notification text. Short title, then one sentence naming the thing to do -
# the same rule as rte/status.py, because a participant reads this mid-game.

_RAISED: dict[Issue, tuple[str, str]] = {
    Issue.STORAGE_FAILED: (
        "RiftRec cannot save",
        "The folder RiftRec writes to is not reachable. Data is being held in "
        "memory and written as soon as the folder is back - if it is an "
        "external drive or a cloud folder, plug it in or sign in.",
    ),
    Issue.GAME_NOT_VISIBLE: (
        "League is running, RiftRec sees no game",
        "RiftRec cannot read the live game data, so nothing is being recorded. "
        "Please tell us if this keeps happening.",
    ),
    Issue.NO_HEART_RATE: (
        "No heart rate",
        "A match is being recorded but no heart rate is arriving. Is the chest "
        "strap on, and are the electrodes moistened?",
    ),
    Issue.NO_SKIN_CONTACT: (
        "Chest strap lost skin contact",
        "The strap is connected but is no longer reading your heartbeat. Push it "
        "down against the skin and moisten the electrodes.",
    ),
    Issue.BATTERY_LOW: (
        "Chest strap battery low",
        "Replace the coin cell (CR2025) soon - a strap that dies mid-match costs "
        "the whole session.",
    ),
}

_CLEARED: dict[Issue, tuple[str, str]] = {
    Issue.STORAGE_FAILED: (
        "RiftRec can save again",
        "The folder is reachable and everything held in memory has been written.",
    ),
    Issue.GAME_NOT_VISIBLE: (
        "Game data is coming through",
        "RiftRec can read the live game data again.",
    ),
    Issue.NO_HEART_RATE: ("Heart rate is back", "Recording continues normally."),
    Issue.NO_SKIN_CONTACT: (
        "Skin contact is back",
        "The strap is reading your heartbeat again.",
    ),
    Issue.BATTERY_LOW: ("Battery level is fine", "A fresh cell was detected."),
}


def notification_for(issue: Issue, raised: bool = True) -> tuple[str, str]:
    """(title, message) for a tray notification when an issue starts or ends."""
    table = _RAISED if raised else _CLEARED
    return table.get(issue, ("RiftRec", ""))
