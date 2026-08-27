"""System tray icon reflecting the recorder state (EW-38, EW-89).

Observes RecorderState for the icon colour and StatusReport for the words.
Menu:
- two disabled lines: what is happening, and why (EW-89)
- the strap's battery level
- "Show status…" - the same reason in a window, also the double-click action
- "Add note…" - a small text prompt for a per-session note
- "Stop and exit" - stops the recorder and quits

There is deliberately no autostart-with-Windows entry: a recording has to
be a conscious act. The participant has to put the strap on anyway, so
starting RiftRec belongs to the same decision - a recorder that came up
silently at logon would be recording without anyone having chosen to.

Prompt and status window run tkinter on their own short-lived threads so they do
not interfere with pystray owning the main thread.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

import pystray

from ..rte.health import Issue, notification_for
from ..rte.state import Observable, RecorderState
from ..rte.status import StatusReport, tooltip_text
from . import status_window
from .tray_icons import battery_text, make_icon


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
    def __init__(self, status: Observable, report: Optional[Observable] = None) -> None:
        self._status = status
        self._current = status.state
        self._report: StatusReport = StatusReport(state=self._current)
        self._on_quit: Optional[Callable[[], None]] = None
        self._on_note: Optional[Callable[[str], None]] = None
        self._snapshot: Optional[Callable[[], dict]] = None
        self._battery: Optional[int] = None

        items = [
            pystray.MenuItem(lambda item: self._report.headline, self._noop, enabled=False),
            pystray.MenuItem(lambda item: self._report.detail, self._noop, enabled=False),
            pystray.MenuItem(lambda item: battery_text(self._battery), self._noop, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Show status…", self._show_status, default=True),
            pystray.MenuItem("Add note…", self._add_note),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Stop and exit", self._quit),
        ]

        self._icon = pystray.Icon(
            "riftrec", make_icon(self._current), self._tooltip(), menu=pystray.Menu(*items)
        )
        status.subscribe(self._on_state)
        if report is not None:
            report.subscribe(self._on_report)

    # -- wiring from the runner -------------------------------------------
    def set_on_quit(self, cb: Callable[[], None]) -> None:
        self._on_quit = cb

    def set_on_note(self, cb: Callable[[str], None]) -> None:
        self._on_note = cb

    def set_status_source(self, fn: Callable[[], dict]) -> None:
        """Supply the snapshot the status window renders."""
        self._snapshot = fn

    # -- pystray callbacks ------------------------------------------------
    def _noop(self, icon=None, item=None) -> None:
        pass

    def _tooltip(self) -> str:
        """Hover text: state, the reason behind it, and the battery."""
        return tooltip_text(self._report, battery_text(self._battery))

    def _redraw(self, icon: bool = False) -> None:
        """Push the current state to the tray. Called from worker threads."""
        try:
            if icon:
                self._icon.icon = make_icon(self._current)
            self._icon.title = self._tooltip()
            self._icon.update_menu()
        except Exception:
            pass  # tray not up yet, or already torn down

    def set_battery(self, pct: Optional[int]) -> None:
        """Show the strap's battery level (called from the supervisor thread)."""
        self._battery = pct
        self._redraw()

    def _on_state(self, state: RecorderState) -> None:
        self._current = state
        self._redraw(icon=True)

    def alert(self, issue: Issue, raised: bool = True) -> None:
        """Pop a Windows notification when a health issue starts or ends.

        The status line is passive - it only helps someone who looks. These
        situations lose data while nobody is looking, so they push (EW-89).
        Called from the supervisor thread; failure to notify must never
        disturb a recording.
        """
        title, message = notification_for(issue, raised)
        try:
            self._icon.notify(message, title)
        except Exception as exc:
            print(f"[warn] could not show a notification: {exc}")

    def _on_report(self, report: StatusReport) -> None:
        """The reason changed: new tooltip and new menu wording (EW-89)."""
        self._report = report
        self._redraw()

    def _show_status(self, icon=None, item=None) -> None:
        snapshot = self._snapshot
        if snapshot is None:
            # No supervisor attached (shouldn't happen) - still show the words
            # we have rather than nothing at all.
            def snapshot() -> dict:  # type: ignore[misc]
                return {"report": self._report, "battery": battery_text(self._battery)}

        status_window.show(snapshot)

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
