"""Single-instance guard (EW-43): stop a second recorder from running.

Holds a non-blocking OS-level exclusive lock on a lock file for the whole
process lifetime. The OS releases the lock automatically when the process exits
or crashes, so there is no stale-lock file to clean up. Works regardless of how
the recorder was started (double-click launcher or a raw `python -m riftrec`).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_lock_handle = None  # kept alive for the process lifetime to hold the lock


def _lock_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "RiftRec" / "riftrec.lock"


def _lock_file(path: Path):
    """Open `path` and take a non-blocking exclusive OS lock.

    Returns (handle, "ok") on success - keep the handle alive to hold the lock;
    (None, "locked") if another handle/process already holds it; (None, "error")
    if the file could not be created/opened at all.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(path, "a+")
    except OSError:
        return None, "error"
    try:
        if sys.platform == "win32":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None, "locked"
    return fh, "ok"


def acquire_single_instance() -> bool:
    """Become the only running instance. True on success; False if one already
    runs. A filesystem error acquiring the lock returns True - never block
    recording over a lock quirk."""
    global _lock_handle
    fh, status = _lock_file(_lock_path())
    if status == "ok":
        _lock_handle = fh
        return True
    if status == "locked":
        return False
    return True  # "error" -> don't block recording


def warn_already_running() -> None:
    """Tell the user another instance is up (log + a message box on Windows)."""
    msg = "RiftRec is already running. Check the system tray (bottom-right)."
    print(f"[error] {msg}")  # goes to riftrec.log under pythonw
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, msg, "RiftRec", 0x40)  # MB_ICONINFORMATION
        except Exception:
            pass
    else:
        try:
            import tkinter
            from tkinter import messagebox

            root = tkinter.Tk()
            root.withdraw()
            messagebox.showinfo("RiftRec", msg)
            root.destroy()
        except Exception:
            pass
