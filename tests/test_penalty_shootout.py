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


# ── 6. football-data is STILL queried to break a finished-but-level knockout ──
# openfootball publishes ft/et minutes-to-hours before it back-fills the shootout
# (p), so a knockout can sit `finished` and level (1-1) with no winner. The overlay
# must keep consulting football-data for that shootout winner instead of treating
# the match as untouchable the instant openfootball finishes it (JOE-16 revision).

def _finished_level_ger_par():
    """Germany-Paraguay as openfootball leaves it when `p` hasn't landed yet:
    finished, 1-1, no penalties — i.e. no winner, bracket stalled."""
    s = dict(_GER_PAR)
    s.update(pen1=None, pen2=None, status="finished")  # score1==score2==1
    return s


def test_undecided_knockout_predicate():
    assert update_results._undecided_knockout(_finished_level_ger_par()) is True
    # already has penalties -> decided
    assert update_results._undecided_knockout(dict(_GER_PAR)) is False
    # decided on the pitch (2-1) -> not undecided
    decided = dict(_GER_PAR); decided.update(score1=2, score2=1, pen1=None, pen2=None)
    assert update_results._undecided_knockout(decided) is False


def test_overlay_backfills_winner_onto_finished_level_knockout():
    # The core revision case: openfootball already marked it finished 1-1 with no
    # shootout; football-data names Paraguay (AWAY) as the penalty winner. The
    # overlay must break the tie so Paraguay — not Germany — advances.
    conn = _conn_with([_finished_level_ger_par()])
    changed = _run_overlay(conn, [{
        "status": "FINISHED",
        "utcDate": "2026-06-29T16:00:00Z",
        "homeTeam": {"name": "Germany"},
        "awayTeam": {"name": "Paraguay"},
        "score": {"winner": "AWAY_TEAM", "fullTime": {"home": 1, "away": 1}},
    }])
    row = conn.execute(
        "SELECT score1, score2, pen1, pen2, status FROM matches WHERE num=74").fetchone()
    assert changed == 1
    assert (row["score1"], row["score2"]) == (1, 1)   # scoreline untouched
    assert row["status"] == "finished"
    assert compute.winner_side(
        row["score1"], row["score2"], row["pen1"], row["pen2"]) == 2   # Paraguay


def test_overlay_uses_real_penalty_breakdown_to_break_a_finished_tie():
    conn = _conn_with([_finished_level_ger_par()])
    changed = _run_overlay(conn, [{
        "status": "FINISHED",
        "utcDate": "2026-06-29T16:00:00Z",
        "homeTeam": {"name": "Germany"},
        "awayTeam": {"name": "Paraguay"},
        "score": {"fullTime": {"home": 1, "away": 1},
                  "penalties": {"home": 3, "away": 4}},
    }])
    row = conn.execute(
        "SELECT pen1, pen2 FROM matches WHERE num=74").fetchone()
    assert changed == 1
    assert (row["pen1"], row["pen2"]) == (3, 4)


def test_overlay_never_overwrites_a_knockout_decided_on_the_pitch():
    # openfootball settled it 2-1 (decisive). football-data must not touch it even
    # if its own (stale/in-play) view momentarily disagrees.
    decided = dict(_GER_PAR)
    decided.update(score1=2, score2=1, pen1=None, pen2=None, status="finished")
    conn = _conn_with([decided])
    changed = _run_overlay(conn, [{
        "status": "FINISHED",
        "utcDate": "2026-06-29T16:00:00Z",
        "homeTeam": {"name": "Germany"},
        "awayTeam": {"name": "Paraguay"},
        "score": {"winner": "AWAY_TEAM", "fullTime": {"home": 1, "away": 1}},
    }])
    row = conn.execute(
        "SELECT score1, score2, pen1, pen2 FROM matches WHERE num=74").fetchone()
    assert changed == 0
    assert (row["score1"], row["score2"], row["pen1"], row["pen2"]) == (2, 1, None, None)


