"""Regression tests for penalty-shootout knockout results (JOE-16, revision).

The reported bug: the Germany-Paraguay Round-of-32 match was shown with the wrong
result. openfootball records that match as level after extra time and decided by
a shootout:

    {"ht": [0, 1], "ft": [1, 1], "et": [1, 1], "p": [3, 4]}  # Paraguay won 4-3

But `data_source.normalize` only read `ft`, so the match was stored as a 1-1 draw
and `compute.resolve_bracket` (which picked the winner with `score1 >= score2`)
silently advanced *Germany*. The fix parses the extra-time/penalty fields from
every data source and decides a level knockout by the shootout.

These tests pin the whole chain: parsing, the winner helper, bracket resolution,
the prediction engine, and the football-data overlay.
"""

import sqlite3
from unittest import mock

import compute
import config
import data_source
import db
import predict
import update_results


# ── 1. the feed parser keeps the shootout ────────────────────────────────────

def _normalize_one(score, round_label="Round of 32", stage_group=None):
    raw = {"matches": [{
        "round": round_label,
        "group": stage_group,
        "date": "2026-06-29",
        "time": "16:00 UTC+0",
        "ground": "any",
        "team1": "Germany",
        "team2": "Paraguay",
        "score": score,
    }]}
    return data_source.normalize(raw)[0]


def test_normalize_reads_extra_time_and_penalties():
    m = _normalize_one({"ht": [0, 1], "ft": [1, 1], "et": [1, 1], "p": [3, 4]})
    # the standing score is the (extra-time) 1-1; the shootout is preserved
    assert (m["score1"], m["score2"]) == (1, 1)
    assert (m["pen1"], m["pen2"]) == (3, 4)
    assert m["status"] == "finished"


def test_normalize_prefers_extra_time_score_over_full_time():
    # a goal in extra time: ft 1-1, et 2-1, no shootout
    m = _normalize_one({"ft": [1, 1], "et": [2, 1]})
    assert (m["score1"], m["score2"]) == (2, 1)
    assert (m["pen1"], m["pen2"]) == (None, None)


def test_normalize_plain_full_time_has_no_penalties():
    m = _normalize_one({"ht": [0, 0], "ft": [2, 0]})
    assert (m["score1"], m["score2"]) == (2, 0)
    assert (m["pen1"], m["pen2"]) == (None, None)


# ── 2. the winner helper ─────────────────────────────────────────────────────

def test_winner_side_uses_penalties_to_break_a_tie():
    assert compute.winner_side(1, 1, 3, 4) == 2     # Paraguay
    assert compute.winner_side(1, 1, 5, 4) == 1
    assert compute.winner_side(2, 1) == 1           # decided in normal time
    assert compute.winner_side(1, 3) == 2


def test_winner_side_returns_none_for_an_unbroken_draw():
    assert compute.winner_side(1, 1) is None        # group draw, no shootout
    assert compute.winner_side(1, 1, 3, 3) is None  # malformed (no winner)
    assert compute.winner_side(None, None) is None


# ── 3. bracket resolution advances the shootout winner ───────────────────────

def _conn_with(rows):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_schema(conn)
    for r in rows:
        cols = ", ".join(r)
        ph = ", ".join("?" for _ in r)
        conn.execute(f"INSERT INTO matches ({cols}) VALUES ({ph})", tuple(r.values()))
    conn.commit()
    return conn


_GER_PAR = {
    "num": 74, "stage": "knockout", "round_label": "Round of 32",
    "utc_datetime": "2026-06-29T16:00:00+00:00",
    "team1_slot": "Germany", "team2_slot": "Paraguay",
    "team1": "Germany", "team2": "Paraguay",
    "score1": 1, "score2": 1, "pen1": 3, "pen2": 4, "status": "finished",
}
_NEXT = {
    "num": 89, "stage": "knockout", "round_label": "Round of 16",
    "utc_datetime": "2026-07-04T16:00:00+00:00",
    "team1_slot": "W74", "team2_slot": "W77",
    "team1": None, "team2": None,
    "score1": None, "score2": None, "status": "scheduled",
}


def test_shootout_winner_advances_not_the_first_listed_team():
    conn = _conn_with([dict(_GER_PAR), dict(_NEXT)])
    compute.resolve_bracket(conn, {})
    nxt = conn.execute("SELECT team1 FROM matches WHERE num=89").fetchone()
    assert nxt["team1"] == "Paraguay"   # NOT Germany, despite score1 == score2


def test_shootout_loser_flows_to_loser_slot():
    third = dict(_NEXT)
    third.update(num=90, team1_slot="L74", team1=None)
    conn = _conn_with([dict(_GER_PAR), third])
    compute.resolve_bracket(conn, {})
    row = conn.execute("SELECT team1 FROM matches WHERE num=90").fetchone()
    assert row["team1"] == "Germany"    # the team that lost the shootout


# ── 4. prediction engine treats a finished shootout as settled ───────────────

def test_predict_locks_and_advances_the_shootout_winner():
    rows = [dict(_GER_PAR), dict(_NEXT)]
    # the projection needs ratings for any *unplayed* sims; a finished match must
    # be read straight from the result, not simulated.
    conn = _conn_with(rows)
    w, l = predict._finished_decision({
        "status": "finished", "score1": 1, "score2": 1, "pen1": 3, "pen2": 4,
        "team1": "Germany", "team2": "Paraguay",
    })
    assert (w, l) == ("Paraguay", "Germany")


