"""SQLite helpers and schema for the World Cup dashboard."""

import sqlite3

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS venues (
    ground   TEXT PRIMARY KEY,   -- openfootball ground string (join key)
    stadium  TEXT,
    city     TEXT,
    country  TEXT,
    lat      REAL,
    lng      REAL,
    tz       TEXT,
    roof     TEXT                 -- 'open' | 'retractable' | 'fixed' (open vs covered)
);

CREATE TABLE IF NOT EXISTS teams (
    name    TEXT PRIMARY KEY,
    group_letter TEXT
);

CREATE TABLE IF NOT EXISTS matches (
    num          INTEGER PRIMARY KEY,   -- 1..104, also referenced by W{n}/L{n}
    stage        TEXT,                  -- 'group' | 'knockout'
    round_label  TEXT,                  -- openfootball round ("Round of 32", ...)
    group_letter TEXT,                  -- 'A'..'L' for group stage, else NULL
    date         TEXT,                  -- YYYY-MM-DD
    local_time   TEXT,                  -- HH:MM at the venue
    utc_offset   INTEGER,               -- venue offset from UTC (e.g. -6)
    utc_datetime TEXT,                  -- ISO 8601 in UTC
    ground       TEXT,                  -- FK -> venues.ground
    team1_slot   TEXT,                  -- raw slot ("2A", "W74", or team name)
    team2_slot   TEXT,
    team1        TEXT,                  -- resolved team name (NULL if unknown)
    team2        TEXT,
    score1       INTEGER,
    score2       INTEGER,
    status       TEXT                   -- 'scheduled' | 'finished'
);

CREATE TABLE IF NOT EXISTS scorers (
    match_num INTEGER,                  -- FK -> matches.num the goal was scored in
    team      TEXT,                     -- team credited with the goal on the feed
    player    TEXT,                     -- scorer's name (openfootball goals feed)
    minute    TEXT,                     -- "67", "45+2", etc. (kept as text)
    penalty   INTEGER,                  -- 1 if scored from the penalty spot
    owngoal   INTEGER                   -- 1 if an own goal (NOT a Golden Boot goal)
);

CREATE TABLE IF NOT EXISTS standings (
    group_letter TEXT,
    team         TEXT,
    played       INTEGER,
    win          INTEGER,
    draw         INTEGER,
    loss         INTEGER,
    gf           INTEGER,
    ga           INTEGER,
    gd           INTEGER,
    points       INTEGER,
    rank         INTEGER,        -- 1..4 within group
    third_rank   INTEGER,        -- 1..12 across 3rd-placed teams (NULL otherwise)
    qualified    INTEGER,        -- 1 if advancing to the Round of 32
    PRIMARY KEY (group_letter, team)
);
"""


def connect():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn):
    conn.executescript(SCHEMA)
    _migrate_venue_roof(conn)
    conn.commit()


def _migrate_venue_roof(conn):
    """Add + backfill venues.roof on an already-seeded DB.

    CREATE TABLE IF NOT EXISTS never alters an existing table, so a production DB
    seeded before the open-vs-covered note shipped has no ``roof`` column. This
    runs on startup (db.init_schema) and, when the column is missing, adds it and
    backfills the static roof type from the venue table — no full reseed needed.
    Idempotent: a no-op once the column exists.
    """
    cols = [r[1] for r in conn.execute("PRAGMA table_info(venues)")]
    if "roof" in cols:
        return
    conn.execute("ALTER TABLE venues ADD COLUMN roof TEXT")
    from venues import VENUES
    for ground, v in VENUES.items():
        conn.execute("UPDATE venues SET roof=? WHERE ground=?",
                     (v.get("roof"), ground))
