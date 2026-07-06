"""CLI front-end for RiftRec.

Kept thin: parses arguments into a RecorderConfig, builds sources + sink, and
runs the RecorderRuntime. The later tray/settings front-end (EW-38) plugs into
the same place without touching core code.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .config import RecorderConfig
from .rte.runtime import RecorderRuntime
from .rte.state import RecorderState
from .sources.base import SignalSource
from .storage.sqlite_sink import SqliteSink


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


def _parse_args(argv: list[str] | None) -> RecorderConfig:
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

    args = parser.parse_args(argv)
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


def main(argv: list[str] | None = None) -> None:
    config = _parse_args(argv)
    try:
        asyncio.run(_run(config))
    except KeyboardInterrupt:
        print("\nAborted - closing session.")


if __name__ == "__main__":
    main()
