"""Pre-game settings window (EW-38): configure a hands-off recording run.

Shown once before recording. Collects participant id, starting session index,
the H10 BLE device, and the storage file. Returns a RecorderConfig (or None if
cancelled). The recorder then runs unattended and records each detected match
automatically.
"""

from __future__ import annotations

import threading
from datetime import date
from pathlib import Path
from typing import Optional

from ..config import RecorderConfig
from .device_scan import scan_polar_devices


def prompt_settings() -> Optional[RecorderConfig]:
    return _SettingsDialog().prompt()


_AUTO_LABEL = "(auto — pick first Polar found)"


class _SettingsDialog:
    def __init__(self) -> None:
        self.result: Optional[RecorderConfig] = None
        self._devices: list[tuple[str, str]] = []

    def prompt(self) -> Optional[RecorderConfig]:
        import tkinter as tk
        from tkinter import filedialog, ttk

        root = tk.Tk()
        self._root = root
        self._tk = tk
        self._filedialog = filedialog
        root.title("RiftRec — session settings")
        root.resizable(False, False)

        frm = ttk.Frame(root, padding=14)
        frm.grid(sticky="nsew")

        self._participant = tk.StringVar()
        self._session = tk.StringVar(value="0")
        self._db = tk.StringVar(value=str(Path.cwd() / "riftrec_session.sqlite"))
        self._device_var = tk.StringVar()

        r = 0
        ttk.Label(frm, text="Participant ID").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Entry(frm, textvariable=self._participant, width=32).grid(row=r, column=1, columnspan=2, sticky="we")

        r += 1
        ttk.Label(frm, text="Start session #").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Entry(frm, textvariable=self._session, width=8).grid(row=r, column=1, sticky="w")
        ttk.Label(frm, text="(each match increments it)").grid(row=r, column=2, sticky="w")

        r += 1
        ttk.Label(frm, text="H10 device").grid(row=r, column=0, sticky="w", pady=3)
        self._device_cb = ttk.Combobox(frm, textvariable=self._device_var, width=30, state="readonly")
        self._device_cb["values"] = [_AUTO_LABEL]
        self._device_cb.current(0)
        self._device_cb.grid(row=r, column=1, sticky="we")
        self._scan_btn = ttk.Button(frm, text="Scan", command=self._scan)
        self._scan_btn.grid(row=r, column=2, sticky="we", padx=(6, 0))

        r += 1
        ttk.Label(frm, text="Storage file").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Entry(frm, textvariable=self._db, width=30).grid(row=r, column=1, sticky="we")
        ttk.Button(frm, text="Browse", command=self._browse).grid(row=r, column=2, sticky="we", padx=(6, 0))

        r += 1
        ttk.Label(frm, text="Scan and pick a device, or keep \"auto\" to use the first Polar found.",
                  foreground="#666").grid(row=r, column=0, columnspan=3, sticky="w", pady=(8, 0))

        r += 1
        ttk.Label(
            frm,
            text="Runs unattended from here: matches are detected and recorded\n"
                 "automatically (tray icon turns red while a match is live).",
            foreground="#666",
        ).grid(row=r, column=0, columnspan=3, sticky="w", pady=(2, 0))

        r += 1
        btns = ttk.Frame(frm)
        btns.grid(row=r, column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(btns, text="Cancel", command=self._cancel).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(btns, text="Save & run", command=self._start).grid(row=0, column=1)

        root.protocol("WM_DELETE_WINDOW", self._cancel)
        root.mainloop()
        return self.result

    def _scan(self) -> None:
        # Run off the Tk main thread: on Windows, a thread that has created a
        # window is a "GUI thread", and bleak's WinRT backend refuses to
        # deliver scan callbacks there ("Thread is configured for Windows GUI
        # but callbacks are not working"). A plain worker thread has no
        # window, so it isn't flagged and the scan works.
        self._scan_btn.config(text="Scanning…", state="disabled")

        def worker() -> None:
            devices: list[tuple[str, str]] = []
            error: Optional[str] = None
            try:
                devices = scan_polar_devices(6.0)
            except Exception as exc:
                error = str(exc) or type(exc).__name__
                print(f"[scan] failed: {exc!r}")
            self._root.after(0, self._on_scan_done, devices, error)

        threading.Thread(target=worker, daemon=True).start()

    def _on_scan_done(self, devices: list[tuple[str, str]], error: Optional[str]) -> None:
        self._devices = devices
        if error:
            note = [f"(scan error: {error})"]
        elif not devices:
            note = ["(none found — is the H10 worn?)"]
        else:
            note = []
        # _AUTO_LABEL always stays selectable so the user can switch back to
        # auto-pick after having chosen a specific device from a scan.
        self._device_cb["values"] = [_AUTO_LABEL] + [f"{n} ({a})" for n, a in devices] + note
        if devices:
            self._device_cb.current(1)
        self._scan_btn.config(text="Scan", state="normal")

    def _browse(self) -> None:
        path = self._filedialog.asksaveasfilename(
            defaultextension=".sqlite",
            filetypes=[("SQLite", "*.sqlite"), ("All files", "*.*")],
            initialfile=Path(self._db.get()).name,
        )
        if path:
            self._db.set(path)

    def _start(self) -> None:
        participant = self._participant.get().strip() or None
        try:
            session = int(self._session.get()) if self._session.get().strip() else 0
        except ValueError:
            session = 0

        device = None
        sel = self._device_var.get()
        if sel != _AUTO_LABEL:
            for name, addr in self._devices:
                if sel == f"{name} ({addr})":
                    device = addr
                    break

        db = self._db.get().strip() or "riftrec_session.sqlite"
        # If the default name is untouched, auto-name by participant + date.
        if Path(db).name == "riftrec_session.sqlite" and participant:
            db = str(Path(db).with_name(f"{participant}_{date.today().isoformat()}.sqlite"))

        self.result = RecorderConfig(
            participant_id=participant,
            session_index=session,
            db_path=Path(db),
            device=device,
        )
        self._root.destroy()

    def _cancel(self) -> None:
        self.result = None
        self._root.destroy()
