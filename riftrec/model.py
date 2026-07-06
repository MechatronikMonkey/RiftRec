"""Datenmodell: die Records, die durch die Runtime-Queue in den Sink fließen.

Jeder Sample-Record trägt zwei Zeitstempel: `mono_ns` (perf_counter_ns, für
präzise Ordnung/Inter-Arrival innerhalb einer Session) und `utc` (ISO-8601,
für die streamübergreifende Ausrichtung). Beide werden von der `SessionClock`
bei Ankunft der Daten gesetzt. Riot-Records tragen zusätzlich `game_time_s`
(In-Game-Uhr) als dritte, gegen Reconnects robuste Zeitachse.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union


@dataclass(slots=True)
class HrSample:
    """Ein Herzfrequenz-Wert (bpm) aus einer HR-Measurement-Notification."""

    mono_ns: int
    utc: str
    hr_bpm: int


@dataclass(slots=True)
class RrInterval:
    """Ein RR-Intervall (ms) — das tragende Signal für die HRV-Auswertung.

    Eine einzelne HR-Notification kann 0..n RR-Intervalle enthalten; jedes wird
    zu einem eigenen Record, damit RiftLab die Schlag-zu-Schlag-Reihe lückenlos
    rekonstruieren kann.
    """

    mono_ns: int
    utc: str
    rr_ms: float


@dataclass(slots=True)
class GameEvent:
    """Ein diskretes In-Game-Event aus der Riot Live Client Data API.

    `event_id` ist die Riot-`EventID` (monoton je Match) und dient der
    Deduplizierung über aufeinanderfolgende Polls. `payload_json` hält das
    rohe Event-Objekt, damit später weitere Felder ausgewertet werden können,
    ohne den Recorder zu ändern.
    """

    mono_ns: int
    utc: str
    game_time_s: Optional[float]
    event_id: Optional[int]
    event_type: str
    payload_json: str


@dataclass(slots=True)
class GameSnapshot:
    """Periodischer Scoreboard-Schnappschuss des aktiven Spielers.

    Liefert Verlaufsgrößen (KDA/CS/Gold/Level) für Game-Phasen-Analysen, die
    aus den diskreten Events allein nicht ablesbar sind.
    """

    mono_ns: int
    utc: str
    game_time_s: Optional[float]
    kills: Optional[int]
    deaths: Optional[int]
    assists: Optional[int]
    cs: Optional[int]
    gold: Optional[float]
    level: Optional[int]


@dataclass(slots=True)
class Gap:
    """Marker für eine Verbindungslücke einer Quelle (EW-39).

    Damit unterscheidet RiftLab "Daten fehlen" von "Signal war flach".
    `end_utc` bleibt offen, bis die Quelle wieder verbunden ist.
    """

    source: str
    start_utc: str
    end_utc: Optional[str] = None


# Alles, was eine Quelle über `emit` in die Queue legen darf.
Record = Union[HrSample, RrInterval, GameEvent, GameSnapshot]


@dataclass(slots=True)
class SessionMeta:
    """Kopfdaten einer Aufnahme-Session (eine Zeile in `session`).

    `mono_anchor_ns` + `started_utc` bilden den Anker, mit dem jeder `mono_ns`
    eines Records auf UTC abgebildet werden kann.
    """

    session_id: str
    participant_id: Optional[str]
    session_index: Optional[int]
    started_utc: str
    mono_anchor_ns: int
    app_version: str
    schema_version: int
    notes: Optional[str] = None
    ended_utc: Optional[str] = None
