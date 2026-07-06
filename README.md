# RiftRec

PC-Recorder für Esports-Performance-Sessions: liest Live-Herzfrequenz/RR/EKG/ACC vom **Polar H10** (BLE) und In-Game-Events aus der **Riot Live Client Data API** (`localhost:2999`) aus.

## Architektur

Geschichtet, damit die H10-Datenquelle austauschbar bleibt (späterer Wechsel auf einen USB-Dongle) und beide Streams zeitsynchron in *einer* Session landen:

```
CLI  (später Tray+Settings, EW-38)          dünnes Front-End
        │
   RTE  │  RecorderRuntime + SessionClock    Lifecycle, gemeinsame asyncio.Queue,
        │  (verdrahtet Quellen → Queue → Sink)  Session-Grenzen, Zustandsmaschine
        ▼
  SignalSource (Protocol)            SessionSink (Protocol)
   ├─ FakeSource  (synthetisch, für HW-lose Tests)   └─ SqliteSink (WAL) = Vertrag zu RiftLab
   ├─ H10Source   (Polar HR/RR-Parsing 0x2A37)
   │     └─ HAL: BleTransport → BleakTransport   ← Naht für Dongle-Wechsel (nrf52840+Bumble)
   └─ RiotSource  (HTTP-Poll, Game-Start/-Ende, Event-Dedup, Snapshots)
```

Kernidee: alle Quellen stempeln Records auf **einer** `SessionClock` (`mono_ns` präzise + `utc`
als Anker) und schreiben unter **einer** `session_id` in dieselbe SQLite-DB. Der „Merge“ der
Streams ist damit ein Join zur Auswertungszeit — kein eigener Schritt. Die HAL-Grenze ist der
*BLE-Transport* (scan/connect/notify/write), nicht die Polar-Semantik; ein Dongle tauscht nur
den Host-BLE-Stack, nicht das Polar-GATT-Protokoll.

Paket: `riftrec/` — `rte/` (Runtime+State), `sources/` (fake/h10/riot + `base`), `hal/`
(`ble` Protocol + `ble_bleak`), `storage/` (`sqlite_sink` + `schema.sql`), `clock`, `model`,
`config`, `cli`.

## Nutzung

```
pip install -r requirements.txt

# Hardware-los: synthetische Pipe (erzeugt eine gültige Session-DB)
python -m riftrec record --source fake --seconds 5 --db demo.sqlite

# Echt: H10 + laufendes LoL-Match, bis Match-Ende (Riot-Quelle stoppt die Session)
python -m riftrec record --participant P01 --session 3 --source h10,riot --db P01_s3.sqlite
```

Tests (ohne H10, ohne Match): `PYTHONPATH=. python tests/test_pipe.py` (analog `test_h10.py`,
`test_riot.py`, `test_session_merge.py`), oder `PYTHONPATH=. python -m pytest tests/`.

## Datenschema (SQLite = Vertrag zu RiftLab)

`riftrec/storage/schema.sql` ist maßgeblich. Tabellen: `session` (Kopf + `mono_anchor_ns`/
`started_utc` als mono→UTC-Anker), `hr_sample`, `rr_interval` (eigene Tabelle, tragendes
HRV-Signal), `game_event` (dedupliziert per Riot-`EventID`), `game_snapshot` (KDA/CS/Gold-
Verlauf), `gap` (Dropout-Marker, EW-39). Schema-Version in `riftrec/__init__.py:SCHEMA_VERSION`.

## Stand (2026-07-06)

**End-to-End gegen echte Hardware validiert (M0–M5, EW-26/EW-37 erfüllt).** Recorder-Kern
(Skelett+SQLite-Vertrag, H10Source/0x2A37-Parser, RiotSource mit Poll/Dedup/Snapshot/Game-Ende,
zwei Quellen → eine Session) plus RiftLab-Viewer (EW-31/36). Realer Lauf: getragener Polar H10
(HR/RR über volle 180 s) + laufendes LoL-Practice-Tool über `--source h10,riot` — Live-Events
(10× ChampionKill/FirstBlood/Multikill, korrekt dedupliziert), Snapshots mit korrektem
Spieler-Matching (riotId), Zeitsync H10↔Riot, Chart aus dem SQLite-Vertrag: HR-Spitze fällt
mit dem Kill-Cluster zusammen, HRV (RMSSD) gegenläufig.

