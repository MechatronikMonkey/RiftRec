"""RiftRec - PC recorder for the Polar H10 (BLE) + Riot Live Client Data API.

Layers (see README): thin front-end (CLI / later tray) -> RTE (RecorderRuntime,
wires sources through a shared asyncio.Queue into a sink) -> SignalSource
sources (H10 over a BLE HAL, Riot over HTTP) -> SessionSink (SQLite, WAL). Both
sources write under one session_id on a shared clock into the same DB; the
"merge" is therefore a join at analysis time rather than a separate step.
"""

__version__ = "0.1.0"

# Version of the SQLite session schema (= contract between RiftRec and RiftLab).
# Increment on every incompatible schema change.
#
# 2 (EW-86): raw channels `hr_raw` and `game_raw` plus `hr_sample.contact`.
#            Additive only - RiftLab keeps reading version 1 files unchanged.
SCHEMA_VERSION = 2
