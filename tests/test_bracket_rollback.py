"""Tests for the bracket roll-back feature (JOE-50).

Users can roll the bracket back to the end of an earlier matchday: every later
real result is stripped from the working view (never from the DB), the dynamic
Elo replay stops at the cutoff — so the "real-world data that updates the
simulation models" rewinds too — and the tournament is re-forecast from that
point. Rolled-back matches unlock and become steerable like any other unplayed
match; the group rails rewind to the standings as they stood at the cutoff.

These tests pin the pure view helpers (rollback.py), the engine integration
(predict.py), the API plumbing (app.py) and the UI wiring (template/CSS/JS).
"""

import os

import pytest

import app as flask_app
import db
import predict
import ratings
import rollback

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)

# Small, seeded sims keep the projection deterministic and fast for tests.
SIMS = 300
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


@pytest.fixture
def dates(conn):
    """Finished matchdays in the seeded DB (ascending)."""
    pts = rollback.points(conn)
    assert pts, 'seed data must contain finished results to roll back'
    return [p['date'] for p in pts]


# ── rollback.py: the pure view helpers ───────────────────────────────────────

@pytest.mark.parametrize('raw,expected', [
    ('2026-06-20', '2026-06-20'),
    (' 2026-06-20 ', '2026-06-20'),     # tolerated whitespace
    ('start', 'start'),
    ('', None),
    (None, None),
    ('yesterday', None),                # malformed -> ignored, not an error
    ('2026-6-2', None),
    ('2026-06-20; DROP TABLE', None),
])
def test_parse_param(raw, expected):
    assert rollback.parse_param(raw) == expected


def test_apply_strips_only_results_after_the_cutoff():
    rows = [
        {'num': 1, 'date': '2026-06-11', 'status': 'finished',
         'score1': 2, 'score2': 0},
        {'num': 2, 'date': '2026-06-20', 'status': 'finished',
         'score1': 1, 'score2': 1},
        {'num': 3, 'date': '2026-06-21', 'status': 'finished',
         'score1': 0, 'score2': 3, 'pen1': None, 'pen2': None},
        {'num': 4, 'date': '2026-06-22', 'status': 'scheduled',
         'score1': None, 'score2': None},
    ]
    _, rolled = rollback.apply(rows, '2026-06-20')
    assert rolled == [3]                       # only the later FINISHED match
    assert rows[0]['status'] == 'finished' and rows[0]['score1'] == 2
    assert rows[1]['status'] == 'finished'     # on-the-cutoff-day results kept
    assert rows[2]['status'] == 'scheduled' and rows[2]['score1'] is None
    assert rows[3]['status'] == 'scheduled'    # unplayed stays unplayed, not "rolled"


def test_apply_start_strips_everything():
    rows = [{'num': n, 'date': f'2026-06-{10+n:02d}', 'status': 'finished',
             'score1': 1, 'score2': 0} for n in range(1, 4)]
    _, rolled = rollback.apply(rows, rollback.START)
    assert rolled == [1, 2, 3]
    assert all(r['status'] == 'scheduled' and r['score1'] is None for r in rows)


def test_standings_as_of_rewinds_the_tables(conn, dates):
    """Standings at an early cutoff show fewer games played than live, and at
    'start' every team sits on zero — while every team still appears."""
    live = rollback.standings_as_of(conn, dates[-1])
    early = rollback.standings_as_of(conn, dates[0])
    zero = rollback.standings_as_of(conn, rollback.START)
    assert set(live) == set(early) == set(zero)          # same groups
    for g in live:
        assert len(zero[g]) == len(live[g])              # every team still listed
        assert sum(r['played'] for r in early[g]) < sum(r['played'] for r in live[g])
        assert all(r['played'] == 0 and r['points'] == 0 for r in zero[g])
        assert [r['rank'] for r in zero[g]] == list(range(1, len(zero[g]) + 1))


