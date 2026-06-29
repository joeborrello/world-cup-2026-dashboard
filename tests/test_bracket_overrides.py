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


def _resolved_open(slots):
    """First fully-resolved knockout match that is NOT locked (i.e. not already
    finished), so its winner can still be forced. Robust as the tournament
    progresses and earlier rounds become locked, where _resolved_r32 would pick a
    finished match the engine (correctly) refuses to override."""
    for num in sorted(slots):
        e = slots[num]
        if e['team1'] and e['team2'] and not e.get('locked'):
            return num, e['team1']['team'], e['team2']['team']
    raise AssertionError('no open (overridable) resolved match in projection')


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
    num, t1, t2 = _resolved_open(base)

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
    # The forced underdog must advance into the *immediate* next match the default
    # winner used to feed, and the default winner must no longer appear anywhere
    # downstream. We don't require the underdog to occupy *every* slot the default
    # winner once reached: the underdog can lose a later round, so the slots beyond
    # the next match get re-resolved to whoever wins there (correct engine
    # behaviour — asserting full equality made this test brittle).
    underdog_feed = _downstream_of(forced, num, underdog)
    assert feed[0] in underdog_feed, 'underdog must advance into the next match'
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


# ── JOE-11: resolved-but-unplayed R32 matches are steerable ─────────────────
# Once the groups are decided, every Round-of-32 pairing holds two *real*
# qualified teams, yet the match hasn't been played. The bracket template renders
# those sides WITHOUT the `tbd` class, and the old projection code only made
# `tbd` sides clickable — so the entire R32 column swallowed clicks and nothing
# could be picked. The fix: a resolved side whose match isn't finished (not
# `locked`) is just as steerable as a projected one.

def test_resolved_r32_matches_are_overridable_not_locked(conn):
    """Every R32 match with both real teams known but unplayed must be
    overridable: `locked` is False (only finished matches lock), so the engine
    accepts a forced winner. This is the exact state the R32 column is in once the
    groups finish — the case the original UI couldn't select."""
    base = predict.projected_bracket(conn, {}, sims=SIMS, seed=SEED)['slots']
    r32 = {n: e for n, e in base.items() if e['round'] == 'r32'}
    assert r32, 'sanity: projection should include R32 slots'
    resolved = [(n, e) for n, e in r32.items() if e['team1'] and e['team2']]
    assert resolved, 'sanity: at least one R32 match should have both teams known'
    for num, e in resolved:
        assert not e.get('locked'), f'unplayed R32 match #{num} must not be locked'

    # forcing either competitor in such a match must take effect (be echoed back)
    num, e = resolved[0]
    t2 = e['team2']['team']
    out = predict.projected_bracket(conn, {num: t2}, sims=SIMS, seed=SEED)
    assert out['overrides'].get(num) == t2, \
        'a resolved-but-unplayed R32 match must accept a forced winner'


def test_js_makes_resolved_sides_clickable_not_just_tbd():
    """The root JOE-11 bug: only `tbd` sides were made interactive, so R32 sides
    holding already-qualified teams (no `tbd` class) couldn't be picked. The fix
    keys interactivity off `locked` instead of `tbd`, marks resolved sides
    `decided`, and only outlines a box that actually has a pickable side."""
    js = _read('static', 'js', 'bracket.js')
    # interactivity now gates on the match being locked, not on the side being tbd
    assert 'if (!tbd && e.locked) return' in js
    # the old tbd-only gate that swallowed R32 clicks must not come back
    assert "!side.classList.contains('tbd')) return" not in js
    # resolved (already-qualified) sides get the `decided` marker so they stay
    # clickable but render upright (no italics / trivial 100% badge)
    assert "add('decided')" in js
    # a box is only outlined as steerable when it actually has a pickable side
    assert 'if (pickable)' in js


def test_css_keeps_decided_sides_upright():
    """A `decided` side is a real qualified team, not a guess — it must render
    upright (override the projected italics) so it doesn't read as a prediction."""
    css = _read('static', 'css', 'style.css')
    assert '.bm-side.predicted.decided' in css


def test_pick_hint_explains_round_of_32_is_clickable(client):
    """The hint must tell the user the already-qualified R32 teams are clickable,
    so the previously-dead R32 column reads as interactive."""
    html = client.get('/bracket').get_data(as_text=True).lower()
    assert 'round of 32' in html
    assert 'qualified' in html


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


