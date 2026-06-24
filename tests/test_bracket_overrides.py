"""Tests for interactive predictive-bracket manipulation (JOE-10).

The projected bracket can be steered with user `overrides` ({match_num:
forced_winner}). A forced winner must advance from its match and be carried
forward into every later slot it feeds, while the heavy Monte-Carlo aggregate
(and therefore per-slot confidence) stays unchanged. Overrides that can't take
effect (the team isn't actually in that match) are dropped and echoed back so
the client can stay in sync.

These tests pin down the engine (predict.py), the API plumbing (app.py) and the
interactive UI wiring (template/CSS/JS) so the feature can't silently regress.
"""

import json
import os

import pytest

import app as flask_app
import db
import predict

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


def _resolved_r32(slots):
    """Return (num, team1, team2) of the first R32 match whose two competitors
    are both projected (so we have a real pairing to force a winner in)."""
    for num in sorted(slots):
        e = slots[num]
        if e['round'] == 'r32' and e['team1'] and e['team2']:
            return num, e['team1']['team'], e['team2']['team']
    raise AssertionError('no fully-resolved R32 match in projection')


def _downstream_of(slots, num, team):
    """Match numbers after `num` where `team` appears on either side."""
    return [n for n in sorted(slots) if n > num
            for s in ('team1', 'team2')
            if slots[n][s] and slots[n][s]['team'] == team]


# ── engine: overrides force a winner and propagate ──────────────────────────

def test_default_projection_has_no_overrides(conn):
    data = predict.projected_bracket(conn, {}, sims=SIMS, seed=SEED)
    assert data['overrides'] == {}
    assert data['slots'], 'projection should produce slots'


def test_forcing_underdog_carries_it_downstream(conn):
    """Forcing the team that does NOT win by default must flip the match winner
    and replace the default winner everywhere downstream."""
    base = predict.projected_bracket(conn, {}, sims=SIMS, seed=SEED)['slots']
    num, t1, t2 = _resolved_r32(base)

    # The default winner is the competitor that appears in a later slot.
    if _downstream_of(base, num, t1):
        default_winner, underdog = t1, t2
    else:
        default_winner, underdog = t2, t1
    assert _downstream_of(base, num, underdog) == [], \
        'underdog should not advance by default'

    out = predict.projected_bracket(conn, {num: underdog}, sims=SIMS, seed=SEED)
    assert out['overrides'] == {num: underdog}, 'override should be applied'

    forced = out['slots']
    feed = _downstream_of(base, num, default_winner)
    assert feed, 'sanity: default winner should feed a later match'
    # the forced underdog now occupies the slots the default winner used to.
    assert _downstream_of(forced, num, underdog) == feed
    assert _downstream_of(forced, num, default_winner) == []


def test_override_leaves_aggregate_confidence_untouched(conn):
    """Overrides only re-walk the single projected bracket; the underlying sims
    (hence per-slot marginal confidence on un-forced slots) must not change."""
    base = predict.projected_bracket(conn, {}, sims=SIMS, seed=SEED)['slots']
    num, _, _ = _resolved_r32(base)
    # confidence reported for the match's own sides is the marginal P(occupies),
    # which is independent of the override and so identical in both runs.
    out = predict.projected_bracket(conn, {num: base[num]['team2']['team']},
                                    sims=SIMS, seed=SEED)['slots']
    assert out[num]['team1']['conf'] == base[num]['team1']['conf']
    assert out[num]['team2']['conf'] == base[num]['team2']['conf']


def test_invalid_override_is_dropped(conn):
    """An override naming a team that isn't in the match (or a non-existent
    match) takes no effect and is not echoed back."""
    base = predict.projected_bracket(conn, {}, sims=SIMS, seed=SEED)['slots']
    num, t1, t2 = _resolved_r32(base)
    out = predict.projected_bracket(
        conn, {num: 'Atlantis', 99999: t1}, sims=SIMS, seed=SEED)
    assert num not in out['overrides']
    assert 99999 not in out['overrides']


def test_predictions_backcompat(conn):
    """The un-manipulated entry point still returns teams + a default bracket."""
    data = predict.predictions(conn, sims=SIMS, seed=SEED)
    assert 'teams' in data and 'slots' in data
    assert 'overrides' not in data        # plain odds view carries no picks


# ── app: parse + endpoint plumbing ──────────────────────────────────────────

@pytest.mark.parametrize('raw,expected', [
    ('{"73": "Switzerland"}', {73: 'Switzerland'}),
    ('{"73": "A", "74": "B"}', {73: 'A', 74: 'B'}),
    ('not json', {}),
    ('[1, 2, 3]', {}),          # not an object
    ('', {}),
    (None, {}),
    ('{"x": "Y"}', {}),         # non-int key dropped
])
def test_parse_overrides(raw, expected):
    assert flask_app._parse_overrides(raw) == expected


def test_endpoint_echoes_applied_overrides(client, conn):
    base = predict.projected_bracket(conn, {}, sims=SIMS, seed=SEED)['slots']
    num, t1, t2 = _resolved_r32(base)
    ov = json.dumps({str(num): t2})
    resp = client.get('/api/bracket/predicted?depth=final&overrides=' + ov)
    assert resp.status_code == 200
    body = resp.get_json()
    assert 'overrides' in body
    assert body['overrides'].get(str(num)) == t2


def test_endpoint_without_overrides_still_works(client):
    resp = client.get('/api/bracket/predicted')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['overrides'] == {}
    assert body['slots']


# ── UI wiring (template / CSS / JS) ─────────────────────────────────────────

def test_bracket_template_exposes_pick_controls(client):
    html = client.get('/bracket').get_data(as_text=True)
    assert 'id="resetPicks"' in html
    assert 'id="pickHint"' in html


def test_css_styles_locked_pick():
    css = _read('static', 'css', 'style.css')
    assert '.bm-side.predicted.locked' in css
    assert '.pick-reset' in css


def test_js_sends_and_reconciles_overrides():
    js = _read('static', 'js', 'bracket.js')
    # overrides are sent on the request...
    assert "params.set('overrides'" in js
    # ...and reconciled to what the engine actually applied
    assert 'd.overrides' in js
    # clicking a projected side toggles its pick
    assert "closest('.bm-side.predicted')" in js