def test_overlay_does_not_re_touch_a_finished_shootout_already_settled():
    # Already has penalties (from openfootball) -> idempotent no-op, even though
    # football-data reports the same match as FINISHED.
    conn = _conn_with([dict(_GER_PAR)])   # 1-1, pens 3-4 already
    changed = _run_overlay(conn, [{
        "status": "FINISHED",
        "utcDate": "2026-06-29T16:00:00Z",
        "homeTeam": {"name": "Germany"},
        "awayTeam": {"name": "Paraguay"},
        "score": {"winner": "AWAY_TEAM", "fullTime": {"home": 1, "away": 1}},
    }])
    assert changed == 0


def test_overlay_skips_backfill_when_level_scores_disagree():
    # openfootball says 1-1; football-data's final is 0-0 — the feeds disagree on
    # the scoreline, so we don't trust its winner either. Leave the tie unbroken.
    conn = _conn_with([_finished_level_ger_par()])
    changed = _run_overlay(conn, [{
        "status": "FINISHED",
        "utcDate": "2026-06-29T16:00:00Z",
        "homeTeam": {"name": "Germany"},
        "awayTeam": {"name": "Paraguay"},
        "score": {"winner": "AWAY_TEAM", "fullTime": {"home": 0, "away": 0}},
    }])
    row = conn.execute(
        "SELECT pen1, pen2 FROM matches WHERE num=74").fetchone()
    assert changed == 0
    assert (row["pen1"], row["pen2"]) == (None, None)


def test_overlay_does_not_backfill_a_finished_group_draw():
    # A finished, level GROUP match is a genuine draw — never invent a shootout.
    grp = {
        "num": 19, "stage": "group", "round_label": "Matchday 2",
        "group_letter": "B", "utc_datetime": "2026-06-20T16:00:00+00:00",
        "team1_slot": "USA", "team2_slot": "Paraguay",
        "team1": "USA", "team2": "Paraguay",
        "score1": 1, "score2": 1, "pen1": None, "pen2": None,
        "status": "finished",
    }
    conn = _conn_with([grp])
    changed = _run_overlay(conn, [{
        "status": "FINISHED",
        "utcDate": "2026-06-20T16:00:00Z",
        "homeTeam": {"name": "USA"},
        "awayTeam": {"name": "Paraguay"},
        "score": {"winner": "AWAY_TEAM", "fullTime": {"home": 1, "away": 1}},
    }])
    row = conn.execute(
        "SELECT pen1, pen2, status FROM matches WHERE num=19").fetchone()
    assert changed == 0
    assert (row["pen1"], row["pen2"]) == (None, None)


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


# ── 7. the projection cache busts when a result is corrected IN PLACE ─────────
# Why the bug *persisted* after the parser was fixed: openfootball publishes the
# extra-time score (finished, level) minutes-to-hours before it back-fills the
# shootout. The projection caches that level result (Germany favored to advance),
# then `p` lands and `pen1/pen2` are written IN PLACE — the count of finished
# matches never changes. A cache keyed only on that COUNT kept a long-running
# gunicorn serving the stale projected bracket (Germany still shown advancing past
# Paraguay) until the next restart. The key must fold in the actual results.

def _ger_par_level():
    """num 74 as openfootball leaves it pre-shootout: finished, 1-1, no pens."""
    s = dict(_GER_PAR)
    s.update(pen1=None, pen2=None)   # score1==score2==1, status finished
    return s


