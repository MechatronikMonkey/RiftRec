-- Canonical RiftRec session schema.
-- This IS the contract between RiftRec (recorder) and RiftLab (analysis):
-- RiftLab reads exactly these tables. Changes here => bump SCHEMA_VERSION.
--
-- Time axes per sample: mono_ns (perf_counter_ns, precise ordering) + utc
-- (ISO-8601, cross-stream alignment). session.mono_anchor_ns +
-- session.started_utc map mono_ns -> utc. Riot rows also carry game_time_s
-- (in-game clock).

CREATE TABLE IF NOT EXISTS session (
    session_id     TEXT    PRIMARY KEY,
    participant_id TEXT,                 -- pseudonymous; NULL in the demo, mandatory in the pilot (EW-41)
    session_index  INTEGER,              -- consecutive session no. per participant (EW-41)
    started_utc    TEXT    NOT NULL,      -- ISO-8601 UTC
    ended_utc      TEXT,                 -- NULL until close_session
    mono_anchor_ns INTEGER NOT NULL,     -- perf_counter_ns at started_utc
    app_version    TEXT    NOT NULL,
    schema_version INTEGER NOT NULL,
    notes          TEXT,
    active_riot_id TEXT                  -- Riot Name#TAG of the recording player, captured
                                          -- from the Live Client API; used by RiftLab to
                                          -- split kill/death/assist from enemy events.
);

CREATE TABLE IF NOT EXISTS hr_sample (
    session_id TEXT    NOT NULL REFERENCES session(session_id),
    mono_ns    INTEGER NOT NULL,
    utc        TEXT    NOT NULL,
    hr_bpm     INTEGER NOT NULL,
    contact    INTEGER              -- BLE sensor contact: 1 = skin contact, 0 = none,
                                     -- NULL = device does not report it.
                                     -- NOTE: the Polar H10 does NOT report it (verified
                                     -- 21.08.2026, flags byte 0x10 - contact-supported bit
                                     -- never set), so this stays NULL with our hardware.
                                     -- Kept spec-compliant for other devices. To detect
                                     -- contact loss use the RR channel instead: RR
                                     -- intervals stop arriving ~10 s before hr_bpm drops
                                     -- to 0, and in between the H10 emits a FROZEN but
                                     -- plausible HR value. See the note in README.
);

-- Unparsed HR notification payloads. Keeps a parser bug recoverable and
-- preserves fields we do not decode today (EW-86).
CREATE TABLE IF NOT EXISTS hr_raw (
    session_id TEXT    NOT NULL REFERENCES session(session_id),
    mono_ns    INTEGER NOT NULL,
    utc        TEXT    NOT NULL,
    payload    BLOB    NOT NULL
);

-- Complete `allgamedata` responses, zlib-compressed JSON. Holds everything the
-- parsed tables drop: champion, position, team, items, runes and the full
-- scoreboard of all ten players. Foreign Riot IDs are pseudonymised before
-- storage; the recording player's own id is left as-is (see session.active_riot_id).
CREATE TABLE IF NOT EXISTS game_raw (
    session_id   TEXT    NOT NULL REFERENCES session(session_id),
    mono_ns      INTEGER NOT NULL,
    utc          TEXT    NOT NULL,
    game_time_s  REAL,
    payload_zlib BLOB    NOT NULL
);

CREATE TABLE IF NOT EXISTS rr_interval (
    session_id TEXT    NOT NULL REFERENCES session(session_id),
    mono_ns    INTEGER NOT NULL,
    utc        TEXT    NOT NULL,
    rr_ms      REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS game_event (
    session_id   TEXT    NOT NULL REFERENCES session(session_id),
    mono_ns      INTEGER NOT NULL,
    utc          TEXT    NOT NULL,
    game_time_s  REAL,
    event_id     INTEGER,               -- Riot EventID, for deduplication
    event_type   TEXT    NOT NULL,      -- ChampionKill, TurretKilled, DragonKill, ...
    payload_json TEXT
);

CREATE TABLE IF NOT EXISTS game_snapshot (
    session_id  TEXT    NOT NULL REFERENCES session(session_id),
    mono_ns     INTEGER NOT NULL,
    utc         TEXT    NOT NULL,
    game_time_s REAL,
    kills       INTEGER,
    deaths      INTEGER,
    assists     INTEGER,
    cs          INTEGER,
    gold        REAL,
    level       INTEGER
);

-- Identity and state of each sensor per session. Lets a recording be traced
-- back to the physical strap - essential once straps rotate between
-- participants - and records the firmware, because Polar changed BLE
-- behaviour within the 4.x line. Battery is captured per session since a weak
-- cell is a plausible confounder for signal quality.
CREATE TABLE IF NOT EXISTS device_info (
    session_id   TEXT NOT NULL REFERENCES session(session_id),
    source       TEXT NOT NULL,      -- 'h10'
    utc          TEXT NOT NULL,      -- when it was read
    address      TEXT,
    name         TEXT,
    manufacturer TEXT,
    model        TEXT,
    serial       TEXT,               -- identifies the individual strap
    hardware_rev TEXT,
    firmware_rev TEXT,
    software_rev TEXT,               -- the number Polar's release notes use
    battery_pct  INTEGER
);

CREATE TABLE IF NOT EXISTS gap (
    session_id TEXT    NOT NULL REFERENCES session(session_id),
    source     TEXT    NOT NULL,        -- 'h10' | 'riot'
    start_utc  TEXT    NOT NULL,
    end_utc    TEXT
);

CREATE INDEX IF NOT EXISTS idx_hr_sample_session   ON hr_sample(session_id, mono_ns);
CREATE INDEX IF NOT EXISTS idx_hr_raw_session      ON hr_raw(session_id, mono_ns);
CREATE INDEX IF NOT EXISTS idx_game_raw_session    ON game_raw(session_id, mono_ns);
CREATE INDEX IF NOT EXISTS idx_rr_interval_session ON rr_interval(session_id, mono_ns);
CREATE INDEX IF NOT EXISTS idx_game_event_session  ON game_event(session_id, mono_ns);
CREATE INDEX IF NOT EXISTS idx_game_snapshot_session ON game_snapshot(session_id, mono_ns);
