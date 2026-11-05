"""Daily check-in: when to ask, and what gets written (schema 5).

Once per study day, before the first match, the participant answers a short
questionnaire on the things that move heart rate and HRV besides the game -
sleep above all, because the primary hypothesis compares recovery after poor
and good sleep (EW-61). Without the sleep answer that hypothesis has no
independent variable.

The resting measurement is folded into it: the window only opens once the
strap is delivering beats, so heart rate is already being recorded while the
questions are answered (that minute is the settling-in phase), and the 60 s
countdown after "Submit" is the actual resting window. Nothing is measured
here - the beats are in hr_sample / rr_interval like all others; the check-in
row only stores the answers and the start and end of the resting window.

`CheckinController` decides whether to ask; `CheckinRun` is one opened window
and owns its row. Both are free of tkinter so they can be tested without a
display; the window itself is `checkin_window`.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable, Optional

from ..clock import local_utc_offset_min
from ..config import RecorderConfig
from ..model import Checkin
from ..storage.sqlite_sink import write_checkin
from .checkin_questions import (
    MAX_PROMPTS_PER_DAY, QUESTIONNAIRE_VERSION, cleaned, study_day,
)
from .prefs import load_prefs, save_prefs


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CheckinRun:
    """One check-in window, from opening to the end of the resting minute.

    Every step writes the row again, so whatever happens next - the game
    starting, the PC going down - what was answered is already on disk.
    """

    def __init__(self, controller: "CheckinController") -> None:
        self._controller = controller
        service = controller.service
        self.record = Checkin(
            checkin_id=str(uuid.uuid4()),
            questionnaire_version=QUESTIONNAIRE_VERSION,
            opened_utc=_utc_now(),
            session_id=service.current_session_id if service else None,
            participant_id=controller.config.participant_id,
            utc_offset_min=local_utc_offset_min(),
        )

    @property
    def match_live(self) -> bool:
        """A match started: the resting minute is no longer at rest."""
        service = self._controller.service
        return bool(service is not None and service.match_live)

    def submit(self, answers: dict) -> None:
        """Store the answers; the day counts as done from here on.

        Blanks are allowed: the participant information promises that any
        single question may be skipped. A skipped answer is stored as NULL.
        """
        self.record.answers = cleaned(answers)
        self.record.submitted_utc = _utc_now()
        self._write()
        self._controller.mark_done()

    def rest_started(self) -> None:
        self.record.rest_start_utc = _utc_now()
        self._write()

    def rest_finished(self, status: str) -> None:
        """'completed', 'skipped' or 'aborted' (a match started, window closed)."""
        if status not in ("completed", "skipped", "aborted"):
            raise ValueError(status)
        self.record.rest_status = status
        self.record.rest_end_utc = _utc_now()
        self._write()

    def dismissed(self) -> None:
        """Closed before submitting: kept as a row, so compliance is visible."""
        if self.record.submitted_utc is None:
            self._write()

    def _write(self) -> None:
        try:
            write_checkin(self._controller.config.db_path, self.record)
        except Exception as exc:  # a questionnaire must never end a recording
            print(f"[warn] could not save the check-in: {exc}")


class CheckinController:
    """Decides when the check-in window opens.

    Asked by the supervisor once per idle stretch with the strap reading the
    skin (`on_ready`), and by the tray when the participant opens it by hand
    (`open_manual`). Only one window at a time.
    """

    def __init__(
        self,
        config: RecorderConfig,
        service=None,
        *,
        show: Optional[Callable[[CheckinRun], None]] = None,
        now: Optional[Callable[[], datetime]] = None,
        load=load_prefs,
        save=save_prefs,
    ) -> None:
        self.config = config
        self.service = service
        self._show = show or _show_tk_window
        self._now = now or (lambda: datetime.now().astimezone())
        self._load = load
        self._save = save
        self._lock = threading.Lock()
        self._open = False

    def _today(self) -> str:
        return study_day(self._now()).isoformat()

    def is_due(self) -> bool:
        return self._load().last_checkin_day != self._today()

    def on_ready(self) -> None:
        """The supervisor says the moment is right. Ask if still due today."""
        prefs = self._load()
        today = self._today()
        if prefs.last_checkin_day == today:
            return
        shown = prefs.prompts if prefs.prompt_day == today else 0
        if shown >= MAX_PROMPTS_PER_DAY or self._open:
            return
        # Counted before the window opens, so a submit inside it (which saves
        # the same file) cannot be overwritten by this bookkeeping.
        self._save(replace(prefs, prompt_day=today, prompts=shown + 1))
        self._start()

    def open_manual(self) -> None:
        """From the tray: always allowed, also after today's check-in."""
        self._start()

    def mark_done(self) -> None:
        self._save(replace(self._load(), last_checkin_day=self._today()))

    def _start(self) -> bool:
        with self._lock:
            if self._open:
                return False
            self._open = True
        run = CheckinRun(self)
        threading.Thread(target=self._run, args=(run,), daemon=True).start()
        return True

    def _run(self, run: CheckinRun) -> None:
        try:
            self._show(run)
        except Exception as exc:
            print(f"[warn] check-in window failed: {exc}")
        finally:
            run.dismissed()
            with self._lock:
                self._open = False


def _show_tk_window(run: CheckinRun) -> None:
    from .checkin_window import run_window

    run_window(run)
