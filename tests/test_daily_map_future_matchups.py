"""The daily map previews future match-ups (JOE-33).

Before a knockout fixture resolves, the daily map used to show a bare slot
placeholder ("1E", "W74") with a "?" flag. Instead it now surfaces the teams
that *may* end up playing there, taken from the same Monte-Carlo aggregate that
drives the projected bracket:

  * ``predict.slot_candidates`` returns, per knockout match and side, the top
    few candidate teams by projected probability.
  * ``/api/matches`` attaches ``team{1,2}_candidates`` (team + flag code + odds)
    to every *undecided* knockout side — and only those sides.
  * the daily-map client (map.js / CSS) renders the "may play" cluster on the
    pin, in the side panel and in the popup.

These tests pin the engine, the API plumbing and the client wiring so the
feature can't silently regress.
"""

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


def _team_names(conn):
    return {r['name'] for r in conn.execute("SELECT name FROM teams")}


def _unresolved_ko(conn):
    """(num, date) of a knockout match with at least one undecided side."""
    row = conn.execute(
        "SELECT num, date FROM matches WHERE stage='knockout' "
        "AND (team1 IS NULL OR team2 IS NULL) ORDER BY num LIMIT 1").fetchone()
    assert row is not None, "expected an undecided knockout fixture in the seed"
    return row['num'], row['date']


# ── engine: slot_candidates ──────────────────────────────────────────────────

def test_slot_candidates_shape_and_validity(conn):
    teams = _team_names(conn)
    cands = predict.slot_candidates(conn, top=3, sims=SIMS, seed=SEED)
    assert cands, "expected candidates for the knockout slots"
    for num, sides in cands.items():
        assert set(sides) == {'team1', 'team2'}
        for side in ('team1', 'team2'):
            lst = sides[side]
            assert len(lst) <= 3
            ps = [c['p'] for c in lst]
            # every candidate is a real team with a plausible probability
            for c in lst:
                assert c['team'] in teams
                assert 0.0 < c['p'] <= 1.0
            # ranked most-likely first, and marginals over a set of teams sum <= 1
            assert ps == sorted(ps, reverse=True)
            assert sum(ps) <= 1.0 + 1e-9


def test_slot_candidates_respect_top_limit(conn):
    one = predict.slot_candidates(conn, top=1, sims=SIMS, seed=SEED)
    assert all(len(s['team1']) <= 1 and len(s['team2']) <= 1 for s in one.values())


def test_slot_candidates_cover_the_open_field(conn):
    """An early-round slot fed by a group winner should list more than one team
    (the field is genuinely open) drawn from that group's members."""
    cands = predict.slot_candidates(conn, top=4, sims=SIMS, seed=SEED)
    # find a knockout side whose slot is a group-winner/runner-up placeholder
    rows = conn.execute(
        "SELECT num, team1, team2, team1_slot, team2_slot FROM matches "
        "WHERE stage='knockout' ORDER BY num").fetchall()
    checked = 0
    for r in rows:
        for side, resolved, slot in (('team1', r['team1'], r['team1_slot']),
                                     ('team2', r['team2'], r['team2_slot'])):
            if resolved is not None:
                continue
            lst = cands[r['num']][side]
            # at least one candidate offered for every undecided side
            assert lst, f"no candidates for match {r['num']} {side} ({slot})"
            checked += 1
    assert checked > 0, "expected at least one undecided knockout side"


# ── API: /api/matches attaches candidates only where undecided ───────────────

def test_api_attaches_candidates_to_undecided_knockout(client, conn):
    num, d = _unresolved_ko(conn)
    resp = client.get('/api/matches', query_string={'date': d})
    assert resp.status_code == 200
    match = next(m for m in resp.get_json() if m['num'] == num)

    saw_candidates = False
    for side in ('team1', 'team2'):
        key = f'{side}_candidates'
        if match[f'{side}_resolved']:
            # a decided side keeps its real team and gets no candidate list
            assert key not in match
        else:
            cands = match.get(key)
            assert cands, f"{side} undecided but no candidates attached"
            saw_candidates = True
            for c in cands:
                assert c['team'] and c['code'] and 0.0 < c['p'] <= 1.0
    assert saw_candidates


def test_group_stage_matches_have_no_candidates(client):
    """The group stage is always fully decided up front — no candidate lists."""
    d = '2026-06-11'  # tournament opening day (group stage)
    matches = client.get('/api/matches', query_string={'date': d}).get_json()
    assert matches, "expected group-stage matches on opening day"
    for m in matches:
        assert m['stage'] == 'group'
        assert 'team1_candidates' not in m and 'team2_candidates' not in m


# ── client wiring (map.js / CSS) ─────────────────────────────────────────────

def test_map_js_renders_candidates():
    js = _read('static', 'js', 'map.js')
    # consumes the API fields
    assert 'team1_candidates' in js and 'team2_candidates' in js
    # renders them on the pin, in the side panel and in the popup
    for fn in ('pinSide', 'listSide', 'popSide', 'slotLabel'):
        assert fn in js, f"map.js missing {fn}"


def test_css_styles_the_maybe_clusters():
    css = _read('static', 'css', 'style.css')
    for cls in ('.fp-maybe', '.ml-maybe', '.ml-cand'):
        assert cls in css, f"style.css missing {cls}"
