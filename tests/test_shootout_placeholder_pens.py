"""Regression tests for the Australia-Egypt bracket stall (JOE-38).

The reported bug: the winner of Round-of-32 match 88 (Australia-Egypt) never
advanced, so its Round-of-16 feeder (num 95, W86 vs W88) and the quarter-final
behind it (num 100, W95 vs W96) showed holes instead of two projected teams.

Root cause was in the football-data overlay, mid-backfill. While the feed was
still assembling the final it published

    status FINISHED, winner null, duration PENALTY_SHOOTOUT,
    fullTime 3-5 (INCLUDING shootout goals), regularTime 1-1, extraTime 0-0,
    penalties {home: 0, away: 0}   # placeholder — later 4-4, equally bogus

and `_decide_football_data` trusted the level penalties breakdown verbatim,
storing the match as finished 1-1 pens 0-0. A level shootout is impossible, so
`winner_side` was None and nothing advanced — and because `_undecided_knockout`
required pen1/pen2 to be NULL, the 0-0 placeholder made the row look settled:
the updater never consulted football-data again and the stall was permanent.

These tests pin the fixes: a level penalties breakdown is never a result, the
shootout can be recovered from the fullTime surplus, an undecided knockout is
anything `winner_side` can't decide, and the whole chain un-stalls a poisoned
row end-to-end.
"""

import sqlite3
from unittest import mock

import compute
import config
import data_source
import db
import predict
import update_results


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


_AUS_EGY = {
    "num": 88, "stage": "knockout", "round_label": "Round of 32",
    "utc_datetime": "2026-07-03T18:00:00+00:00",
    # literal team slots (as in the JOE-16 fixtures): the synthetic DB has no
    # group matches, so "2D"/"2G" slots would resolve to None and be cleared
    "team1_slot": "Australia", "team2_slot": "Egypt",
    "team1": "Australia", "team2": "Egypt",
    "score1": None, "score2": None, "pen1": None, "pen2": None,
    "status": "scheduled",
}
_R16 = {
    "num": 95, "stage": "knockout", "round_label": "Round of 16",
    "utc_datetime": "2026-07-06T18:00:00+00:00",
    "team1_slot": "W86", "team2_slot": "W88",
    "team1": None, "team2": None,
    "score1": None, "score2": None, "status": "scheduled",
}
_QF = {
    "num": 100, "stage": "knockout", "round_label": "Quarter-final",
    "utc_datetime": "2026-07-10T18:00:00+00:00",
    "team1_slot": "W95", "team2_slot": "W96",
    "team1": None, "team2": None,
    "score1": None, "score2": None, "status": "scheduled",
}

# football-data's actual mid-backfill payload for Australia-Egypt: FINISHED,
# no winner named, fullTime inflated by the shootout, penalties a level 4-4.
_FD_MID_BACKFILL = {
    "status": "FINISHED",
    "utcDate": "2026-07-03T18:00:00Z",
    "homeTeam": {"name": "Australia"},
    "awayTeam": {"name": "Egypt"},
    "score": {
        "winner": None,
        "duration": "PENALTY_SHOOTOUT",
        "fullTime": {"home": 3, "away": 5},
        "halfTime": {"home": 0, "away": 1},
        "regularTime": {"home": 1, "away": 1},
        "extraTime": {"home": 0, "away": 0},
        "penalties": {"home": 4, "away": 4},
    },
}


def _run_overlay(conn, fixtures):
    with mock.patch.object(config, "FOOTBALL_DATA_API_KEY", "test-key"), \
            mock.patch.object(update_results.requests, "get") as g:
        g.return_value.json.return_value = {"matches": fixtures}
        g.return_value.raise_for_status.return_value = None
        return update_results._update_from_football_data(conn)


# ── 1. a level penalties breakdown is a placeholder, not a result ────────────

def test_decide_ignores_level_penalties_placeholder():
    # The original poisoning write: FINISHED, fullTime 1-1, penalties 0-0, no
    # winner. Nothing decisive here — must return None, not (1, 1, 0, 0).
    assert update_results._decide_football_data(
        {"fullTime": {"home": 1, "away": 1}, "penalties": {"home": 0, "away": 0}},
        "Australia", "Egypt", "Australia", "Egypt", is_knockout=True) is None


def test_decide_prefers_named_winner_over_level_penalties():
    # Same placeholder pens, but the winner IS named: use it (minimal 0-1).
    decided = update_results._decide_football_data(
        {"winner": "AWAY_TEAM", "fullTime": {"home": 1, "away": 1},
         "penalties": {"home": 0, "away": 0}},
        "Australia", "Egypt", "Australia", "Egypt", is_knockout=True)
    assert decided == (1, 1, 0, 1)
    assert compute.winner_side(*decided) == 2


