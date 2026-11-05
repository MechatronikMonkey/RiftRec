"""Persisted pilot preferences (EW-43).

Remembers the participant id and the storage folder across launches so a pilot
does not re-enter them every time. Stored as a small INI under the user's config
dir (``%APPDATA%\\RiftRec`` on Windows, ``~/.config/riftrec`` otherwise) so it
survives moving or reinstalling the app folder. Reading/writing is best-effort:
a missing or corrupt prefs file must never block recording.
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_SECTION = "recorder"


def _prefs_path() -> Path:
    appdata = os.environ.get("APPDATA")  # Windows
    if appdata:
        return Path(appdata) / "RiftRec" / "prefs.ini"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    root = Path(xdg) if xdg else Path.home() / ".config"
    return root / "riftrec" / "prefs.ini"


@dataclass
class Prefs:
    participant_id: Optional[str] = None
    storage_folder: Optional[str] = None
    # Daily check-in bookkeeping (schema 5). Days are study days as ISO dates
    # (see checkin_questions.study_day). Kept here rather than read back from
    # the .sqlite files, because every run writes a new file.
    last_checkin_day: Optional[str] = None   # day the questionnaire was submitted
    prompt_day: Optional[str] = None         # day `prompts` counts for
    prompts: int = 0                         # times shown on prompt_day


def load_prefs() -> Prefs:
    """Load saved prefs, or empty defaults if none/unreadable."""
    path = _prefs_path()
    cp = configparser.ConfigParser()
    try:
        if path.exists():
            cp.read(path, encoding="utf-8")
            if cp.has_section(_SECTION):
                s = cp[_SECTION]
                try:
                    prompts = int(s.get("prompts") or 0)
                except ValueError:
                    prompts = 0
                return Prefs(
                    participant_id=(s.get("participant_id") or "").strip() or None,
                    storage_folder=(s.get("storage_folder") or "").strip() or None,
                    last_checkin_day=(s.get("last_checkin_day") or "").strip() or None,
                    prompt_day=(s.get("prompt_day") or "").strip() or None,
                    prompts=prompts,
                )
    except (OSError, configparser.Error):
        pass  # corrupt/unreadable prefs must never block recording
    return Prefs()


def save_prefs(prefs: Prefs) -> None:
    """Persist prefs. Best-effort: failure just means they aren't remembered."""
    path = _prefs_path()
    cp = configparser.ConfigParser()
    cp[_SECTION] = {
        "participant_id": prefs.participant_id or "",
        "storage_folder": prefs.storage_folder or "",
        "last_checkin_day": prefs.last_checkin_day or "",
        "prompt_day": prefs.prompt_day or "",
        "prompts": str(prefs.prompts),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            cp.write(f)
    except OSError:
        pass
