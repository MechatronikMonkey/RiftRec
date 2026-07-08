# RiftRec

PC recorder for esports-performance sessions: reads live heart rate / RR / ECG / ACC from the **Polar H10** (BLE) and in-game events from the **Riot Live Client Data API** (`localhost:2999`).

## Architecture

Layered so the H10 data source stays swappable (later switch to a USB dongle) and both streams land time-synchronised in *one* session:

```
CLI / tray+settings GUI (EW-38)             thin front-end
        │
   RTE  │  RecorderRuntime + SessionClock    lifecycle, shared asyncio.Queue,
        │  (wires sources → queue → sink)     session bounds, state machine
        ▼
  SignalSource (protocol)            SessionSink (protocol)
   ├─ FakeSource  (synthetic, for HW-free tests)    └─ SqliteSink (WAL) = contract to RiftLab
   ├─ H10Source   (Polar HR/RR parsing 0x2A37)
   │     └─ HAL: BleTransport → BleakTransport   ← seam for the dongle switch (nrf52840+Bumble)
   └─ RiotSource  (HTTP poll, game start/end, event dedup, snapshots)
```

Core idea: all sources timestamp records on **one** `SessionClock` (`mono_ns` precise + `utc`
anchor) and write under **one** `session_id` into the same SQLite DB. The "merge" of the streams
is therefore a join at analysis time — not a separate step. The HAL boundary is the *BLE
transport* (scan/connect/notify/write), not the Polar semantics; a dongle swaps only the host
BLE stack, not the Polar GATT protocol.

Package: `riftrec/` — `rte/` (runtime+state), `sources/` (fake/h10/riot + `base`), `hal/`
(`ble` protocol + `ble_bleak`), `storage/` (`sqlite_sink` + `schema.sql`), `clock`, `model`,
`config`, `cli`.

## Usage

```
pip install -r requirements.txt

# Hardware-free: synthetic pipe (produces a valid session DB)
python -m riftrec record --source fake --seconds 5 --db demo.sqlite

# Real: H10 + running LoL match, until match end (the Riot source stops the session)
python -m riftrec record --participant P01 --session 3 --source h10,riot --db P01_s3.sqlite
```

Tests (no H10, no match): `PYTHONPATH=. python tests/test_pipe.py` (likewise `test_h10.py`,
`test_riot.py`, `test_session_merge.py`), or `PYTHONPATH=. python -m pytest tests/`.

## Data schema (SQLite = contract to RiftLab)

`riftrec/storage/schema.sql` is authoritative. Tables: `session` (header + `mono_anchor_ns`/
`started_utc` as the mono→UTC anchor), `hr_sample`, `rr_interval` (own table, the load-bearing
HRV signal), `game_event` (deduplicated by Riot `EventID`), `game_snapshot` (KDA/CS/gold trend),
`gap` (dropout marker, EW-39). Schema version in `riftrec/__init__.py:SCHEMA_VERSION`.

## Status (2026-07-06)

**Validated end-to-end against real hardware (M0–M5, EW-26/EW-37 met).** Recorder core
(skeleton+SQLite contract, H10Source/0x2A37 parser, RiotSource with poll/dedup/snapshot/game-end,
two sources → one session) plus the RiftLab viewer (EW-31/36). Real run: worn Polar H10
(HR/RR over the full 180 s) + running LoL Practice Tool via `--source h10,riot` — live events
(10× ChampionKill/FirstBlood/Multikill, correctly deduplicated), snapshots with correct
player matching (riotId), time-sync H10↔Riot, chart from the SQLite contract: the HR spike
coincides with the kill cluster, HRV (RMSSD) inversely.

**Hands-off supervisor + tray/settings GUI validated against real hardware (EW-38).**
`python -m riftrec gui` shows a settings window once (participant id, start session #, H10
device — scan or leave on auto-pick, storage file), then runs unattended: `SupervisorService`
keeps the H10 connected and watches the Riot API continuously, opening/closing one session row
per detected match (auto-incrementing `session_index`) in a single SQLite file; HR arriving
between matches is discarded. A tray icon reflects state (grey idle, amber connecting, green
ready, red recording, slate stopped, purple error) and offers "Add note…" (timestamped, appended
to the current or last-closed session) and "Stop and exit". Real run: a full match recorded
end-to-end unattended (736 HR samples, 894 RR intervals, 23 game events, 145 snapshots, clean
session close).

