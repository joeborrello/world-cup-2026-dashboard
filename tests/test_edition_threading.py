"""Editions threaded through the prediction engine (JOE-47).

The engine no longer reads module-level 2026 constants: Elo priors come from
the connection's own DB (ratings.db_priors), the bracket shape comes from the
data (round labels, "3X/Y" wildcard slots), and every per-process cache is
keyed per edition so two tournaments can be served side by side. The men's
2026 edition is still the only registered one, so all of this must be a
provable no-op for the existing site.
"""

import dataclasses
import os
import sqlite3
import time

import pytest

import alerts
import compute
import db
import editions
import live
import predict
import pundits
import ratings
import scenarios
import update_results


# ---------------------------------------------------------------- ratings

def test_db_priors_reads_the_seeded_teams_table(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    db.init_schema(conn)
    conn.execute("INSERT INTO teams (name, group_letter, elo, is_host) "
                 "VALUES ('Atlantis', 'A', 1234, 1)")
    conn.execute("INSERT INTO teams (name, group_letter, elo, is_host) "
                 "VALUES ('Utopia', 'A', 1567, 0)")
    elo, hosts = ratings.db_priors(conn)
    assert elo == {"Atlantis": 1234, "Utopia": 1567}
    assert hosts == {"Atlantis"}
    conn.close()


def test_db_priors_falls_back_to_the_mens_static_tables():
    conn = sqlite3.connect(":memory:")   # no teams table at all
    conn.row_factory = sqlite3.Row
    elo, hosts = ratings.db_priors(conn)
    assert elo == ratings.ELO
    assert hosts == set(ratings.HOSTS)
    conn.close()


def test_db_priors_backfills_null_elo_from_the_mens_table(tmp_path):
    # a pre-migration row (elo NULL) can only come from the men's 2026 DB
    conn = db.connect(str(tmp_path / "t.db"))
    db.init_schema(conn)
    conn.execute("INSERT INTO teams (name, group_letter) VALUES ('Brazil', 'C')")
    elo, _ = ratings.db_priors(conn)
    assert elo["Brazil"] == ratings.ELO["Brazil"]
    conn.close()


def test_get_rating_accepts_edition_priors():
    elo, hosts = {"Atlantis": 2000}, {"Atlantis"}
    assert ratings.get_rating("Atlantis", elo=elo, hosts=hosts) == 2000 + ratings.HOST_BONUS
    assert ratings.get_rating("Nowhere", elo=elo, hosts=hosts) == ratings.DEFAULT_ELO
    # defaults unchanged: the men's static tables
    assert ratings.get_rating("USA") == ratings.ELO["USA"] + ratings.HOST_BONUS


def test_dynamic_ratings_accepts_edition_priors():
    elo, hosts = {"Atlantis": 1800, "Utopia": 1800}, set()
    finished = [{"team1": "Atlantis", "team2": "Utopia", "score1": 2, "score2": 0}]
    rt = ratings.dynamic_ratings(finished, k=60, elo=elo, hosts=hosts)
    assert rt["Atlantis"] > 1800 > rt["Utopia"]
    assert rt["Atlantis"] - 1800 == pytest.approx(1800 - rt["Utopia"])
    # k=0 returns the given priors untouched
    assert ratings.dynamic_ratings(finished, k=0, elo=elo, hosts=hosts) == elo


# ---------------------------------------------------------------- compute

def test_count_third_place_slots_is_8_for_the_mens_bracket():
    conn = db.connect()
    try:
        assert compute.count_third_place_slots(conn) == 8
    finally:
        conn.close()


def test_count_third_place_slots_is_0_for_a_32_team_format(tmp_path):
    # scaled-down 32-team shape: every opening-round berth is filled by a
    # group's top two, so no best-third slots exist (2·2 − 2·2 = 0)
    conn = db.connect(str(tmp_path / "t.db"))
    db.init_schema(conn)
    for num, g in ((1, "A"), (2, "B")):
        conn.execute("INSERT INTO matches (num, stage, group_letter) "
                     "VALUES (?, 'group', ?)", (num, g))
    for num, s1, s2 in ((49, "1A", "2B"), (50, "1B", "2A")):
        conn.execute("INSERT INTO matches (num, stage, round_label, team1_slot, "
                     "team2_slot) VALUES (?, 'knockout', 'Round of 16', ?, ?)",
                     (num, s1, s2))
    assert compute.count_third_place_slots(conn) == 0
    conn.close()


def _third_row(group, points):
    return {"team": f"T{group}", "group_letter": group, "points": points,
            "gd": 0, "gf": 0}


def test_rank_third_place_honors_n_qualify():
    standings = {g: [_third_row(g, 9), _third_row(g, 6), _third_row(g, p)]
                 for g, p in zip("ABCD", (4, 3, 2, 1))}
    thirds = compute.rank_third_place(standings, n_qualify=0)
    assert all(not r["qualified_third"] for r in thirds)
    thirds = compute.rank_third_place(standings, n_qualify=2)
    assert [r["qualified_third"] for r in thirds] == [True, True, False, False]


# ---------------------------------------------------------------- predict

def test_round_keys_agree_with_the_mens_match_numbering():
    """The data-driven round (from round_label) must reproduce the legacy
    numbering-based round for every men's knockout match — that equivalence is
    what makes this refactor a no-op."""
    conn = db.connect()
    try:
        rows = conn.execute("SELECT num, round_label FROM matches "
                            "WHERE stage='knockout'").fetchall()
        assert rows
        for r in rows:
            assert predict.ROUND_KEYS[r["round_label"]] == predict.round_of(r["num"])
    finally:
        conn.close()


def test_prediction_cache_is_kept_per_database(tmp_path, monkeypatch):
    """Alternating requests for two editions must not evict each other."""
    calls = []
    monkeypatch.setattr(predict, "_aggregate",
                        lambda c, sims: calls.append(1) or {"n": len(calls)})
    paths = [str(tmp_path / "a.db"), str(tmp_path / "b.db")]
    conns = []
    for p in paths:
        conn = db.connect(p)
        db.init_schema(conn)
        conns.append(conn)
    predict._cache.clear()
    try:
        a1 = predict._aggregate_cached(conns[0], sims=10, seed=1)
        b1 = predict._aggregate_cached(conns[1], sims=10, seed=1)
        a2 = predict._aggregate_cached(conns[0], sims=10, seed=1)
        b2 = predict._aggregate_cached(conns[1], sims=10, seed=1)
        assert a1 is a2 and b1 is b2 and a1 is not b1
        assert len(calls) == 2          # one aggregate per DB, rest cache hits
    finally:
        predict._cache.clear()
        for conn in conns:
            conn.close()


def test_predictions_are_deterministic_for_a_given_seed():
    conn = db.connect()
    try:
        predict._cache.clear()
        p1 = predict.predictions(conn, sims=200, seed=7)
        predict._cache.clear()
        p2 = predict.predictions(conn, sims=200, seed=7)
    finally:
        predict._cache.clear()
        conn.close()
    p1.pop("generated"), p2.pop("generated")
    assert p1 == p2


# ---------------------------------------------------------------- live

def test_live_cache_is_kept_per_edition():
    live._CACHE.clear()
    try:
        now = time.time()
        live._slot("men").update(data=[{"m": 1}], ts=now, checked_at="t1")
        live._slot("women").update(data=[], ts=now, checked_at="t2")
        assert live.live_matches(None, key="men") == [{"m": 1}]
        assert live.live_matches(None, key="women") == []
        assert live.last_checked("men") == "t1"
        assert live.last_checked("women") == "t2"
    finally:
        live._CACHE.clear()


def test_live_matches_without_a_feed_url_is_an_empty_ticker():
    live._CACHE.clear()
    try:
        assert live.live_matches(None, url="", key="women") == []
        assert live.last_checked("women") is not None
    finally:
        live._CACHE.clear()


# ---------------------------------------------------------------- alerts

def test_alerts_cache_is_kept_per_edition():
    alerts._CACHE.clear()
    try:
        now = time.time()
        men_fc, women_fc = {"features": ["m"]}, {"features": ["w"]}
        alerts._CACHE["men"] = {"fc": men_fc, "ts": now}
        alerts._CACHE["women"] = {"fc": women_fc, "ts": now}
        assert alerts.active_alerts(None, key="men") is men_fc
        assert alerts.active_alerts(None, key="women") is women_fc
    finally:
        alerts._CACHE.clear()


# ---------------------------------------------------------------- pundits/scenarios

def test_pundit_briefing_is_edition_templated():
    assert "__TOURNAMENT__" in pundits.SYSTEM
    assert "2026" not in pundits.SYSTEM
    assert "__TOURNAMENT__" in scenarios.SYSTEM
    assert "2026" not in scenarios.SYSTEM


def test_whatif_scope_keeps_the_mens_historical_form():
    # men's rows keep their pre-editions cache key so existing entries stay hits
    men = scenarios._scope("What if Brazil lose?")
    assert men.startswith("whatif:")
    assert men == scenarios._scope("What if Brazil lose?", "men")
    women = scenarios._scope("What if Brazil lose?", "women")
    assert women.startswith("whatif-women:")
    assert women.split(":")[1] == men.split(":")[1]   # same question digest


# ---------------------------------------------------------------- update_results

def test_update_skips_an_edition_with_no_feeds(tmp_path, capsys):
    ghost = dataclasses.replace(
        editions.MEN, key="ghost", openfootball_url="", football_data_url="",
        db_path=str(tmp_path / "ghost.db"))
    update_results.main(prefer_remote=False, edition=ghost)
    out = capsys.readouterr().out
    assert "skipped" in out
    assert not os.path.exists(ghost.db_path)          # never even opened the DB


def test_update_from_openfootball_handles_a_missing_feed(tmp_path):
    ghost = dataclasses.replace(
        editions.MEN, key="ghost", openfootball_url="",
        openfootball_local=str(tmp_path / "nope.json"))
    conn = db.connect(str(tmp_path / "ghost.db"))
    db.init_schema(conn)
    assert update_results._update_from_openfootball(
        conn, prefer_remote=False, edition=ghost) == 0
    conn.close()