def test_overlay_does_not_write_placeholder_pens():
    conn = _conn_with([dict(_AUS_EGY)])
    changed = _run_overlay(conn, [{
        "status": "FINISHED",
        "utcDate": "2026-07-03T18:00:00Z",
        "homeTeam": {"name": "Australia"},
        "awayTeam": {"name": "Egypt"},
        "score": {"winner": None,
                  "fullTime": {"home": 1, "away": 1},
                  "penalties": {"home": 0, "away": 0}},
    }])
    row = conn.execute("SELECT status, pen1, pen2 FROM matches WHERE num=88").fetchone()
    assert changed == 0
    assert row["status"] == "scheduled"          # never marked finished-undecided
    assert (row["pen1"], row["pen2"]) == (None, None)


# ── 2. a shootout's fullTime includes the shootout goals ─────────────────────

def test_decide_recovers_shootout_from_fulltime_surplus():
    # winner null, penalties level: the only decisive signal is fullTime 3-5 on
    # a match that stood 1-1 after extra time -> shootout 2-4, Egypt through.
    decided = update_results._decide_football_data(
        _FD_MID_BACKFILL["score"],
        "Australia", "Egypt", "Australia", "Egypt", is_knockout=True)
    assert decided == (1, 1, 2, 4)               # match score 1-1, NOT 3-5
    assert compute.winner_side(*decided) == 2    # Egypt


def test_decide_skips_unlevel_fulltime_shootout_without_breakdown():
    # PENALTY_SHOOTOUT with an unlevel fullTime but no regular/extra breakdown:
    # the 3-5 can't be split into match score + shootout — skip, don't store.
    assert update_results._decide_football_data(
        {"winner": None, "duration": "PENALTY_SHOOTOUT",
         "fullTime": {"home": 3, "away": 5}},
        "Australia", "Egypt", "Australia", "Egypt", is_knockout=True) is None


def test_overlay_fresh_write_stores_match_score_not_inflated_fulltime():
    # A scheduled row receiving the mid-backfill payload directly must store the
    # real 1-1 (pens 2-4), never fullTime's shootout-inflated 3-5.
    conn = _conn_with([dict(_AUS_EGY)])
    changed = _run_overlay(conn, [dict(_FD_MID_BACKFILL)])
    row = conn.execute(
        "SELECT score1, score2, pen1, pen2, status FROM matches WHERE num=88").fetchone()
    assert changed == 1
    assert (row["score1"], row["score2"]) == (1, 1)
    assert compute.winner_side(
        row["score1"], row["score2"], row["pen1"], row["pen2"]) == 2


# ── 3. placeholder pens no longer make the row look settled ──────────────────

def test_undecided_knockout_with_placeholder_pens():
    poisoned = dict(_AUS_EGY)
    poisoned.update(score1=1, score2=1, pen1=0, pen2=0, status="finished")
    assert update_results._undecided_knockout(poisoned) is True
    # sanity: the JOE-16 cases still hold
    level = dict(poisoned); level.update(pen1=None, pen2=None)
    assert update_results._undecided_knockout(level) is True
    settled = dict(poisoned); settled.update(pen1=2, pen2=4)
    assert update_results._undecided_knockout(settled) is False


def test_overlay_backfills_over_placeholder_pens_and_bracket_advances():
    # The production repair path: the DB already holds the poisoned finished
    # 1-1 pens 0-0 row. The next updater run must re-consult football-data,
    # break the tie from the fullTime surplus, and the bracket must advance
    # Egypt into the W88 feeder (num 95) that leads to quarter-final 100.
    poisoned = dict(_AUS_EGY)
    poisoned.update(score1=1, score2=1, pen1=0, pen2=0, status="finished")
    conn = _conn_with([poisoned, dict(_R16), dict(_QF)])

    changed = _run_overlay(conn, [dict(_FD_MID_BACKFILL)])
    row = conn.execute(
        "SELECT score1, score2, pen1, pen2 FROM matches WHERE num=88").fetchone()
    assert changed == 1
    assert (row["score1"], row["score2"]) == (1, 1)   # scoreline untouched
    assert compute.winner_side(
        row["score1"], row["score2"], row["pen1"], row["pen2"]) == 2

    compute.recompute_all(conn)
    r16 = conn.execute("SELECT team2 FROM matches WHERE num=95").fetchone()
    assert r16["team2"] == "Egypt"                    # W88 resolved


