"""RiftRec - PC-Recorder für Polar H10 (BLE) + Riot Live Client Data API.

Schichten (siehe README): dünnes Front-End (CLI / später Tray) → RTE
(RecorderRuntime, verdrahtet Quellen über eine gemeinsame asyncio.Queue in
einen Sink) → SignalSource-Quellen (H10 über eine BLE-HAL, Riot über HTTP) →
SessionSink (SQLite, WAL). Beide Quellen schreiben unter einer session_id auf
einer gemeinsamen Uhr in dieselbe DB; der "Merge" ist damit ein Join zur
Auswertungszeit statt eines eigenen Schritts.
"""

__version__ = "0.1.0"

# Version des SQLite-Session-Schemas (= Vertrag zwischen RiftRec und RiftLab).
# Bei jeder inkompatiblen Schemaänderung erhöhen.
SCHEMA_VERSION = 1
