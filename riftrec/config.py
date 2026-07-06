"""Laufzeit-Konfiguration des Recorders."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class RecorderConfig:
    # Session-Metadaten (im Demo optional, in der Pilot-Härtung Pflicht - EW-41)
    participant_id: Optional[str] = None
    session_index: Optional[int] = None
    notes: Optional[str] = None

    # Welche Quellen laufen: Teilmenge von {"fake", "h10", "riot"}
    sources: list[str] = field(default_factory=lambda: ["fake"])

    # Ausgabe
    db_path: Path = Path("riftrec_session.sqlite")

    # Laufzeit-Steuerung
    duration_s: Optional[float] = None

    # H10 (BLE)
    device: Optional[str] = None  # Name oder Adresse; None => automatisch scannen

    # Riot Live Client Data API
    poll_interval_s: float = 1.0
    snapshot_interval_s: float = 5.0
