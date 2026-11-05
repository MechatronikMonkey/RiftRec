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
    participant_id TEXT,                 -- the participant's Riot name (register D6) - plain
                                          -- text until pseudonymised on intake (D7); NULL in
                                          -- the demo, mandatory in the pilot (EW-41)
    session_index  INTEGER,              -- consecutive match no. per participant (EW-41);
                                          -- NULL for kind = 'idle'
    started_utc    TEXT    NOT NULL,      -- ISO-8601 UTC
    ended_utc      TEXT,                 -- NULL until close_session
    mono_anchor_ns INTEGER NOT NULL,     -- perf_counter_ns at started_utc
    app_version    TEXT    NOT NULL,
    schema_version INTEGER NOT NULL,
    notes          TEXT,
    active_riot_id TEXT,                 -- Riot Name#TAG of the recording player, captured
                                          -- from the Live Client API; used by RiftLab to
                                          -- split kill/death/assist from enemy events.
    kind           TEXT,                 -- 'match' or 'idle' (schema 5). Heart rate is
                                          -- recorded from the moment the strap connects;
                                          -- the stretches between matches (lobby, champ
                                          -- select, loading screen, post-game) are their
                                          -- own rows with kind = 'idle' and no game_* rows.
                                          -- NULL in files before schema 5 = 'match'.
    utc_offset_min INTEGER               -- local UTC offset at started_utc, minutes east
                                          -- of UTC (EW-101): time of day for the analysis
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
    level       INTEGER,
    is_dead     INTEGER,          -- 1 while the player is waiting to respawn
    respawn_timer_s REAL          -- seconds left until respawn. Sampled here
                                  -- (5 s) and not only in game_raw (30 s),
                                  -- because EW-61 needs the timer at the moment
                                  -- of death and timers run only 10-50 s.
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

-- Stretches where a signal was not usable. Read this before trusting an
-- interval: an 'h10_contact' gap contains hr_bpm values that look perfectly
-- ordinary and are not (see the note on hr_sample.contact above).
CREATE TABLE IF NOT EXISTS gap (
    session_id TEXT    NOT NULL REFERENCES session(session_id),
    source     TEXT    NOT NULL,        -- 'h10'         BLE link down, no data at all
                                        -- 'h10_contact' link up, strap not reading skin:
                                        --               RR stopped while hr_bpm froze
                                        -- 'riot'        game data unavailable
    start_utc  TEXT    NOT NULL,
    end_utc    TEXT
);

-- Daily check-in (schema 5): a short questionnaire on the confounders the
-- study controls for, followed by a 60 s resting measurement. Offered the
-- first time on a study day the strap delivers beats and no match is live;
-- if closed unanswered, once more after the next match; the tray can open it
-- any time. So a day can have several rows - the analysis picks the first
-- submitted one, and only a resting window before the day's first match is
-- rest. Any single answer may be NULL: participants may skip questions. The heart rate is not stored here - it is
-- in hr_sample / rr_interval of the session that was running, so the resting
-- window is simply [rest_start_utc, rest_end_utc] on that time axis.
-- Answer codes are defined in riftrec/app/checkin_questions.py.
CREATE TABLE IF NOT EXISTS checkin (
    checkin_id            TEXT PRIMARY KEY,
    session_id            TEXT REFERENCES session(session_id), -- running when it opened
    participant_id        TEXT,
    questionnaire_version INTEGER NOT NULL,
    opened_utc            TEXT NOT NULL,
    submitted_utc         TEXT,      -- NULL = closed without answering
    utc_offset_min        INTEGER,
    sleep_quality         INTEGER,   -- 1..5, last main sleep, 3 = normal for you
    sleep_hours           INTEGER,   -- 4 means <= 4, 10 means >= 10
    alcohol               TEXT,      -- last 24 h: 'none' '1-2' '3-4' '5+'
    caffeine              TEXT,      -- last 3 h:  '0' '1' '2' '3+'
    nicotine              TEXT,      -- last 1 h:  'no' 'yes'
    last_meal             TEXT,      -- '<1h' '1-3h' '>3h'
    exercise              TEXT,      -- last 24 h, hardest: 'none' 'light' 'moderate' 'hard'
    exercise_ended        TEXT,      -- '<2h' '2-6h' '>6h'; NULL when exercise = 'none'
    ill                   TEXT,      -- 'no' 'a_bit' 'yes'
    feeling               INTEGER,   -- 1..5, 3 = normal for you
    adhd_medication       TEXT,      -- since waking up: 'no' 'yes' 'never'
    rest_status           TEXT,      -- 'completed' 'skipped' 'aborted'; NULL = not reached
    rest_start_utc        TEXT,
    rest_end_utc          TEXT
);

CREATE INDEX IF NOT EXISTS idx_hr_sample_session   ON hr_sample(session_id, mono_ns);
CREATE INDEX IF NOT EXISTS idx_hr_raw_session      ON hr_raw(session_id, mono_ns);
CREATE INDEX IF NOT EXISTS idx_game_raw_session    ON game_raw(session_id, mono_ns);
CREATE INDEX IF NOT EXISTS idx_rr_interval_session ON rr_interval(session_id, mono_ns);
CREATE INDEX IF NOT EXISTS idx_game_event_session  ON game_event(session_id, mono_ns);
CREATE INDEX IF NOT EXISTS idx_game_snapshot_session ON game_snapshot(session_id, mono_ns);