def test_predict_finished_decision_none_when_undecided():
    # finished knockout level with no shootout in the feed yet -> not yet decided
    assert predict._finished_decision({
        "status": "finished", "score1": 1, "score2": 1, "pen1": None, "pen2": None,
        "team1": "Germany", "team2": "Paraguay",
    }) is None


# ── 5. football-data overlay also captures the shootout ──────────────────────

def _run_overlay(conn, fixtures):
    with mock.patch.object(config, "FOOTBALL_DATA_API_KEY", "test-key"), \
            mock.patch.object(update_results.requests, "get") as g:
        g.return_value.json.return_value = {"matches": fixtures}
        g.return_value.raise_for_status.return_value = None
        return update_results._update_from_football_data(conn)


def _scheduled_ger_par():
    s = dict(_GER_PAR)
    s.update(score1=None, score2=None, pen1=None, pen2=None, status="scheduled")
    return s


def test_overlay_records_penalties_for_a_finished_shootout():
    conn = _conn_with([_scheduled_ger_par()])
    changed = _run_overlay(conn, [{
        "status": "FINISHED",
        "utcDate": "2026-06-29T16:00:00Z",
        "homeTeam": {"name": "Germany"},
        "awayTeam": {"name": "Paraguay"},
        "score": {
            "fullTime": {"home": 1, "away": 1},
            "penalties": {"home": 3, "away": 4},
        },
    }])
    row = conn.execute(
        "SELECT score1, score2, pen1, pen2, status FROM matches WHERE num=74").fetchone()
    assert changed == 1
    assert (row["score1"], row["score2"]) == (1, 1)
    assert (row["pen1"], row["pen2"]) == (3, 4)
    assert compute.winner_side(*[row[k] for k in ("score1", "score2", "pen1", "pen2")]) == 2


def test_overlay_uses_winner_field_when_no_penalty_breakdown():
    # football-data's v4 match list carries NO penalties breakdown — a shootout is
    # a level fullTime with the winner named only in score.winner. The overlay must
    # read that field and advance Paraguay, not stall on a 1-1 "draw".
    conn = _conn_with([_scheduled_ger_par()])
    changed = _run_overlay(conn, [{
        "status": "FINISHED",
        "utcDate": "2026-06-29T16:00:00Z",
        "homeTeam": {"name": "Germany"},
        "awayTeam": {"name": "Paraguay"},
        "score": {
            "winner": "AWAY_TEAM",
            "duration": "PENALTY_SHOOTOUT",
            "fullTime": {"home": 1, "away": 1},
        },
    }])
    row = conn.execute(
        "SELECT score1, score2, pen1, pen2, status FROM matches WHERE num=74").fetchone()
    assert changed == 1
    assert (row["score1"], row["score2"]) == (1, 1)
    # Paraguay (team2) is encoded as the shootout winner so the bracket advances it.
    assert compute.winner_side(
        row["score1"], row["score2"], row["pen1"], row["pen2"]) == 2


def test_overlay_skips_level_knockout_with_no_winner_named():
    # FINISHED, level, but no winner field yet (feed mid-update) -> not settled.
    conn = _conn_with([_scheduled_ger_par()])
    changed = _run_overlay(conn, [{
        "status": "FINISHED",
        "utcDate": "2026-06-29T16:00:00Z",
        "homeTeam": {"name": "Germany"},
        "awayTeam": {"name": "Paraguay"},
        "score": {"fullTime": {"home": 1, "away": 1}},
    }])
    row = conn.execute("SELECT status FROM matches WHERE num=74").fetchone()
    assert changed == 0
    assert row["status"] == "scheduled"


def test_overlay_skips_when_winner_field_contradicts_aligned_score():
    # If football-data's winner disagrees with the aligned scoreline, our team
    # name match is wrong — skip rather than record a wrong result.
    conn = _conn_with([_scheduled_ger_par()])
    changed = _run_overlay(conn, [{
        "status": "FINISHED",
        "utcDate": "2026-06-29T16:00:00Z",
        "homeTeam": {"name": "Germany"},
        "awayTeam": {"name": "Paraguay"},
        # Germany 2-1 on the board, yet the feed says the AWAY team won: incoherent.
        "score": {"winner": "AWAY_TEAM", "fullTime": {"home": 2, "away": 1}},
    }])
    row = conn.execute("SELECT status FROM matches WHERE num=74").fetchone()
    assert changed == 0
    assert row["status"] == "scheduled"


def test_overlay_group_draw_stays_a_draw_with_no_penalties():
    # A level group match is a genuine draw — the knockout-only shootout encoding
    # must not kick in and invent a winner.
    grp = {
        "num": 19, "stage": "group", "round_label": "Matchday 2",
        "group_letter": "B", "utc_datetime": "2026-06-20T16:00:00+00:00",
        "team1_slot": "USA", "team2_slot": "Paraguay",
        "team1": "USA", "team2": "Paraguay",
        "score1": None, "score2": None, "pen1": None, "pen2": None,
        "status": "scheduled",
    }
    conn = _conn_with([grp])
    changed = _run_overlay(conn, [{
        "status": "FINISHED",
        "utcDate": "2026-06-20T16:00:00Z",
        "homeTeam": {"name": "USA"},
        "awayTeam": {"name": "Paraguay"},
        "score": {"winner": "DRAW", "fullTime": {"home": 1, "away": 1}},
    }])
    row = conn.execute(
        "SELECT score1, score2, pen1, pen2, status FROM matches WHERE num=19").fetchone()
    assert changed == 1
    assert (row["score1"], row["score2"]) == (1, 1)
    assert (row["pen1"], row["pen2"]) == (None, None)
    assert row["status"] == "finished"
