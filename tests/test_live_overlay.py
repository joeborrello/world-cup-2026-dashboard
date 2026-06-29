"""Regression tests for the football-data.org live overlay (JOE-16).

The bug: `update_results._update_from_football_data` marked a match
`status='finished'` whenever the feed carried a `score.fullTime` value. But
football-data.org fills `score.fullTime` with the *live running* score while a
match is IN_PLAY/PAUSED — it is the final result only once `status == FINISHED`.
The overlay therefore settled matches mid-game, advancing the knockout bracket
prematurely (the reported symptom: South Africa shown as beating Canada before
the final whistle).

These tests pin the invariant: an in-play feed never finishes a match or feeds a
bracket; only a FINISHED feed does.
"""

import sqlite3
from unittest import mock

import compute
import config
import db
import update_results


def _conn_with_matches(rows):
    """Build an isolated in-memory DB containing just the given match rows."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_schema(conn)
    for r in rows:
        cols = ", ".join(r)
        ph = ", ".join("?" for _ in r)
        conn.execute(f"INSERT INTO matches ({cols}) VALUES ({ph})", tuple(r.values()))
    conn.commit()
    return conn


def _fixture(status, home_name, away_name, home, away,
             utc="2026-06-18T16:00:00Z"):
    return {
        "status": status,
        "utcDate": utc,
        "homeTeam": {"name": home_name},
        "awayTeam": {"name": away_name},
        "score": {"fullTime": {"home": home, "away": away}},
    }


def _run_overlay(conn, fixtures):
    with mock.patch.object(config, "FOOTBALL_DATA_API_KEY", "test-key"), \
            mock.patch.object(update_results.requests, "get") as g:
        g.return_value.json.return_value = {"matches": fixtures}
        g.return_value.raise_for_status.return_value = None
        return update_results._update_from_football_data(conn)


# Czech Republic vs South Africa, scheduled, kicking off 2026-06-18T16:00Z.
_SAFRICA_MATCH = {
    "num": 3, "stage": "group", "round_label": "Group F", "group_letter": "F",
    "utc_datetime": "2026-06-18T16:00:00+00:00",
    "team1": "Czech Republic", "team2": "South Africa",
    "score1": None, "score2": None, "status": "scheduled",
}


def test_in_play_match_is_not_finished():
    """South Africa leading mid-game must NOT settle the match."""
    conn = _conn_with_matches([_SAFRICA_MATCH])
    changed = _run_overlay(conn, [
        _fixture("IN_PLAY", "Czech Republic", "South Africa", 0, 1),
    ])
    row = conn.execute("SELECT score1, score2, status FROM matches WHERE num=3").fetchone()
    assert changed == 0
    assert row["status"] == "scheduled"
    assert row["score1"] is None and row["score2"] is None


def test_paused_match_is_not_finished():
    """Half-time (PAUSED) with a lead must not settle the match either."""
    conn = _conn_with_matches([_SAFRICA_MATCH])
    changed = _run_overlay(conn, [
        _fixture("PAUSED", "Czech Republic", "South Africa", 0, 2),
    ])
    row = conn.execute("SELECT status FROM matches WHERE num=3").fetchone()
    assert changed == 0
    assert row["status"] == "scheduled"


def test_finished_match_is_settled_and_aligned():
    """A genuinely FINISHED feed settles the match, scores aligned by name."""
    conn = _conn_with_matches([_SAFRICA_MATCH])
    # Feed lists South Africa as home, Czech Republic away — names, not position,
    # decide the mapping onto team1/team2.
    changed = _run_overlay(conn, [
        _fixture("FINISHED", "South Africa", "Czech Republic", 2, 1),
    ])
    row = conn.execute("SELECT score1, score2, status FROM matches WHERE num=3").fetchone()
    assert changed == 1
    assert row["status"] == "finished"
    # team1=Czech Republic got 1, team2=South Africa got 2.
    assert (row["score1"], row["score2"]) == (1, 2)


def test_in_play_feeder_does_not_advance_bracket():
    """A knockout feeder that is only IN_PLAY must leave its W{n} slot unresolved."""
    rows = [
        {  # Round-of-32 feeder, match 73, in play with South Africa ahead
            "num": 73, "stage": "knockout", "round_label": "Round of 32",
            "utc_datetime": "2026-06-18T16:00:00+00:00",
            "team1_slot": "Canada", "team2_slot": "South Africa",
            "team1": "Canada", "team2": "South Africa",
            "score1": None, "score2": None, "status": "scheduled",
        },
        {  # Round-of-16 match fed by the winner of 73
            "num": 89, "stage": "knockout", "round_label": "Round of 16",
            "utc_datetime": "2026-06-30T16:00:00+00:00",
            "team1_slot": "W73", "team2_slot": "W74",
            "team1": None, "team2": None,
            "score1": None, "score2": None, "status": "scheduled",
        },
    ]
    conn = _conn_with_matches(rows)
    _run_overlay(conn, [
        _fixture("IN_PLAY", "Canada", "South Africa", 0, 1),
    ])
    compute.resolve_bracket(conn, {})
    nxt = conn.execute("SELECT team1 FROM matches WHERE num=89").fetchone()
    assert nxt["team1"] is None  # winner not yet decided — slot stays empty


def test_finished_feeder_advances_bracket():
    """Once the feeder is FINISHED, the winner flows into the next round."""
    rows = [
        {
            "num": 73, "stage": "knockout", "round_label": "Round of 32",
            "utc_datetime": "2026-06-18T16:00:00+00:00",
            "team1_slot": "Canada", "team2_slot": "South Africa",
            "team1": "Canada", "team2": "South Africa",
            "score1": None, "score2": None, "status": "scheduled",
        },
        {
            "num": 89, "stage": "knockout", "round_label": "Round of 16",
            "utc_datetime": "2026-06-30T16:00:00+00:00",
            "team1_slot": "W73", "team2_slot": "W74",
            "team1": None, "team2": None,
            "score1": None, "score2": None, "status": "scheduled",
        },
    ]
    conn = _conn_with_matches(rows)
    _run_overlay(conn, [
        _fixture("FINISHED", "Canada", "South Africa", 0, 1),
    ])
    compute.resolve_bracket(conn, {})
    nxt = conn.execute("SELECT team1 FROM matches WHERE num=89").fetchone()
    assert nxt["team1"] == "South Africa"
