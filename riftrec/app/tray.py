"""System tray icon reflecting the recorder state (EW-38).

Observes RecorderState and updates the icon colour/tooltip. Menu:
- a disabled status line (current state)
- "Add note…" - opens a small text prompt for a per-session note
- "Stop and exit" - stops the recorder and quits

The note prompt runs tkinter in its own short-lived thread so it does not
interfere with pystray owning the main thread.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

import pystray

from ..rte.state import Observable, RecorderState
from .tray_icons import make_icon, title_for


def _prompt_note() -> Optional[str]:
    """Modal single-line text prompt (own tkinter root, own thread)."""
    import tkinter as tk
    from tkinter import simpledialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return simpledialog.askstring(
            "RiftRec note", "Note for the current session:", parent=root
        )
    finally:
        root.destroy()


class TrayController:
    def __init__(self, status: Observable) -> None:
        self._status = status
        self._current = status.state
        self._on_quit: Optional[Callable[[], None]] = None
        self._on_note: Optional[Callable[[str], None]] = None
        self._icon = pystray.Icon(
            "riftrec",
            make_icon(self._current),
            title_for(self._current),
            menu=pystray.Menu(
                pystray.MenuItem(lambda item: title_for(self._current), self._noop, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Add note…", self._add_note),
                pystray.MenuItem("Stop and exit", self._quit),
            ),
        )
        status.subscribe(self._on_state)

    # -- wiring from the runner -------------------------------------------
    def set_on_quit(self, cb: Callable[[], None]) -> None:
        self._on_quit = cb

    def set_on_note(self, cb: Callable[[str], None]) -> None:
        self._on_note = cb

    # -- pystray callbacks ------------------------------------------------
    def _noop(self, icon=None, item=None) -> None:
        pass

    def _on_state(self, state: RecorderState) -> None:
        self._current = state
        try:
            self._icon.icon = make_icon(state)
            self._icon.title = title_for(state)
            self._icon.update_menu()
        except Exception:
            pass

    def _add_note(self, icon, item) -> None:
        def worker() -> None:
            text = _prompt_note()
            if text and self._on_note is not None:
                self._on_note(text)

        threading.Thread(target=worker, daemon=True).start()

    def _quit(self, icon, item) -> None:
        if self._on_quit is not None:
            self._on_quit()
        self._icon.stop()

    # -- lifecycle --------------------------------------------------------
    def run(self) -> None:
        self._icon.run()

    def stop(self) -> None:
        try:
            self._icon.stop()
        except Exception:
            pass
