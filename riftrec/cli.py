"""CLI front-end for RiftRec.

Kept thin: parses arguments into a RecorderConfig, builds sources + sink, and
runs the RecorderRuntime. The later tray/settings front-end (EW-38) plugs into
the same place without touching core code.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
from pathlib import Path

from .config import RecorderConfig
from .rte.runtime import RecorderRuntime
from .rte.state import RecorderState
from .sources.base import SignalSource
from .storage.sqlite_sink import SqliteSink


# Everything the recorder touches at runtime, including the paths only reached
# once a strap connects or a window opens - those are exactly the imports a
# frozen build tends to drop, because nothing imports them at start-up.
_RUNTIME_MODULES = (
    "asyncio", "sqlite3", "zlib", "json", "configparser", "subprocess",
    "tkinter", "tkinter.ttk", "tkinter.filedialog", "tkinter.messagebox",
    "tkinter.simpledialog",
    "httpx", "bleak", "pystray", "PIL.Image", "PIL.ImageDraw",
    "riftrec.cli", "riftrec.config", "riftrec.model", "riftrec.clock",
    "riftrec.app.runner", "riftrec.app.tray", "riftrec.app.tray_icons",
    "riftrec.app.settings_window", "riftrec.app.status_window",
    "riftrec.app.prefs", "riftrec.app.device_scan", "riftrec.app.single_instance",
    "riftrec.app.reveal",
    "riftrec.rte.supervisor", "riftrec.rte.runtime", "riftrec.rte.status",
    "riftrec.rte.state", "riftrec.rte.health",
    "riftrec.sources.h10", "riftrec.sources.riot", "riftrec.sources.game_process",
    "riftrec.storage.sqlite_sink", "riftrec.hal.ble_bleak",
)


def _build_sources(config: RecorderConfig) -> list[SignalSource]:
    sources: list[SignalSource] = []
    for name in config.sources:
        if name == "fake":
            from .sources.fake import FakeSource

            ticks = int(config.duration_s) if config.duration_s else 10
            sources.append(FakeSource(ticks=ticks))
        elif name == "h10":
            from .sources.h10 import H10Source

            sources.append(H10Source(device=config.device))
        elif name == "riot":
            from .sources.riot import RiotSource

            sources.append(RiotSource(
                poll_interval_s=config.poll_interval_s,
                snapshot_interval_s=config.snapshot_interval_s,
            ))
        else:
            raise SystemExit(f"Unknown source: {name!r} (allowed: fake, h10, riot)")
    return sources


async def _run(config: RecorderConfig) -> None:
    sink = SqliteSink(config.db_path)
    runtime = RecorderRuntime(
        _build_sources(config),
        sink,
        participant_id=config.participant_id,
        session_index=config.session_index,
        duration_s=config.duration_s,
        notes=config.notes,
    )
    runtime.status.subscribe(lambda s: print(f"[state] {s.value}"))
    print(f"Recording starts -> {config.db_path}")
    session_id = await runtime.run()
    print(f"Session {session_id} finished.")


def _parse_args(argv: list[str] | None) -> "RecorderConfig | str":
    parser = argparse.ArgumentParser(prog="riftrec", description="RiftRec recorder")
    sub = parser.add_subparsers(dest="command", required=True)

    rec = sub.add_parser("record", help="Record a session")
    rec.add_argument("--participant", dest="participant_id", default=None)
    rec.add_argument("--session", dest="session_index", type=int, default=None)
    rec.add_argument("--notes", default=None)
    rec.add_argument(
        "--source", dest="sources", default="fake",
        help="Comma-separated: fake, h10, riot (e.g. --source h10,riot)",
    )
    rec.add_argument("--db", dest="db_path", default="riftrec_session.sqlite")
    rec.add_argument("--seconds", dest="duration_s", type=float, default=None,
                     help="Optional fixed runtime; otherwise until Ctrl+C / sources end")
    rec.add_argument("--device", default=None, help="H10 name/address (otherwise auto-scan)")
    rec.add_argument("--poll-interval", dest="poll_interval_s", type=float, default=1.0)
    rec.add_argument("--snapshot-interval", dest="snapshot_interval_s", type=float, default=5.0)

    sub.add_parser("gui", help="Launch the settings window + hands-off tray recorder (EW-38)")
    sub.add_parser(
        "selfcheck",
        help="Import everything the recorder needs and exit 0/1 (packaging smoke test)")

    args = parser.parse_args(argv)
    if args.command in ("gui", "selfcheck"):
        return args.command
    return RecorderConfig(
        participant_id=args.participant_id,
        session_index=args.session_index,
        notes=args.notes,
        sources=[s.strip() for s in args.sources.split(",") if s.strip()],
        db_path=Path(args.db_path),
        duration_s=args.duration_s,
        device=args.device,
        poll_interval_s=args.poll_interval_s,
        snapshot_interval_s=args.snapshot_interval_s,
    )


def _selfcheck() -> int:
    """Import every runtime dependency and check the bundled data files (EW-89).

    This is the packaging smoke test. The reason the zipped version never
    started on Bicas' PC was a missing Python dependency; in a frozen build the
    same class of failure - a module PyInstaller did not notice, or the schema
    file left out of the bundle - shows up as a window that simply never
    appears, with nothing on screen to explain it. Running this against the
    built exe turns that into a failed build instead of a failed participant.

    Returns a process exit code: 0 = everything imports, 1 = something is
    missing (each miss is printed, and under pythonw lands in riftrec.log).
    """
    problems: list[str] = []
    for name in _RUNTIME_MODULES:
        try:
            importlib.import_module(name)
        except Exception as exc:  # ImportError, but a broken C-ext can raise anything
            problems.append(f"import {name}: {exc}")

    # schema.sql is read from disk at runtime, so it has to be inside the
    # bundle - PyInstaller does not pick up non-Python files by itself.
    try:
        from .storage import sqlite_sink

        schema = Path(sqlite_sink.__file__).with_name("schema.sql")
        if not schema.exists():
            problems.append(f"missing data file: {schema}")
    except Exception as exc:
        problems.append(f"storage layer unusable: {exc}")

    # The blind-recorder detector (EW-89) shells out to `tasklist`. A frozen,
    # windowed build is exactly where spawning a console process can quietly
    # stop working, and the symptom would be a detector that never fires.
    import sys

    if sys.platform == "win32":
        try:
            from .sources.game_process import is_game_running

            if is_game_running() is None:
                problems.append("game process probe: could not read the process list")
        except Exception as exc:
            problems.append(f"game process probe: {exc}")

    if problems:
        print(f"[selfcheck] FAILED - {len(problems)} problem(s):")
        for line in problems:
            print(f"[selfcheck]   {line}")
        return 1
    print(f"[selfcheck] OK - {len(_RUNTIME_MODULES)} modules, schema.sql present")
    return 0


def _redirect_output_if_windowless() -> None:
    """Under pythonw.exe (no console) sys.stdout/stderr are None, so the app's
    print() calls would crash. Send them to a log file instead - which doubles
    as a troubleshooting log for pilots."""
    import sys

    if sys.stdout is not None and sys.stderr is not None:
        return  # normal console run - leave streams alone
    import os

    log_dir = Path(os.environ.get("APPDATA") or Path.home()) / "RiftRec"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        sink = open(log_dir / "riftrec.log", "a", buffering=1, encoding="utf-8")
    except OSError:
        import io

        sink = io.StringIO()  # last resort: swallow output rather than crash
    if sys.stdout is None:
        sys.stdout = sink
    if sys.stderr is None:
        sys.stderr = sink


def main(argv: list[str] | None = None) -> None:
    config = _parse_args(argv)
    if config == "selfcheck":
        _redirect_output_if_windowless()
        raise SystemExit(_selfcheck())
    if config == "gui":
        _redirect_output_if_windowless()
        from .app.single_instance import acquire_single_instance, warn_already_running

        if not acquire_single_instance():
            warn_already_running()
            return
        from .app.runner import run_gui

        run_gui()
        return
    try:
        asyncio.run(_run(config))
    except KeyboardInterrupt:
        print("\nAborted - closing session.")


if __name__ == "__main__":
    main()
