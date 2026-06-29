"""Tests for the Golden Boot tracker (JOE-13).

Cover the whole feature so it can't silently regress:

  * parsing scorers out of the openfootball goals feed (penalty / own-goal flags,
    team attribution),
  * the leaderboard rules — penalties count, own goals don't, ranking + tiebreak,
  * the remaining-goals projection (engine tie-in, shrinkage, the arithmetic),
  * the API + page plumbing and the front-end wiring (template / CSS / JS).
"""

import os
import sqlite3

import pytest

import app as flask_app
import data_source
import db
import goldenboot

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)

# Small, seeded sims keep the projection deterministic and fast for tests.
SIMS = 200
SEED = 1


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as fh:
        return fh.read()


@pytest.fixture
def conn():
    c = db.connect()
    yield c
    c.close()


@pytest.fixture
def client():
    flask_app.app.config['TESTING'] = True
    with flask_app.app.test_client() as c:
        yield c


def _mem():
    """A fresh in-memory DB with the schema, for hermetic unit tests."""
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    db.init_schema(c)
    return c


def _add_scorer(c, num, team, player, minute='10', penalty=0, owngoal=0):
    c.execute(
        "INSERT INTO scorers (match_num, team, player, minute, penalty, owngoal) "
        "VALUES (?,?,?,?,?,?)", (num, team, player, minute, penalty, owngoal))


# ── parsing the openfootball goals feed ─────────────────────────────────────

def test_parse_goals_attributes_team_and_flags():
    mt = {
        "goals1": [
            {"name": "Scorer One", "minute": "9"},
            {"name": "Spot Kicker", "minute": "45+2", "penalty": True},
        ],
        "goals2": [
            {"name": "Own Goaler", "minute": "7", "owngoal": True},
        ],
    }
    goals = data_source._parse_goals(mt, "Mexico", "Brazil")
    by_player = {g["player"]: g for g in goals}

    # goals1 -> team1, goals2 -> team2 (the beneficiary side for an own goal)
    assert by_player["Scorer One"]["team"] == "Mexico"
    assert by_player["Spot Kicker"]["team"] == "Mexico"
    assert by_player["Own Goaler"]["team"] == "Brazil"
    # flags
    assert by_player["Spot Kicker"]["penalty"] is True
    assert by_player["Scorer One"]["penalty"] is False
    assert by_player["Own Goaler"]["owngoal"] is True
    # minute kept as text (handles "45+2")
    assert by_player["Spot Kicker"]["minute"] == "45+2"


def test_parse_goals_skips_nameless_and_empty():
    assert data_source._parse_goals({}, "A", "B") == []
    mt = {"goals1": [{"minute": "5"}]}          # no name -> dropped
    assert data_source._parse_goals(mt, "A", "B") == []


def test_normalize_carries_goals_through():
    """normalize() must surface a `goals` list so seed/update can persist it."""
    raw = data_source.fetch_raw(prefer_remote=False)
    matches = data_source.normalize(raw)
    assert all("goals" in m for m in matches)
    assert any(m["goals"] for m in matches), "the cached feed has finished matches"


# ── leaderboard rules ───────────────────────────────────────────────────────

def test_rebuild_and_leaderboard_excludes_own_goals():
    c = _mem()
    matches = [{
        "num": 1,
        "goals": [
            {"team": "T1", "player": "Striker", "minute": "10",
             "penalty": False, "owngoal": False},
            {"team": "T1", "player": "Striker", "minute": "80",
             "penalty": False, "owngoal": False},
            {"team": "T1", "player": "Lucky", "minute": "90",
             "penalty": False, "owngoal": True},   # own goal -> never credited
        ],
    }]
    goldenboot.rebuild_scorers(c, matches)
    board = goldenboot.leaderboard(c)
    names = {p["player"]: p for p in board}
    assert names["Striker"]["goals"] == 2
    assert "Lucky" not in names, "own goals must not appear on the board"


def test_rebuild_is_idempotent():
    c = _mem()
    matches = [{"num": 1, "goals": [
        {"team": "T1", "player": "A", "minute": "5", "penalty": False, "owngoal": False}]}]
    goldenboot.rebuild_scorers(c, matches)
    goldenboot.rebuild_scorers(c, matches)      # second run must not double-count
    assert goldenboot.leaderboard(c)[0]["goals"] == 1


def test_penalties_count_but_are_tracked():
    c = _mem()
    _add_scorer(c, 1, "T1", "PenTaker", penalty=1)
    _add_scorer(c, 2, "T1", "PenTaker", penalty=1)
    c.commit()
    p = goldenboot.leaderboard(c)[0]
    assert p["goals"] == 2          # penalties DO count toward the Golden Boot
    assert p["penalties"] == 2