def test_projection_cache_busts_when_a_shootout_is_backfilled_in_place(monkeypatch):
    conn = _conn_with([_ger_par_level(), dict(_NEXT)])

    def n_finished():
        return conn.execute(
            "SELECT COUNT(*) FROM matches WHERE status='finished'").fetchone()[0]

    # Stub the heavy Monte-Carlo aggregate so we can simply count recomputes; this
    # test is about the cache KEY, not the simulation itself.
    calls = {"n": 0}
    monkeypatch.setattr(
        predict, "_aggregate",
        lambda c, sims: (calls.__setitem__("n", calls["n"] + 1) or {"agg": calls["n"]}))
    predict._cache.update(key=None, agg=None)
    try:
        before = n_finished()
        a1 = predict._aggregate_cached(conn, sims=50, seed=1)
        a2 = predict._aggregate_cached(conn, sims=50, seed=1)
        assert calls["n"] == 1 and a1 is a2     # unchanged results -> served cached

        # Back-fill the shootout in place; the finished COUNT is identical, so a
        # count-only key would NOT bust here — that was the bug.
        conn.execute("UPDATE matches SET pen1=3, pen2=4 WHERE num=74")
        conn.commit()
        assert n_finished() == before

        predict._aggregate_cached(conn, sims=50, seed=1)
        assert calls["n"] == 2                  # result changed -> cache recomputed
    finally:
        predict._cache.update(key=None, agg=None)


# ── 8. the PROJECTED bracket never advances the Elo favorite of a finished tie ─
# The bug the reviewer kept seeing: openfootball finishes Germany-Paraguay level
# (1-1) and back-fills the shootout (p) minutes-to-hours later. In that window the
# match is `finished` but undecided, and the projected/interactive bracket fell
# through to "advance the Elo favorite" — Germany — exactly the premature update.
# A finished-but-level knockout must advance NOBODY until a real result lands.

def _project_with(ko, R):
    from collections import Counter
    agg = {
        "_rank_counts": {}, "teams": {}, "_R": R,
        "_slot_counts": {m["num"]: {"team1": Counter(), "team2": Counter()}
                         for m in ko},
        "_third_slots": [], "_ko": ko, "_group_teams": {}, "sims": 100,
    }
    return predict._project(agg, {})


_KO_LEVEL = [
    {"num": 74, "slot1": "Germany", "slot2": "Paraguay", "status": "finished",
     "team1": "Germany", "team2": "Paraguay",
     "score1": 1, "score2": 1, "pen1": None, "pen2": None},
    {"num": 89, "slot1": "W74", "slot2": "W77", "status": "scheduled",
     "team1": None, "team2": None,
     "score1": None, "score2": None, "pen1": None, "pen2": None},
]
# Germany is the heavy Elo favorite — so a careless projection would advance it.
_PROJ_R = {"Germany": 1900, "Paraguay": 1700}


def test_project_does_not_advance_elo_favorite_for_finished_level_knockout():
    slots, _ = _project_with([dict(m) for m in _KO_LEVEL], _PROJ_R)
    # both teams are still shown in the match…
    assert slots[74]["team1"]["team"] == "Germany"
    assert slots[74]["team2"]["team"] == "Paraguay"
    assert slots[74]["locked"] is False        # not settled -> still interactive
    # …but NEITHER is advanced: the W74 feeder in the next round stays empty.
    assert slots[89]["team1"] is None


def test_project_advances_shootout_winner_once_penalties_land():
    ko = [dict(m) for m in _KO_LEVEL]
    ko[0].update(pen1=3, pen2=4)               # Paraguay won the shootout 4-3
    slots, _ = _project_with(ko, _PROJ_R)
    assert slots[74]["locked"] is True         # decisively settled now
    # W74 resolves to Paraguay (the shootout winner), NOT the favorite Germany.
    assert slots[89]["team1"]["team"] == "Paraguay"


def test_projection_cache_holds_when_nothing_changed(monkeypatch):
    # The flip side: an unchanged result set must stay cached (no needless resims).
    conn = _conn_with([dict(_GER_PAR), dict(_NEXT)])
    calls = {"n": 0}
    monkeypatch.setattr(
        predict, "_aggregate",
        lambda c, sims: (calls.__setitem__("n", calls["n"] + 1) or {"agg": calls["n"]}))
    predict._cache.update(key=None, agg=None)
    try:
        predict._aggregate_cached(conn, sims=50, seed=1)
        predict._aggregate_cached(conn, sims=50, seed=1)
        predict._aggregate_cached(conn, sims=50, seed=1)
        assert calls["n"] == 1
    finally:
        predict._cache.update(key=None, agg=None)
