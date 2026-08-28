"""Show a participant where their recordings are (EW-89).

The single most valuable thing a participant does after playing is send the
file back. Everything that stands between "I finished a session" and "the file
is attached to an email" costs recordings - and asking somebody mid-week to
navigate to a path they chose once, weeks ago, is exactly such an obstacle.

So the tray opens the folder for them, with the current recording selected.

Never raises: failing to open a window must not disturb a running recording,
and the status window shows the path in text as a fallback.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

# Windows: keep a console window from flashing up behind Explorer.
_NO_WINDOW = 0x08000000


def _select_in_explorer(target: Path) -> None:
    """Open the containing folder with `target` highlighted.

    Explorer wants `/select,<path>` as ONE argument - split into two it opens
    the user's Documents folder instead, which is worse than doing nothing. It
    also returns exit code 1 on success, so the code is not checked.
    """
    subprocess.Popen(
        ["explorer", f"/select,{target}"],
        creationflags=_NO_WINDOW,
    )


def open_location(
    target: Optional[Path],
    *,
    select: Optional[Callable[[Path], None]] = None,
    open_folder: Optional[Callable[[Path], None]] = None,
) -> bool:
    """Show `target` in the file manager. True if something was opened.

    `target` is the recording file. If it exists, its folder opens with the file
    selected; if it does not exist yet - no match recorded, so no file - the
    folder opens on its own. Both callables are injectable so the decision can
    be tested without opening a window.
    """
    if target is None:
        return False
    target = Path(target)
    select = select or _select_in_explorer
    open_folder = open_folder or (os.startfile if sys.platform == "win32" else None)

    try:
        if target.is_file():
            select(target)
            return True
        folder = target if target.is_dir() else target.parent
        if not folder.is_dir() or open_folder is None:
            return False
        open_folder(folder)
        return True
    except OSError as exc:
        print(f"[warn] could not open {target}: {exc}")
        return False