def test_rollback_never_writes_the_db(conn, dates):
    """The whole feature is an as-of *view* — the matches table must be
    byte-identical after building rolled-back forecasts and standings."""
    before = conn.execute(
        "SELECT num, status, score1, score2, pen1, pen2, team1, team2 "
        "FROM matches ORDER BY num").fetchall()
    predict.predictions(conn, sims=SIMS, seed=SEED, rollback=dates[0])
    rollback.standings_as_of(conn, dates[0])
    after = conn.execute(
        "SELECT num, status, score1, score2, pen1, pen2, team1, team2 "
        "FROM matches ORDER BY num").fetchall()
    assert [tuple(r) for r in before] == [tuple(r) for r in after]


# ── engine: the forecast rewinds with the results ────────────────────────────

def test_rollback_reduces_finished_and_reports_stripped(conn, dates):
    live = predict.predictions(conn, sims=SIMS, seed=SEED)
    rb = predict.predictions(conn, sims=SIMS, seed=SEED, rollback=dates[2])
    assert live['rolled_back'] == [] and live['rollback'] is None
    assert rb['rollback'] == dates[2]
    assert rb['n_finished'] < live['n_finished']
    assert len(rb['rolled_back']) == live['n_finished'] - rb['n_finished']


def test_rollback_to_start_is_the_pre_tournament_forecast(conn):
    """At 'start' nothing has been played: zero finished, every result rolled
    back, and every team's dynamic Elo equals its pre-tournament prior — the
    model's learned in-tournament form is fully unwound (the 'roll back the
    real-world data that updates the simulation models' half of the issue)."""
    live = predict.predictions(conn, sims=SIMS, seed=SEED)
    start = predict.predictions(conn, sims=SIMS, seed=SEED,
                                rollback=rollback.START)
    assert start['n_finished'] == 0
    assert len(start['rolled_back']) == live['n_finished']
    assert all(v['elo'] == v['elo_prior'] for v in start['teams'].values())
    # sanity: live form HAS diverged from the priors, so the equality above
    # genuinely demonstrates a rewind rather than a no-op
    assert any(v['elo'] != v['elo_prior'] for v in live['teams'].values())


def test_rolled_back_knockout_unlocks_and_accepts_overrides(conn, dates):
    """A knockout settled on the pitch is locked in the live projection; rolling
    back past its matchday must unlock it, re-forecast its slots, and accept a
    forced winner exactly like any other unplayed match."""
    live = predict.projected_bracket(conn, {}, sims=SIMS, seed=SEED)['slots']
    locked = [n for n, e in live.items() if e.get('locked')]
    assert locked, 'seed data should contain at least one finished knockout'

    # roll back to the day before the first finished knockout
    ko_date = conn.execute(
        "SELECT MIN(date) FROM matches WHERE stage='knockout' "
        "AND status='finished'").fetchone()[0]
    cutoff = max(d for d in dates if d < ko_date)
    out = predict.projected_bracket(conn, {}, sims=SIMS, seed=SEED,
                                    rollback=cutoff)
    for n in locked:
        e = out['slots'][n]
        assert e['locked'] is False, f'match #{n} must unlock under rollback'
        assert n in out['rolled_back']
        assert e['team1'] and e['team2'], 'rolled-back match must re-project'

    # and the previously-settled match is steerable again: force team2 through
    n = locked[0]
    t2 = out['slots'][n]['team2']['team']
    forced = predict.projected_bracket(conn, {n: t2}, sims=SIMS, seed=SEED,
                                       rollback=cutoff)
    assert forced['overrides'].get(n) == t2


def test_rollback_is_deterministic_and_isolated_from_live(conn, dates):
    """Same cutoff + seed -> identical forecast; and computing a rolled-back
    world must not disturb the cached live aggregate (separate cache keys)."""
    a = predict.predictions(conn, sims=SIMS, seed=SEED, rollback=dates[1])
    live1 = predict.predictions(conn, sims=SIMS, seed=SEED)
    b = predict.predictions(conn, sims=SIMS, seed=SEED, rollback=dates[1])
    live2 = predict.predictions(conn, sims=SIMS, seed=SEED)
    assert a['teams'] == b['teams']
    assert live1['teams'] == live2['teams']
    assert live1['n_finished'] != a['n_finished']


