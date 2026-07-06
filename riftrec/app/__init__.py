"""GUI front-end (EW-38): pre-game settings window + in-game tray icon.

Kept separate from the core so the recorder stays a headless, testable engine.
The tray only observes `RecorderRuntime.status`; the runtime runs in a worker
thread while pystray owns the main thread.
"""
