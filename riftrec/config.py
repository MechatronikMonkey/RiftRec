"""Runtime configuration of the recorder."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class RecorderConfig:
    # Session metadata (optional in the demo, mandatory for the pilot - EW-41)
    participant_id: Optional[str] = None
    session_index: Optional[int] = None
    notes: Optional[str] = None

    # Which sources run: subset of {"fake", "h10", "riot"}
    sources: list[str] = field(default_factory=lambda: ["fake"])

    # Output
    db_path: Path = Path("riftrec_session.sqlite")

    # Runtime control
    duration_s: Optional[float] = None

    # H10 (BLE)
    device: Optional[str] = None  # name or address; None => scan automatically

    # Riot Live Client Data API
    poll_interval_s: float = 1.0
    snapshot_interval_s: float = 5.0

    # How often the supervisor commits the buffered rows to SQLite. Decoupled
    # from poll_interval_s so a burst of poll ticks (objective/teamfight kills)
    # does not turn into a burst of synchronous commits/checkpoints - which an
    # on-access AV shield (Avast) scans one by one and which briefly block the
    # watch loop (EW-51). Mirrors RecorderRuntime's flush throttle.
    flush_interval_s: float = 2.0

    # When the H10 drops (out of range, strap off), how long to wait between
    # reconnect attempts. bleak does not reconnect on its own; the supervisor
    # detects the drop, logs a gap, and re-establishes the link (EW-42).
    reconnect_backoff_s: float = 3.0