# ── app: API plumbing ────────────────────────────────────────────────────────

def test_rollback_points_endpoint(client, dates):
    body = client.get('/api/rollback/points').get_json()
    assert body['start_value'] == rollback.START
    assert [p['date'] for p in body['points']] == dates
    assert all(p['n'] >= 1 for p in body['points'])


def test_predicted_endpoint_with_rollback(client, dates):
    """?rollback= rewinds the projection: the response echoes the cutoff, lists
    the stripped matches, unlocks them, and carries the as-of standings."""
    cutoff = dates[-2]                       # roll back just the last matchday
    body = client.get('/api/bracket/predicted',
                      query_string={'depth': 'final',
                                    'rollback': cutoff}).get_json()
    assert body['rollback'] == cutoff
    assert body['rolled_back'], 'later results must be reported as rolled back'
    for n in body['rolled_back']:
        e = body['slots'].get(str(n))
        if e:                                # group matches have no bracket slot
            assert e['locked'] is False
    # rails payload: every group present, rows carry rank/team/points/code
    st = body['standings']
    assert st and all(len(rows) >= 4 for rows in st.values())
    row = next(iter(st.values()))[0]
    assert {'rank', 'team', 'points', 'played', 'code'} <= set(row)


def test_predicted_endpoint_without_rollback_stays_live(client):
    body = client.get('/api/bracket/predicted').get_json()
    assert body['rollback'] is None
    assert body['rolled_back'] == []
    assert 'standings' not in body           # rails stay server-rendered live


def test_predicted_endpoint_ignores_malformed_rollback(client):
    body = client.get('/api/bracket/predicted',
                      query_string={'rollback': 'garbage'}).get_json()
    assert body['rollback'] is None
    assert body['rolled_back'] == []


def test_predictions_endpoint_accepts_rollback(client, dates):
    live = client.get('/api/predictions').get_json()
    rb = client.get('/api/predictions',
                    query_string={'rollback': dates[-2]}).get_json()
    assert live['rollback'] is None
    assert rb['rollback'] == dates[-2]
    assert rb['n_finished'] < live['n_finished']


# ── UI wiring (template / CSS / JS) ──────────────────────────────────────────

def test_bracket_template_exposes_rollback_control(client):
    html = client.get('/bracket').get_data(as_text=True)
    assert 'id="rollbackSel"' in html
    assert 'rollbackPointsUrl' in html
    # the live (no-rollback) option is the default
    assert 'Live — all results' in html


def test_pick_hint_explains_rollback(client):
    html = client.get('/bracket').get_data(as_text=True).lower()
    assert 'roll back' in html
    assert 'matchday' in html


def test_js_sends_rollback_and_renders_rolled_back_state():
    js = _read('static', 'js', 'bracket.js')
    # the cutoff is sent with the projection request…
    assert "params.set('rollback', rollback)" in js
    # …the stripped matches are marked and treated as forecasts again…
    assert 'd.rolled_back' in js
    assert "classList.add('rolled-back')" in js
    # …a rolled-back side sheds its winner tint while re-projected, and the
    # original class list is restored when the roll-back ends
    assert "side.classList.remove('win')" in js
    assert 'origCls' in js
    # the group rails rewind with the bracket and are restored afterwards
    assert 'd.standings' in js
    assert 'restoreRails' in js
    # the picker is populated from the points endpoint
    assert 'rollbackPointsUrl' in js


def test_css_styles_rolled_back_matches_and_rails():
    css = _read('static', 'css', 'style.css')
    assert '.bmatch.rolled-back' in css
    assert '.mini-group.rolled-back' in css
