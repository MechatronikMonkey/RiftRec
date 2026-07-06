"""Runner (EW-38): settings window -> hands-off supervisor + tray.

Shows the settings window once, then runs the SupervisorService in a worker
thread (its own asyncio loop) while pystray owns the main thread. The tray's
"Stop and exit" sets the supervisor's stop event; "Add note…" routes text to
SupervisorService.add_note.
"""

from __future__ import annotations

import asyncio
import threading

from ..rte.supervisor import SupervisorService
from .settings_window import prompt_settings
from .tray import TrayController


def run_gui() -> None:
    config = prompt_settings()
    if config is None:
        print("Cancelled.")
        return

    service = SupervisorService(config)
    tray = TrayController(service.status)
    tray.set_on_note(service.add_note)

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
    print(f"Recorder stopped -> {config.db_path}")
