"""The check-in window: questionnaire, then the 60 s resting countdown.

Runs tkinter on its own thread like the status window - pystray owns the main
thread. Two pages in one window:

1. The questions. A line on top says the resting measurement is already
   running; it is - the strap has been recording since it connected, and this
   minute of reading and clicking is the settling-in phase.
2. After "Submit": a 60 s countdown with "sit back, hands off". That minute is
   the resting window the analysis uses.

The window is raised once when it opens and then left alone: it must never sit
on top of a game that starts while it is open. If a match starts during the
countdown, the resting minute is recorded as aborted and the window closes.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from .checkin_questions import QUESTIONS, REST_SECONDS, is_visible, missing

if TYPE_CHECKING:
    from .checkin import CheckinRun

_HELP_FG = "#666"
_TICK_MS = 250

# Two columns on a PC monitor: no scrolling, and the eye moves left to right
# once instead of down a long list. Left what you slept and consumed, right
# exercise, how you are, medication. A scrollbar appears only on a screen too
# small for both columns (e.g. a laptop at 150 % scaling).
_LEFT_COLUMN = ("sleep_quality", "sleep_hours", "alcohol", "caffeine",
                "nicotine", "last_meal")
_COLUMN_WIDTH = 470


def run_window(run: "CheckinRun") -> None:
    _CheckinWindow(run).run()


class _CheckinWindow:
    def __init__(self, run: "CheckinRun") -> None:
        self._run = run
        self._root = None
        self._vars: dict = {}
        self._rows: dict = {}
        self._phase = "questions"
        self._left = REST_SECONDS * 1000
        self._rest_t0 = 0.0

    # -- page 1: questions ------------------------------------------------

    def run(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        root = tk.Tk()
        self._root = root
        self._tk = tk
        self._ttk = ttk
        root.title("RiftRec — daily check-in")
        root.resizable(False, False)

        outer = ttk.Frame(root, padding=(14, 12, 14, 12))
        outer.pack(fill="both", expand=True)
        self._outer = outer

        ttk.Label(outer, text="Daily check-in — about 2 minutes",
                  font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(
            outer,
            text="● Your resting measurement is already running. Please stay "
                 "seated while you answer.",
            foreground="#2e7d32",
        ).pack(anchor="w", pady=(4, 8))

        # The questions sit in a canvas so a too-small screen can still scroll;
        # on a normal monitor the canvas is sized to fit and the bar stays away.
        holder = ttk.Frame(outer)
        holder.pack(fill="both", expand=True)
        canvas = tk.Canvas(holder, highlightthickness=0)
        bar = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview)
        body = ttk.Frame(canvas)
        body.bind("<Configure>", lambda e: self._fit())
        canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)
        self._canvas, self._bar_widget, self._body = canvas, bar, body
        self._scrolling = False

        columns = (ttk.Frame(body), ttk.Frame(body))
        columns[0].grid(row=0, column=0, sticky="nw", padx=(0, 24))
        ttk.Separator(body, orient="vertical").grid(row=0, column=1, sticky="ns")
        columns[1].grid(row=0, column=2, sticky="nw", padx=(24, 0))
        counts = [0, 0]
        for q in QUESTIONS:
            col = 0 if q.id in _LEFT_COLUMN else 1
            self._add_question(columns[col], counts[col], q)
            counts[col] += 1

        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(10, 0))
        self._left_var = tk.StringVar()
        ttk.Label(footer, textvariable=self._left_var,
                  foreground=_HELP_FG).pack(side="left")
        self._submit = ttk.Button(footer, text="Submit", command=self._on_submit)
        self._submit.pack(side="right")

        self._refresh()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        # Raise once so it is seen, then let go: it must never stay on top of
        # a game that starts while it is open.
        root.lift()
        root.attributes("-topmost", True)
        root.after(800, lambda: root.attributes("-topmost", False))
        root.after(_TICK_MS, self._watch_match)
        root.mainloop()

    def _fit(self) -> None:
        """Size the canvas to the questions; scroll only if the screen is short."""
        root, canvas, body = self._root, self._canvas, self._body
        if root is None or self._phase != "questions":
            return
        need_w, need_h = body.winfo_reqwidth(), body.winfo_reqheight()
        room = root.winfo_screenheight() - 230   # title bar, header, footer, taskbar
        canvas.configure(width=need_w, height=min(need_h, room),
                         scrollregion=(0, 0, need_w, need_h))
        scroll = need_h > room
        if scroll and not self._scrolling:
            self._bar_widget.pack(side="right", fill="y")
            root.bind_all("<MouseWheel>",
                          lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))
        elif not scroll and self._scrolling:
            self._bar_widget.pack_forget()
            root.unbind_all("<MouseWheel>")
            canvas.yview_moveto(0)
        self._scrolling = scroll

    def _add_question(self, parent, n: int, q) -> None:
        tk, ttk = self._tk, self._ttk
        row = ttk.Frame(parent, padding=(0, 6, 0, 6))
        row.grid(row=n, column=0, sticky="we")
        self._rows[q.id] = row

        ttk.Label(row, text=q.text, font=("Segoe UI", 10, "bold")).grid(
            row=0, column=0, sticky="w")
        var = tk.StringVar(value="")
        self._vars[q.id] = (var, q)
        opts = ttk.Frame(row)
        opts.grid(row=1, column=0, sticky="w", pady=(2, 0))
        # One line whenever the labels fit (a 1-5 scale split over two lines
        # reads like two questions); only long answer texts wrap.
        width = sum(len(label) + 6 for _, label in q.options)
        per_line = len(q.options) if width <= 72 else 4
        for i, (code, label) in enumerate(q.options):
            ttk.Radiobutton(opts, text=label, value=str(code), variable=var,
                            command=self._refresh).grid(
                row=i // per_line, column=i % per_line, sticky="w", padx=(0, 14))
        if q.help:
            ttk.Label(row, text=q.help, foreground=_HELP_FG,
                      wraplength=_COLUMN_WIDTH,
                      justify="left").grid(row=2, column=0, sticky="w", pady=(2, 0))

    def _answers(self) -> dict:
        """Current answers with their stored type (int scales stay ints)."""
        out: dict = {}
        for qid, (var, q) in self._vars.items():
            raw = var.get()
            if raw == "":
                continue
            for code, _ in q.options:
                if str(code) == raw:
                    out[qid] = code
        return out

    def _refresh(self) -> None:
        answers = self._answers()
        for qid, (var, q) in self._vars.items():
            row = self._rows[qid]
            if is_visible(q, answers):
                row.grid()
            else:
                row.grid_remove()
                var.set("")
        left = missing(self._answers())
        if left:
            self._left_var.set(f"{len(left)} question{'s' if len(left) > 1 else ''} left")
        else:
            self._left_var.set("All answered.")

    def _on_submit(self) -> None:
        # Never blocked: the participant information promises that any single
        # question may be skipped. Blanks are asked about once, then accepted.
        answers = self._answers()
        left = missing(answers)
        if left:
            from tkinter import messagebox

            if not messagebox.askyesno(
                "Some questions are blank",
                f"You left {len(left)} question{'s' if len(left) > 1 else ''} "
                "blank.\n\nThat's fine if you'd rather not answer them - but if "
                "you just missed them, please fill them in.\n\nSubmit anyway?",
                parent=self._root,
            ):
                return
        self._run.submit(answers)
        self._show_rest()

    # -- page 2: the resting minute --------------------------------------

    def _show_rest(self) -> None:
        tk, ttk = self._tk, self._ttk
        root = self._root
        root.unbind_all("<MouseWheel>")
        for child in self._outer.winfo_children():
            child.destroy()
        self._phase = "rest"

        ttk.Label(self._outer, text="Almost done — one quiet minute",
                  font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(
            self._outer,
            text="Sit back, hands off mouse and keyboard, breathe normally and "
                 "don't talk. The window closes by itself.",
            wraplength=560, justify="left",
        ).pack(anchor="w", pady=(6, 14))
        self._count_var = tk.StringVar(value=str(REST_SECONDS))
        ttk.Label(self._outer, textvariable=self._count_var,
                  font=("Segoe UI", 40, "bold")).pack(pady=(0, 6))
        self._bar = ttk.Progressbar(self._outer, maximum=REST_SECONDS * 1000,
                                    length=520)
        self._bar.pack(pady=(0, 14))
        ttk.Button(self._outer, text="Skip", command=self._on_skip).pack(anchor="e")

        root.geometry("")          # shrink to the countdown page
        self._run.rest_started()
        self._rest_t0 = time.monotonic()
        self._left = REST_SECONDS * 1000
        root.after(_TICK_MS, self._tick)

    def _tick(self) -> None:
        if self._phase != "rest":
            return
        # Measured on the clock, not by counting ticks: after() is late by a
        # few ms each time, which added up to 62.4 s in the first real test.
        elapsed_ms = int((time.monotonic() - self._rest_t0) * 1000)
        self._left = REST_SECONDS * 1000 - elapsed_ms
        self._bar["value"] = min(elapsed_ms, REST_SECONDS * 1000)
        self._count_var.set(str(max(0, -(-self._left // 1000))))
        if self._left <= 0:
            self._finish("completed", "Thanks — have a good session!")
            return
        self._root.after(min(_TICK_MS, max(self._left, 1)), self._tick)

    def _on_skip(self) -> None:
        self._finish("skipped", "Skipped. Have a good session!")

    def _finish(self, status: str, message: str) -> None:
        if self._phase != "rest":
            return
        self._phase = "done"
        self._run.rest_finished(status)
        for child in self._outer.winfo_children():
            child.destroy()
        self._ttk.Label(self._outer, text=message,
                        font=("Segoe UI", 11)).pack(padx=20, pady=20)
        self._root.after(2000, self._close)

    # -- leaving ---------------------------------------------------------

    def _watch_match(self) -> None:
        """A game started during the countdown: that minute was not rest."""
        if self._phase == "rest" and self._run.match_live:
            self._finish("aborted", "Your game started — the quiet minute is "
                                    "skipped. Have a good session!")
            return
        if self._phase in ("questions", "rest") and self._root is not None:
            self._root.after(_TICK_MS, self._watch_match)

    def _on_close(self) -> None:
        if self._phase == "rest":
            self._finish("aborted", "Closed.")
            return
        self._close()

    def _close(self) -> None:
        root = self._root
        self._root = None
        if root is not None:
            try:
                root.unbind_all("<MouseWheel>")
                root.destroy()
            except Exception:
                pass
