"""EW-89: telling "nobody is playing" apart from "we cannot see the game".

The Riot Live Client Data API being silent means either of those, and only one
of them is data loss. The game process is what distinguishes them.
"""

from __future__ import annotations

from riftrec.sources.game_process import GAME_PROCESS, is_game_running

_HEADER = '"Image Name","PID","Session Name","Session#","Mem Usage"\n'


def _listing(*names: str) -> str:
    return _HEADER + "".join(f'"{n}","1234","Console","1","10.000 K"\n' for n in names)


def test_finds_the_running_game() -> None:
    listing = _listing("explorer.exe", GAME_PROCESS, "chrome.exe")
    assert is_game_running(lambda: listing) is True


def test_reports_absence() -> None:
    assert is_game_running(lambda: _listing("explorer.exe")) is False


def test_the_launcher_is_not_the_game() -> None:
    """`LeagueClient.exe` runs whenever League is open, match or not. Treating it
    as "a game is on" would fire the alarm every evening."""
    assert is_game_running(lambda: _listing("LeagueClient.exe")) is False
    assert is_game_running(lambda: _listing("LeagueClientUx.exe")) is False


def test_case_does_not_matter() -> None:
    assert is_game_running(lambda: _listing("LEAGUE OF LEGENDS.EXE")) is True


def test_unreadable_process_list_is_unknown_not_absent() -> None:
    """None means "cannot tell", and the health check stays quiet on it -
    guessing "no game" would suppress a real alarm."""
    assert is_game_running(lambda: None) is None


def test_the_real_probe_never_raises() -> None:
    """It runs on strangers' PCs inside the watch loop; whatever the OS does, it
    must return one of True/False/None and nothing else."""
    assert is_game_running() in (True, False, None)
