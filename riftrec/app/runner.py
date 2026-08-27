"""Runner (EW-38, EW-89): settings window -> hands-off supervisor + tray.

Show the settings window once, then run the SupervisorService in a worker
thread (its own asyncio loop) while pystray owns the main thread.

The window is shown on every start on purpose: recording is a conscious act.
The participant has to put the strap on anyway, so confirming who is recording
and where the file goes belongs to the same decision. Prefs pre-fill the fields
(EW-43), so it stays one click - but it is a click.

The tray's "Stop and exit" sets the supervisor's stop event; "Add note…" routes
text to SupervisorService.add_note; "Show status…" reads the snapshot below.
"""

from __future__ import annotations

import asyncio
import threading

from ..rte.supervisor import SupervisorService
from .settings_window import prompt_settings
from .tray import TrayController
from .tray_icons import battery_text


def run_gui() -> None:
    config = prompt_settings()
    if config is None:
        print("Cancelled.")
        return

    service = SupervisorService(config)
    tray = TrayController(service.status, service.report)
    tray.set_on_note(service.add_note)
    service.on_alert = tray.alert          # silent failures push, not wait (EW-89)
    tray.set_status_source(lambda: {
        "report": service.report.state,
        "battery": battery_text(service.battery.state),
        "participant": config.participant_id,
        "db_path": str(config.db_path),
        "matches": service.matches_recorded,
    })
    service.battery.subscribe(tray.set_battery)

    loop = asyncio.new_event_loop()
    holder: dict[str, asyncio.Event] = {}

    def worker() -> None:
        asyncio.set_event_loop(loop)
        stop = asyncio.Event()
        holder["stop"] = stop
        try:
            loop.run_until_complete(service.run(stop))
        finally:
            loop.close()
            tray.stop()

    def on_quit() -> None:
        stop = holder.get("stop")
        if stop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(stop.set)

    tray.set_on_quit(on_quit)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    tray.run()          # blocks on the main thread until the tray stops
    thread.join(timeout=15)
    # The supervisor deletes the file again when the run recorded no match at
    # all (EW-89), so report what actually happened rather than a path that is
    # no longer there.
    if config.db_path.exists():
        print(f"Recorder stopped -> {config.db_path}")
    else:
        print("Recorder stopped - no match was recorded, no file kept.")