def test_ranking_tiebreak_prefers_fewer_penalties_and_uses_competition_rank():
    c = _mem()
    # P_open: 3 open-play goals; P_pen: 3 goals incl. a penalty; P_low: 1 goal
    _add_scorer(c, 1, "T1", "P_open"); _add_scorer(c, 2, "T1", "P_open"); _add_scorer(c, 3, "T1", "P_open")
    _add_scorer(c, 1, "T2", "P_pen"); _add_scorer(c, 2, "T2", "P_pen"); _add_scorer(c, 3, "T2", "P_pen", penalty=1)
    _add_scorer(c, 1, "T3", "P_low")
    c.commit()
    board = goldenboot.leaderboard(c)
    assert [p["player"] for p in board] == ["P_open", "P_pen", "P_low"]
    # joint leaders share rank 1, the 1-goal player is rank 3 (competition ranking)
    assert [p["rank"] for p in board] == [1, 1, 3]


def test_same_name_different_team_stay_separate():
    c = _mem()
    _add_scorer(c, 1, "T1", "Hernandez")
    _add_scorer(c, 2, "T2", "Hernandez")
    c.commit()
    board = goldenboot.leaderboard(c)
    assert len(board) == 2


# ── projection ──────────────────────────────────────────────────────────────

def test_projection_arithmetic_with_shrinkage():
    board = [{"player": "X", "team": "T", "goals": 2,
              "penalties": 0, "matches_scored": 1}]
    goldenboot.project(board, teams_odds={}, played={"T": 1}, remaining={"T": 4.0})
    p = board[0]
    # rate = (2 + 0.5*2) / (1 + 2) = 1.0 ; +4.0 over 4 matches ; total 6.0
    assert p["rate"] == 1.0
    assert p["proj_additional"] == 4.0
    assert p["proj_total"] == 6.0
    assert p["proj_total"] >= p["goals"]


def test_projection_exposes_whole_goal_range():
    """The projection must be surfaced as an integer range, not a fractional tally."""
    board = [{"player": "X", "team": "T", "goals": 2,
              "penalties": 0, "matches_scored": 1}]
    goldenboot.project(board, teams_odds={}, played={"T": 1}, remaining={"T": 4.0})
    p = board[0]
    for key in ("proj_add_low", "proj_add_high", "proj_total_low", "proj_total_high"):
        assert isinstance(p[key], int), f"{key} must be a whole goal count"
    # a non-trivial mean (4.0) yields a genuine spread bracketing the expectation
    assert p["proj_add_low"] < p["proj_add_high"]
    assert p["proj_add_low"] <= p["proj_additional"] <= p["proj_add_high"]
    # the total range is just the present tally shifted up by the additional range
    assert p["proj_total_low"] == p["goals"] + p["proj_add_low"]
    assert p["proj_total_high"] == p["goals"] + p["proj_add_high"]


def test_projection_never_below_current_tally():
    board = [{"player": "Y", "team": "T", "goals": 5,
              "penalties": 0, "matches_scored": 3}]
    goldenboot.project(board, teams_odds={}, played={"T": 3}, remaining={"T": 0.0})
    # an eliminated team has no remaining matches -> no upside, but never negative
    assert board[0]["proj_additional"] == 0.0
    assert board[0]["proj_total"] == 5.0
    # the range collapses onto the present tally — nothing more to score
    assert board[0]["proj_add_low"] == 0 and board[0]["proj_add_high"] == 0
    assert board[0]["proj_total_low"] == 5 and board[0]["proj_total_high"] == 5


def test_poisson_interval_is_central_and_integer():
    # zero mean -> a point at 0 (no remaining matches, no goals to add)
    assert goldenboot._poisson_interval(0.0) == (0, 0)
    lo, hi = goldenboot._poisson_interval(4.0)
    assert isinstance(lo, int) and isinstance(hi, int)
    assert 0 <= lo < hi
    assert lo <= 4 <= hi                    # the interval brackets the mean
    # wider mean -> wider (or equal) band, and the band never inverts
    lo2, hi2 = goldenboot._poisson_interval(8.0)
    assert hi2 - lo2 >= hi - lo


def test_expected_remaining_matches_uses_odds_and_subtracts_played():
    c = _mem()
    # T1 has one group match still to play; no knockout games finished yet
    c.execute("INSERT INTO matches (num, stage, group_letter, team1, team2, status) "
              "VALUES (1,'group','A','T1','T2','scheduled')")
    c.commit()
    odds = {"T1": {"advance": 0.5, "r16": 0.25, "qf": 0.1, "sf": 0.05}}
    rem = goldenboot._expected_remaining_matches(c, odds)
    # 1 group + (0.5 + 0.25 + 0.1 + 2*0.05) knockout = 1 + 0.95
    assert rem["T1"] == pytest.approx(1.95)


def test_expected_remaining_subtracts_knockout_already_played():
    c = _mem()
    c.execute("INSERT INTO matches (num, stage, team1, team2, status) "
              "VALUES (73,'knockout','T1','T2','finished')")
    c.commit()
    odds = {"T1": {"advance": 1.0, "r16": 0.5, "qf": 0.2, "sf": 0.1}}
    rem = goldenboot._expected_remaining_matches(c, odds)
    # total knockout expectation 1.0+0.5+0.2+0.2 = 1.9, minus the one already played
    assert rem["T1"] == pytest.approx(0.9)


# ── full tracker payload ────────────────────────────────────────────────────

