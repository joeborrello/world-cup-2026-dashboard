"""The editions abstraction (JOE-46): edition-specific config lives on an
Edition object, and db.py / seed_data.py take an Edition instead of reading
module-level 2026 constants. The men's 2026 edition is the only registered
edition and default behavior is unchanged."""

import dataclasses
import os
import sqlite3

import pytest

import config
import data_source
import db
import editions
import ratings
import seed_data
import venues


# ---------------------------------------------------------------- registry

def test_men_is_the_only_registered_edition():
    assert set(editions.EDITIONS) == {"men"}
    assert editions.EDITIONS["men"] is editions.MEN
    assert editions.DEFAULT is editions.MEN


def test_get_falls_back_to_default_for_unknown_keys():
    assert editions.get("men") is editions.MEN
    assert editions.get("women") is editions.MEN     # not registered yet
    assert editions.get("bogus") is editions.MEN
    assert editions.get(None) is editions.MEN


def test_men_edition_carries_the_2026_constants():
    men = editions.MEN
    assert men.db_path == config.DB_PATH
    assert men.openfootball_url == config.OPENFOOTBALL_URL
    assert men.openfootball_local == config.OPENFOOTBALL_LOCAL
    assert men.football_data_url == config.FOOTBALL_DATA_URL
    assert men.start == config.TOURNAMENT_START
    assert men.end == config.TOURNAMENT_END
    assert men.venues is venues.VENUES
    assert men.elo is ratings.ELO
    assert men.elo_hosts == frozenset(ratings.HOSTS)
    assert men.url_prefix == ""                      # men's stays at the site root


def test_edition_is_immutable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        editions.MEN.db_path = "/tmp/other.db"


def test_tournament_today_returns_a_date():
    import datetime
    assert isinstance(editions.MEN.tournament_today(), datetime.date)


# ---------------------------------------------------------------- db.connect

def _db_file(conn):
    return conn.execute("PRAGMA database_list").fetchone()["file"]


def test_connect_defaults_to_the_mens_db():
    conn = db.connect()
    try:
        assert _db_file(conn) == config.DB_PATH
    finally:
        conn.close()


def test_connect_opens_the_given_edition_db(tmp_path):
    path = str(tmp_path / "other-edition.db")
    conn = db.connect(path)
    try:
        db.init_schema(conn)
        assert _db_file(conn) == path
    finally:
        conn.close()
    assert os.path.exists(path)


def test_team_priors_migration_backfills_a_preexisting_db(tmp_path):
    # a DB seeded before teams.elo/is_host shipped can only be the men's one
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE teams (name TEXT PRIMARY KEY, group_letter TEXT)")
    conn.executemany("INSERT INTO teams VALUES (?,?)",
                     [("Brazil", "C"), ("USA", "D"), ("Ruritania", "Z")])
    conn.commit()
    conn.close()

    conn = db.connect(path)
    try:
        db.init_schema(conn)
        rows = {r["name"]: r for r in conn.execute("SELECT * FROM teams")}
    finally:
        conn.close()
    assert rows["Brazil"]["elo"] == ratings.ELO["Brazil"]
    assert rows["Brazil"]["is_host"] == 0
    assert rows["USA"]["is_host"] == 1
    assert rows["Ruritania"]["elo"] == ratings.DEFAULT_ELO


# ---------------------------------------------------------------- seeding

def _edition(**overrides):
    return dataclasses.replace(editions.MEN, **overrides)


def test_seed_builds_the_edition_db_at_its_own_path(tmp_path, capsys):
    ed = _edition(key="test", db_path=str(tmp_path / "test-edition.db"))
    seed_data.seed(prefer_remote=False, edition=ed)

    conn = db.connect(ed.db_path)
    try:
        n_matches = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        n_venues = conn.execute("SELECT COUNT(*) FROM venues").fetchone()[0]
        brazil = conn.execute(
            "SELECT elo, is_host FROM teams WHERE name='Brazil'").fetchone()
        usa = conn.execute(
            "SELECT elo, is_host FROM teams WHERE name='USA'").fetchone()
    finally:
        conn.close()
    assert n_matches == 104
    assert n_venues == len(ed.venues)
    # the edition's Elo priors are seeded into the DB alongside the teams
    assert brazil["elo"] == ratings.ELO["Brazil"] and brazil["is_host"] == 0
    assert usa["is_host"] == 1
    assert "[test]" in capsys.readouterr().out


def test_seed_without_any_feed_yields_a_pre_draw_db(tmp_path, capsys):
    # an edition whose fixtures aren't published anywhere yet still seeds:
    # schema + venues + an empty schedule, and no crash downstream
    ed = _edition(key="predraw",
                  db_path=str(tmp_path / "predraw.db"),
                  openfootball_url="",
                  openfootball_local=str(tmp_path / "missing.json"))
    seed_data.seed(prefer_remote=True, edition=ed)

    conn = db.connect(ed.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM venues").fetchone()[0] \
            == len(ed.venues)
    finally:
        conn.close()
    assert "pre-draw" in capsys.readouterr().out


def test_seed_default_edition_is_unchanged_behavior(tmp_path, monkeypatch):
    # seed() with no edition argument still targets the men's 2026 DB path
    import inspect
    sig = inspect.signature(seed_data.seed)
    assert sig.parameters["edition"].default is editions.MEN


# ---------------------------------------------------------------- data feeds

def test_fetch_raw_defaults_to_the_mens_snapshot():
    raw = data_source.fetch_raw(prefer_remote=False)
    assert raw and len(data_source.normalize(raw)) == 104


def test_fetch_raw_returns_none_when_the_edition_has_no_data(tmp_path):
    raw = data_source.fetch_raw(prefer_remote=True, url="",
                                local=str(tmp_path / "nope.json"))
    assert raw is None