**Open:** auto-reconnect + `gap` logging on BLE dropout (EW-39), mandatory participant/session
metadata (EW-41), self-report NASA-TLX/PANAS.

## Setup

**Pilots (Windows): just double-click `Start RiftRec.bat`.** On the first run it
creates a local `.venv` and installs the recorder-only dependencies
(`requirements-recorder.txt` — no PMD/dongle spike packages); every run after
that launches the tray recorder straight away. Needs Python 3.11+ on PATH.

Developers / full toolset (tests, spikes, PMD, dongle):

```
pip install -r requirements.txt
```

## Connecting the Polar H10 (Windows)

The standard Heart Rate service (HR/RR) needs no pairing and works immediately. For **raw ECG + acceleration** (PMD protocol, used by `bleakheart.PolarMeasurementData`) the H10 requires an authenticated/bonded BLE connection. On Windows 11 reactive pairing (the dialog that pops up automatically when a script first accesses PMD) does not work reliably — known open issue: [bleak#1943](https://github.com/hbldh/bleak/issues/1943).

**What works — pair the device proactively via Windows Settings before running a script:**

1. Windows Settings → **Bluetooth & devices**
2. **Add device** → Bluetooth
3. If the H10 does not appear in the short list: click **"Show all devices"** at the bottom (unfiltered list)
4. Click **"Polar H10 <serial>"** → **Pair**
5. Wait for the success message "Your device is ready to go" (the device then shows "Not connected" — that is normal, `bleak` connects itself on script start)
6. Only then start the recorder/test script

Prerequisite for any HR/RR measurement: the strap electrodes must be **moistened** and the strap must be **worn on the body** — lying dry on the desk the H10 sends no usable values.

### Known gotcha: BLE scan from a Tkinter thread

Calling `bleak.BleakScanner.discover()` on a thread that has already created a Tkinter window
fails with `BleakError: Thread is configured for Windows GUI but callbacks are not working` —
Windows flags any thread that has created a window as a "GUI thread", and bleak's WinRT backend
can't deliver scan callbacks there. The same scan on a plain thread (no window ever created on
it) works fine. Fix used in `app/settings_window.py`: run `scan_polar_devices()` on a background
`threading.Thread` and marshal the result back via `root.after(...)`, never call it directly from
a Tk callback.

### Known, unresolved issue: ECG/ACC (PMD) only on the first connect

Reproducibly tested (2026-07-05): raw ECG + acceleration (PMD protocol) arrive only on the **very first** BLE connection after a fresh Windows pairing. Every further reconnect to the same paired device returns `SUCCESS` on the control-point commands (`available_settings`, `start_streaming`) but **not a single data notification** anymore — while HR always stays reliable, and re-pairing does not help either.

Tried and **confirmed ineffective**:
- physical H10 reset (strap off skin for 60 s)
- `BleakClient(device, winrt=dict(use_cached_services=False))`
- pauses between the three notify subscriptions (HR/ECG/ACC)
- a full PC reboot
- an explicit `client.pair()` in the script

Suspected cause: Windows apparently only sets up the encryption needed for the authenticated PMD channel cleanly on the first connect after pairing; reconnects to the already-bonded device do get write access (control point) but no push notifications anymore. No known fix in the community (see [bleak#1943](https://github.com/hbldh/bleak/issues/1943), [bleakheart#5](https://github.com/fsmeraldi/bleakheart/issues/5)).

**Consequence for RiftRec:** HR/RR (standard service) is the reliable basis and covers the MVP requirement (EW-26: "heart rate rises in a teamfight"). ECG/ACC via PMD is currently considered not practical on Windows and is deferred — possibly re-check later (different Bluetooth adapter, Linux, or if bleak/Windows fix it upstream).

## Folder structure

- `riftrec/` — the recorder package (see Architecture above)
- `tests/` — hardware-/match-free tests (parser, sources via fakes, end-to-end pipe)
- `spikes/` — short technical feasibility checks (not for continuous operation, no formal test suite)
  - `h10_ble_scan.py` — pure BLE discovery test: is the H10 found?
  - `h10_ping.py` — connects, pulls 3 frames each of HR/RR + ECG + ACC, measures timing (min/avg/max inter-arrival), like `ping`
  - `h10_simpleble_probe.py` — confirmation test with SimpleBLE (cross-check of the PMD bug)
  - `h10_bumble_probe.py` — talk to the H10 through Google's Bumble user-space BLE stack (WinRT bypass; needs a USB dongle)