**Offen:** Pilot-Härtung — Tray/Settings-UI (EW-38), Auto-Reconnect + `gap`-Logging bei
BLE-Dropout (EW-39), Pflicht-Metadaten participant/session (EW-41), Self-Report NASA-TLX/PANAS.

## Setup

```
pip install -r requirements.txt
```

## Polar H10 verbinden (Windows)

Der Standard-Heart-Rate-Service (HR/RR) braucht kein Pairing und funktioniert sofort. Für **rohes EKG + Beschleunigung** (PMD-Protokoll, genutzt von `bleakheart.PolarMeasurementData`) verlangt der H10 eine authentifizierte/gebondete BLE-Verbindung. Auf Windows 11 funktioniert das reaktive Pairing (der Dialog, der automatisch aufpoppt, wenn ein Skript zum ersten Mal auf PMD zugreift) nicht zuverlässig — bekanntes offenes Problem: [bleak#1943](https://github.com/hbldh/bleak/issues/1943).

**Funktionierender Weg — Gerät proaktiv über die Windows-Einstellungen koppeln, bevor ein Skript läuft:**

1. Windows-Einstellungen → **Bluetooth & Geräte**
2. **Gerät hinzufügen** → Bluetooth
3. Falls der H10 nicht in der Kurzliste auftaucht: unten auf **"Alle Geräte anzeigen"** klicken (ungefilterte Liste)
4. **"Polar H10 <Seriennummer>"** anklicken → **Koppeln**
5. Erfolgsmeldung "Ihr Gerät ist einsatzbereit" abwarten (Gerät zeigt danach "Nicht verbunden" — das ist normal, `bleak` verbindet sich bei Skriptstart selbst)
6. Erst danach das Recorder-/Test-Skript starten

Voraussetzung für jede HR/RR-Messung: Elektroden am Gurt müssen **angefeuchtet** sein und der Gurt muss **am Körper getragen** werden — trocken auf dem Tisch liegend sendet der H10 keine verwertbaren Werte.

### Bekanntes, ungelöstes Problem: EKG/ACC (PMD) nur beim ersten Connect

Reproduzierbar getestet (2026-07-05): rohes EKG + Beschleunigung (PMD-Protokoll) kommen nur bei der **allerersten** BLE-Verbindung nach einem frischen Windows-Pairing an. Jeder weitere Reconnect zum selben gekoppelten Gerät liefert `SUCCESS` auf die Control-Point-Befehle (`available_settings`, `start_streaming`), aber **keine einzige Daten-Notification** mehr — weder HR (das bleibt immer zuverlässig) noch neu koppeln hilft dabei.

Durchprobiert und **bestätigt wirkungslos**:
- physischer H10-Reset (Gurt 60s vom Körper)
- `BleakClient(device, winrt=dict(use_cached_services=False))`
- Pausen zwischen den drei Notify-Anmeldungen (HR/ECG/ACC)
- kompletter PC-Neustart
- explizites `client.pair()` im Skript

Vermutete Ursache: Windows baut die für den authentifizierten PMD-Kanal nötige Verschlüsselung offenbar nur beim ersten Connect nach dem Pairing sauber auf; Reconnects zum bereits gebondeten Gerät bekommen zwar Schreibzugriff (Control-Point), aber keine Push-Notifications mehr. Kein bekannter Fix in der Community (siehe [bleak#1943](https://github.com/hbldh/bleak/issues/1943), [bleakheart#5](https://github.com/fsmeraldi/bleakheart/issues/5)).

**Konsequenz für RiftRec:** HR/RR (Standard-Service) ist die verlässliche Basis und deckt die MVP-Anforderung (EW-26: "Herzfrequenz steigt im Teamfight"). EKG/ACC via PMD gilt auf Windows aktuell als nicht praxistauglich und ist zurückgestellt — ggf. später erneut prüfen (anderer Bluetooth-Adapter, Linux, oder falls bleak/Windows das upstream fixen).

## Ordnerstruktur

- `riftrec/` — das Recorder-Paket (siehe Architektur oben)
- `tests/` — hardware-/spielfreie Tests (Parser, Quellen via Fakes, End-to-End-Pipe)
- `spikes/` — kurze technische Vorabklärungen/Machbarkeitschecks (kein Dauerbetrieb, keine formale Testsuite)
  - `h10_ble_scan.py` — reiner BLE-Discovery-Test, ob der H10 gefunden wird
  - `h10_ping.py` — verbindet sich, holt je 3 Frames HR/RR + EKG + ACC, misst Timing (min/avg/max Inter-Arrival), analog zu `ping`
