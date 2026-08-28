"""Live status window (EW-89): the reason, in a window a participant can read.

The tray tooltip is capped at 127 characters by Windows and disappears as soon
as the pointer moves. This window is the place a participant is told to open
when they are unsure whether anything is being recorded - it names the state,
the reason, what to do about it, and where the file is going.

Runs tkinter on its own short-lived thread, like the note prompt: pystray owns
the main thread, so a window created there would deadlock the tray. Only one
window at a time; asking again just raises the existing one.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

from ..rte.state import RecorderState
from ..rte.status import StatusReport
from .tray_icons import color_for

# A snapshot callable returns everything the window shows, so this module needs
# no reference to the supervisor and can be exercised with a stub.
Snapshot = Callable[[], dict]

_REFRESH_MS = 1000
_open_lock = threading.Lock()
_open_window: Optional["_StatusWindow"] = None


def show(snapshot: Snapshot) -> None:
    """Open the status window (or raise the one already open)."""
    global _open_window
    with _open_lock:
        existing = _open_window
        if existing is not None:
            existing.raise_window()
            return
        window = _StatusWindow(snapshot)
        _open_window = window
    threading.Thread(target=window.run, daemon=True).start()


def _clear(window: "_StatusWindow") -> None:
    global _open_window
    with _open_lock:
        if _open_window is window:
            _open_window = None


class _StatusWindow:
    def __init__(self, snapshot: Snapshot) -> None:
        self._snapshot = snapshot
        self._root = None

    def raise_window(self) -> None:
        root = self._root
        if root is None:
            return
        try:
            root.after(0, lambda: (root.deiconify(), root.lift()))
        except Exception:
            pass  # window is tearing down - the next call opens a fresh one

    def run(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        root = tk.Tk()
        self._root = root
        root.title("RiftRec — status")
        root.resizable(False, False)

        frm = ttk.Frame(root, padding=14)
        frm.grid(sticky="nsew")

        self._dot = tk.Canvas(frm, width=18, height=18, highlightthickness=0)
        self._dot.grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._dot_id = self._dot.create_oval(2, 2, 16, 16, fill="#9e9e9e", outline="")

        self._headline = tk.StringVar()
        ttk.Label(frm, textvariable=self._headline, font=("Segoe UI", 12, "bold")).grid(
            row=0, column=1, sticky="w")

        # Fixed wraplength so a long reason grows downward, not sideways: the
        # window must not resize itself every time the sentence changes.
        self._detail = tk.StringVar()
        ttk.Label(frm, textvariable=self._detail, wraplength=380, justify="left").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))

        ttk.Separator(frm).grid(row=2, column=0, columnspan=2, sticky="we", pady=10)

        self._facts = tk.StringVar()
        ttk.Label(frm, textvariable=self._facts, justify="left",
                  foreground="#666").grid(row=3, column=0, columnspan=2, sticky="w")

        ttk.Label(
            frm,
            text="You can close this window — recording carries on in the background.",
            foreground="#666",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))

        btns = ttk.Frame(frm)
        btns.grid(row=5, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Close", command=self._close).grid(row=0, column=0)

        root.protocol("WM_DELETE_WINDOW", self._close)
        self._refresh()
        root.mainloop()
        _clear(self)

    def _refresh(self) -> None:
        try:
            data = self._snapshot() or {}
        except Exception as exc:      # never let the window kill the recorder
            data = {"error": str(exc)}
        report: StatusReport = data.get("report") or StatusReport()
        self._headline.set(report.headline)
        self._detail.set(report.detail)
        state = getattr(report, "state", RecorderState.IDLE)
        try:
            self._dot.itemconfigure(self._dot_id, fill=color_for(state))
        except Exception:
            pass
        self._facts.set(_facts_text(data))
        root = self._root
        if root is not None:
            root.after(_REFRESH_MS, self._refresh)

    def _close(self) -> None:
        root = self._root
        self._root = None
        _clear(self)
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass


def _facts_text(data: dict) -> str:
    """The plain facts under the reason: who, where, how much so far.

    Kept as a pure function so the wording is testable without a display.
    """
    report: StatusReport = data.get("report") or StatusReport()
    lines = [
        f"Participant: {data.get('participant') or '—'}",
        # battery_text() already reads "Battery: 100%" - prefixing it again
        # produced "Strap battery: Battery: 100%" in the window.
        f"Strap {(data.get('battery') or 'battery: unknown').lower()}",
        f"Matches recorded this run: {data.get('matches', 0)}",
        f"Saving to: {data.get('db_path') or '—'}",
    ]
    if report.cause:
        # The technical message behind the sentence above. A participant does
        # not need it, but it turns a support call into one screenshot.
        lines.append(f"Details: {report.cause}")
    return "\n".join(lines)
