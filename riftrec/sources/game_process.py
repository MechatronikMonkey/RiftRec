"""Is the game actually running? (EW-89)

The Riot Live Client Data API not answering means one of two very different
things: no match is being played, or a match is being played and we cannot see
it - a firewall, an overlay, a Riot change. From the API alone the two are
indistinguishable, and the second one is silent data loss: the tray sits on
"ready, waiting for a match" while matches are played.

The game process tells them apart. `League of Legends.exe` is the match process
(not `LeagueClient.exe`, which is the launcher and runs the whole time).

Scope note for a study on other people's PCs: this asks whether one specific
process exists. It never stores a process list, and nothing about other running
programs reaches the recording.

Never raises and never blocks a recording: anything unexpected returns None,
which the health check reads as "cannot tell" and stays quiet about.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Callable, Optional

GAME_PROCESS = "League of Legends.exe"

# Windows: keep tasklist from flashing a console window. A windowed build has no
# console, and a black box popping up mid-game would be its own bug report.
_NO_WINDOW = 0x08000000


def _tasklist() -> Optional[str]:
    """Raw `tasklist` output, or None if it cannot be run."""
    if sys.platform != "win32":
        return None
    try:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            timeout=10,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    # The console codepage is not UTF-8; process names are ASCII, so decoding
    # loosely is enough and never throws on a stray byte.
    return result.stdout.decode("utf-8", errors="replace")


def is_game_running(lister: Optional[Callable[[], Optional[str]]] = None) -> Optional[bool]:
    """True / False, or None when it could not be determined.

    `lister` is injectable so the decision can be tested without a running game.
    """
    listing = (lister or _tasklist)()
    if listing is None:
        return None
    return GAME_PROCESS.lower() in listing.lower()