def test_endpoint_echoes_applied_overrides(client):
    # Drive the override off the endpoint's OWN projection (served from the cached
    # aggregate), so we force a team the endpoint actually placed in that match —
    # comparing against a differently-seeded projection can disagree on the
    # sim-dependent third-place slots.
    base = client.get('/api/bracket/predicted',
                      query_string={'depth': 'final'}).get_json()['slots']
    num = next(n for n in sorted(base, key=int)
               if base[n]['team1'] and base[n]['team2'] and not base[n].get('locked'))
    t2 = base[num]['team2']['team']
    # pass via query_string so values with '&' (e.g. "Bosnia & Herzegovina") are
    # URL-encoded — the real client uses URLSearchParams, which does the same.
    resp = client.get('/api/bracket/predicted',
                      query_string={'depth': 'final', 'overrides': json.dumps({num: t2})})
    assert resp.status_code == 200
    body = resp.get_json()
    assert 'overrides' in body
    assert body['overrides'].get(num) == t2


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
    # a live status line so the projection is never silent (loading/applied/error)
    assert 'id="pickStatus"' in html


def test_bracket_template_has_floating_pick_toast(client):
    """A floating confirmation pinned over the bracket — the page-top status line
    scrolls out of view, which is why earlier picks felt like they did nothing.
    The toast lives *inside* the viewport so feedback shows where the user clicks."""
    html = client.get('/bracket').get_data(as_text=True)
    assert 'id="pickToast"' in html
    viewport = html.split('id="bviewport"', 1)[1]
    assert 'id="pickToast"' in viewport.split('</div>', 1)[0] or 'pickToast' in viewport[:400]


def test_pick_hint_explains_which_teams_are_clickable(client):
    """The revision feedback was 'I clicked and nothing happened / need instructions'.
    The hint must spell out that the italic projected teams are the clickable ones."""
    html = client.get('/bracket').get_data(as_text=True).lower()
    assert 'italic' in html        # tells the user which teams are interactive
    assert 'click' in html


def test_css_styles_locked_pick():
    css = _read('static', 'css', 'style.css')
    assert '.bm-side.predicted.locked' in css
    assert '.pick-reset' in css


def test_css_clickable_affordance_targets_real_inner_class():
    """The 'projecting' class is toggled on <div class="bracket-inner" id="binner">,
    so the cursor/hover rules must key off `.bracket-inner.projecting`. A stale
    `.binner.projecting` selector matches nothing, leaving the projected teams with
    no clickable affordance (the JOE-10 revision bug). Pin the working selector."""
    css = _read('static', 'css', 'style.css')
    assert '.bracket-inner.projecting .bm-side.predicted' in css
    assert 'cursor: pointer' in css
    # the broken selector must not come back
    assert '.binner.projecting' not in css


def test_js_marks_predicted_sides_clickable():
    """Each projected side gets a title tooltip so users discover it's clickable."""
    js = _read('static', 'js', 'bracket.js')
    assert 'side.title' in js


def test_js_sends_and_reconciles_overrides():
    js = _read('static', 'js', 'bracket.js')
    # overrides are sent on the request...
    assert "params.set('overrides'" in js
    # ...and reconciled to what the engine actually applied
    assert 'd.overrides' in js
    # clicking a projected side toggles its pick
    assert "closest('.bm-side.predicted')" in js


def test_js_accepts_a_click_anywhere_on_the_projected_box():
    """The core revision bug: at the default 'Fit' zoom the team rows are only
    ~10px tall, so a pixel-perfect hit was required and most clicks missed
    ('I click a country and nothing happens'). A click anywhere on the projected
    box must now resolve to the nearest team."""
    js = _read('static', 'js', 'bracket.js')
    # the whole match box is a fallback hit target...
    assert "closest('.bmatch.has-pred')" in js
    # ...mapped to the nearer team by vertical position
    assert 'getBoundingClientRect' in js


def test_js_surfaces_feedback_in_floating_toast():
    """Click feedback must reach a viewport-pinned toast, not only the page-top
    status line that scrolls away."""
    js = _read('static', 'js', 'bracket.js')
    assert 'pickToast' in js


def test_css_styles_floating_toast_and_whole_box_target():
    css = _read('static', 'css', 'style.css')
    assert '.pick-toast' in css
    assert 'position: fixed' in css.split('.pick-toast', 1)[1][:200]
    # the whole projected box shows a pointer cursor (not just the thin team row)
    assert '.bracket-inner.projecting .bmatch.has-pred' in css


def test_js_never_fails_the_projection_silently():
    """A failed projection fetch must surface a message, not quietly do nothing —
    that silent failure was the root of the 'I clicked and nothing happened' report.
    Pin the non-ok check, the .catch handler, and the status updates."""
    js = _read('static', 'js', 'bracket.js')
    assert 'if (!r.ok)' in js              # reject non-2xx responses...
    assert '.catch(' in js                 # ...and handle the rejection
    assert 'setStatus(' in js              # surface state to the user
    css = _read('static', 'css', 'style.css')
    assert '.pick-status' in css