def test_overlay_leaves_placeholder_row_alone_until_feed_is_decisive():
    # If football-data is STILL not decisive (level pens, no winner, fullTime
    # not yet inflated), keep the row untouched and keep polling — never guess.
    poisoned = dict(_AUS_EGY)
    poisoned.update(score1=1, score2=1, pen1=0, pen2=0, status="finished")
    conn = _conn_with([poisoned])
    changed = _run_overlay(conn, [{
        "status": "FINISHED",
        "utcDate": "2026-07-03T18:00:00Z",
        "homeTeam": {"name": "Australia"},
        "awayTeam": {"name": "Egypt"},
        "score": {"winner": None, "duration": "PENALTY_SHOOTOUT",
                  "fullTime": {"home": 1, "away": 1},
                  "regularTime": {"home": 1, "away": 1},
                  "extraTime": {"home": 0, "away": 0},
                  "penalties": {"home": 0, "away": 0}},
    }])
    row = conn.execute("SELECT pen1, pen2 FROM matches WHERE num=88").fetchone()
    assert changed == 0
    assert (row["pen1"], row["pen2"]) == (0, 0)       # unchanged, still polled


# ── 4. openfootball's parser also refuses a level shootout ───────────────────

def _seed_snapshot_conn():
    """In-memory DB seeded from the committed offline snapshot (every group
    match finished, the whole knockout still scheduled), fully derived — the
    same state conftest/seed_data build."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_schema(conn)
    for m in data_source.normalize(data_source.fetch_raw(prefer_remote=False)):
        conn.execute(
            """INSERT INTO matches
               (num, stage, round_label, group_letter, date, local_time,
                utc_offset, utc_datetime, ground, team1_slot, team2_slot,
                team1, team2, score1, score2, pen1, pen2, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (m["num"], m["stage"], m["round_label"], m["group_letter"],
             m["date"], m["local_time"], m["utc_offset"], m["utc_datetime"],
             m["ground"], m["team1_slot"], m["team2_slot"], m["team1"],
             m["team2"], m["score1"], m["score2"], m["pen1"], m["pen2"],
             m["status"]),
        )
    conn.commit()
    compute.recompute_all(conn)
    return conn


def test_projected_bracket_fills_95_and_100_once_shootout_is_decisive():
    # The reported symptom, verbatim: with match 88 poisoned (finished 1-1,
    # placeholder pens 0-0) the projected bracket shows a hole for W88 in the
    # Round-of-16 match 95 and for W95 in quarter-final 100 — correctly, since
    # a finished match's winner is never fabricated. Once the pens are repaired
    # to the real 2-4 (what the fixed updater now writes), both match-ups must
    # display two projected teams again, Egypt among them.
    conn = _seed_snapshot_conn()
    conn.execute("UPDATE matches SET score1=1, score2=1, pen1=0, pen2=0, "
                 "status='finished' WHERE num=88")
    conn.commit()
    compute.recompute_all(conn)
    pb = predict.projected_bracket(conn, sims=300, seed=7)
    assert pb["slots"][95]["team2"] is None       # W88 unresolvable
    assert pb["slots"][95]["team1"] is not None   # W86 still projected
    assert pb["slots"][100]["team1"] is None      # W95 blocked behind it
    assert pb["slots"][100]["team2"] is not None  # W96 unaffected

    conn.execute("UPDATE matches SET pen1=2, pen2=4 WHERE num=88")
    conn.commit()
    compute.recompute_all(conn)
    pb = predict.projected_bracket(conn, sims=300, seed=7)
    assert pb["slots"][95]["team2"]["team"] == "Egypt"
    assert pb["slots"][95]["team1"] is not None
    assert pb["slots"][100]["team1"] is not None  # two projected teams again
    assert pb["slots"][100]["team2"] is not None


def test_normalize_drops_level_penalty_placeholder():
    raw = {"matches": [{
        "round": "Round of 32", "group": None, "date": "2026-07-03",
        "time": "13:00 UTC-5", "ground": "any",
        "team1": "Australia", "team2": "Egypt",
        "score": {"ft": [1, 1], "et": [1, 1], "p": [0, 0]},
    }]}
    m = data_source.normalize(raw)[0]
    assert (m["score1"], m["score2"]) == (1, 1)
    assert (m["pen1"], m["pen2"]) == (None, None)     # placeholder discarded
    # a decisive shootout is still read verbatim (JOE-16 unchanged)
    raw["matches"][0]["score"]["p"] = [3, 4]
    m = data_source.normalize(raw)[0]
    assert (m["pen1"], m["pen2"]) == (3, 4)
