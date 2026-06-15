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
    tz       TEXT
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
    conn.commit()
