"""Data model: the records that flow through the runtime queue into the sink.

Every sample record carries two timestamps: `mono_ns` (perf_counter_ns, for
precise ordering / inter-arrival within a session) and `utc` (ISO-8601, for
cross-stream alignment). Both are set by the `SessionClock` when the data
arrives. Riot records additionally carry `game_time_s` (in-game clock) as a
third time axis that is robust against reconnects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union


@dataclass(slots=True)
class HrSample:
    """A heart-rate value (bpm) from an HR measurement notification.

    `contact` carries the BLE sensor contact status: True/False when the sensor
    reports it, None when the device does not support the feature. It is the
    independent criterion for "discard this window" that the correction rate
    alone cannot provide (EW-86).
    """

    mono_ns: int
    utc: str
    hr_bpm: int
    contact: Optional[bool] = None


@dataclass(slots=True)
class RrInterval:
    """An RR interval (ms) - the load-bearing signal for HRV analysis.

    A single HR notification can carry 0..n RR intervals; each becomes its own
    record so RiftLab can reconstruct the beat-to-beat series without gaps.
    """

    mono_ns: int
    utc: str
    rr_ms: float


@dataclass(slots=True)
class GameEvent:
    """A discrete in-game event from the Riot Live Client Data API.

    `event_id` is the Riot `EventID` (monotonic per match) and is used for
    deduplication across successive polls. `payload_json` holds the raw event
    object so more fields can be analysed later without changing the recorder.
    """

    mono_ns: int
    utc: str
    game_time_s: Optional[float]
    event_id: Optional[int]
    event_type: str
    payload_json: str


@dataclass(slots=True)
class GameSnapshot:
    """A periodic scoreboard snapshot of the active player.

    Provides trend quantities (KDA/CS/gold/level) for game-phase analyses that
    the discrete events alone cannot express.
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
class HrRaw:
    """The unparsed 0x2A37 notification payload (EW-86).

    Stored alongside the parsed HrSample/RrInterval rows so that a parser bug
    is recoverable and fields we do not decode today (e.g. Energy Expended)
    are not lost. Payloads are a handful of bytes, so this is cheap.
    """

    mono_ns: int
    utc: str
    payload: bytes


@dataclass(slots=True)
class GameRaw:
    """A complete `allgamedata` response, zlib-compressed (EW-86).

    The parsed tables keep only the active player's scoreboard. Everything else
    the API delivers - champion, position, team, items, runes and the full
    enemy scoreboard for all ten players - lives only here. Compressed because
    an uncompressed response is ~40 kB and the return path is e-mail, not cloud.

    Foreign Riot IDs are pseudonymised before compression; see
    `riot.pseudonymise_foreign_ids`.
    """

    mono_ns: int
    utc: str
    game_time_s: Optional[float]
    payload_zlib: bytes


@dataclass(slots=True)
class DeviceInfo:
    """Identity and state of a sensor at the start of a session (EW-86).

    Written once per session so a recording can always be traced back to the
    physical device that produced it. With straps rotating between participants
    that is the only way to find a unit that systematically underperforms, and
    the firmware version matters because Polar changed BLE behaviour inside the
    4.x line. `battery_pct` is read per session because a weak cell is a
    plausible confounder for signal quality.

    Every field is optional: a failed read must never abort a recording.
    """

    source: str
    utc: str
    address: Optional[str] = None
    name: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    serial: Optional[str] = None
    hardware_rev: Optional[str] = None
    firmware_rev: Optional[str] = None
    software_rev: Optional[str] = None
    battery_pct: Optional[int] = None


@dataclass(slots=True)
class Gap:
    """Marker for a connection gap of a source (EW-39).

    Lets RiftLab tell "data missing" apart from "signal was flat". `end_utc`
    stays open until the source is reconnected.
    """

    source: str
    start_utc: str
    end_utc: Optional[str] = None


# Everything a source may put onto the queue via `emit`.
Record = Union[HrSample, RrInterval, GameEvent, GameSnapshot, HrRaw, GameRaw]


@dataclass(slots=True)
class SessionMeta:
    """Header data of a recording session (one row in `session`).

    `mono_anchor_ns` + `started_utc` form the anchor that maps every `mono_ns`
    of a record onto UTC.
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
