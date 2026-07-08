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
    hr_bpm     INTEGER NOT NULL
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

CREATE TABLE IF NOT EXISTS gap (
    session_id TEXT    NOT NULL REFERENCES session(session_id),
    source     TEXT    NOT NULL,        -- 'h10' | 'riot'
    start_utc  TEXT    NOT NULL,
    end_utc    TEXT
);

CREATE INDEX IF NOT EXISTS idx_hr_sample_session   ON hr_sample(session_id, mono_ns);
CREATE INDEX IF NOT EXISTS idx_rr_interval_session ON rr_interval(session_id, mono_ns);
CREATE INDEX IF NOT EXISTS idx_game_event_session  ON game_event(session_id, mono_ns);
CREATE INDEX IF NOT EXISTS idx_game_snapshot_session ON game_snapshot(session_id, mono_ns);