def test_tracker_payload_shape_and_contention(conn):
    data = goldenboot.tracker(conn, sims=SIMS, seed=SEED)
    assert {"contenders", "leader_goals", "n_finished", "total_goals",
            "contention_gap", "generated"} <= set(data)
    board = data["contenders"]
    assert board, "the seeded feed has goals"
    # sorted best-first, leader is rank 1 and in contention
    assert [p["goals"] for p in board] == sorted((p["goals"] for p in board), reverse=True)
    assert board[0]["rank"] == 1
    assert board[0]["in_contention"] is True
    # everyone carries the projection fields, including the whole-goal range
    for p in board:
        assert "proj_total" in p and "proj_additional" in p
        assert p["proj_total"] >= p["goals"]
        assert {"proj_add_low", "proj_add_high",
                "proj_total_low", "proj_total_high"} <= set(p)
        assert p["proj_add_low"] <= p["proj_add_high"]
        assert p["proj_total_low"] == p["goals"] + p["proj_add_low"]
        assert p["proj_total_high"] >= p["goals"]
    # contention flag matches the documented gap
    leader = data["leader_goals"]
    for p in board:
        assert p["in_contention"] == (p["goals"] >= leader - data["contention_gap"])


def test_tracker_handles_no_goals():
    c = _mem()      # schema only, no scorers, no matches
    data = goldenboot.tracker(c)
    assert data["contenders"] == []
    assert data["leader_goals"] == 0
    assert data["total_goals"] == 0


# ── API + page plumbing ─────────────────────────────────────────────────────

def test_api_endpoint_returns_board_with_flag_codes(client):
    resp = client.get('/api/golden-boot')
    assert resp.status_code == 200
    body = resp.get_json()
    assert "contenders" in body and body["contenders"]
    first = body["contenders"][0]
    assert first["rank"] == 1
    assert "code" in first, "each contender carries a flag code for the UI"


def test_page_renders_table_and_controls(client):
    html = client.get('/golden-boot').get_data(as_text=True)
    assert 'id="gbTable"' in html
    # the actual/projected sort toggle the JS keys off
    assert 'id="gbSortNow"' in html
    assert 'id="gbSortProj"' in html
    assert 'Proj total' in html


def test_page_shows_whole_goal_ranges_not_decimals(client):
    """Revision JOE-13: projected goals must read as a whole-number range,
    never a fractional tally like ``4.4``."""
    import re
    html = client.get('/golden-boot').get_data(as_text=True)
    proj = re.findall(r'<td class="num proj">\s*(.*?)</td>', html, re.S)
    projtot = re.findall(r'<td class="num projtot">\s*(.*?)</td>', html, re.S)
    assert proj and projtot, "the seeded feed has contenders with projections"
    for cell in proj + projtot:
        # no decimals anywhere in the projection columns
        assert '.' not in cell, f"projection cell still fractional: {cell!r}"
    # at least one genuine range is rendered (en-dash separating low/high)
    assert any('–' in cell for cell in projtot)


def test_nav_links_to_golden_boot(client):
    # the feature must be reachable from the global nav on every page
    html = client.get('/').get_data(as_text=True)
    assert '/golden-boot' in html
    assert 'Golden Boot' in html


def test_template_has_empty_state():
    """The page must degrade gracefully before the first goal is scored."""
    tpl = _read('templates', 'goldenboot.html')
    assert 'gb-empty' in tpl


# ── front-end wiring ────────────────────────────────────────────────────────

def test_css_styles_the_tracker():
    css = _read('static', 'css', 'style.css')
    assert '.gb-table' in css
    assert '.gb-tab.active' in css
    assert '.gb-table tr.leader' in css


def test_js_resorts_between_now_and_projected():
    js = _read('static', 'js', 'goldenboot.js')
    # reads both metrics off the rows and re-ranks on toggle
    assert 'data-proj' in js
    assert 'data-goals' in js
    assert 'by-proj' in js


# ── deploy self-heal (JOE-13 revision) ──────────────────────────────────────
# The Golden Boot feature added a new `scorers` table. On an already-seeded
# production DB, pulling the code is not enough — the table has to be created,
# and the updater's wholesale `DELETE FROM scorers` rebuild would otherwise
# crash with "no such table: scorers". These guard that a plain pull + restart
# brings the feature up on an existing DB, with no manual migration.

def test_app_import_applies_schema(conn):
    """Importing the app (gunicorn startup) creates the scorers table."""
    # `flask_app` is imported at module load; the table must exist by now.
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert 'scorers' in names


def test_updater_recreates_and_repopulates_scorers(conn):
    """Simulate an old production DB: drop `scorers`, run the offline updater,
    and confirm it self-heals the schema and refills the table."""
    import update_results

    conn.execute("DROP TABLE IF EXISTS scorers")
    conn.commit()
    assert not conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='scorers'"
    ).fetchone()

    # Offline: reads the committed openfootball snapshot, no network.
    update_results.main(prefer_remote=False)

    # Fresh connection — the updater used its own.
    c2 = db.connect()
    try:
        assert c2.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='scorers'"
        ).fetchone()
        assert c2.execute("SELECT COUNT(*) FROM scorers").fetchone()[0] > 0
    finally:
        c2.close()
