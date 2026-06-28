"""
2026 World Cup dashboard — Flask application.

Serves three views (bracket, groups, daily map) plus a small JSON API that the
map's day-slider calls. All data comes from data/worldcup.db, built by
seed_data.py and refreshed by update_results.py.
"""

import json
import re
import time
from datetime import date, datetime, timedelta

from flask import Flask, jsonify, render_template, request

import alerts
import config
import db
import live
import predict
import publish_pages
import pundits
import weather
from flags import flag, flag_code


class SubpathMiddleware:
    """Set SCRIPT_NAME so url_for() emits /worldcup/... behind nginx."""

    def __init__(self, wsgi_app, script_name=config.SUBPATH):
        self.wsgi_app = wsgi_app
        self.script_name = script_name

    def __call__(self, environ, start_response):
        environ['SCRIPT_NAME'] = self.script_name
        if 'HTTP_X_REAL_IP' in environ:
            environ['REMOTE_ADDR'] = environ['HTTP_X_REAL_IP']
        if environ.get('HTTP_X_FORWARDED_PROTO') == 'https':
            environ['wsgi.url_scheme'] = 'https'
        return self.wsgi_app(environ, start_response)


app = Flask(__name__)
app.config['APPLICATION_ROOT'] = config.SUBPATH
app.config['PREFERRED_URL_SCHEME'] = 'https'
app.wsgi_app = SubpathMiddleware(app.wsgi_app)
app.jinja_env.filters['flag'] = flag


# ── data helpers ───────────────────────────────────────────────────────────

def _venues_map(conn):
    return {v['ground']: dict(v) for v in conn.execute("SELECT * FROM venues")}


def _match_dict(m, venues):
    """Shape a matches row for templates / JSON, joining venue + display teams."""
    v = venues.get(m['ground'], {})
    # what to show for each side: resolved team, else the raw slot placeholder
    t1 = m['team1'] or m['team1_slot']
    t2 = m['team2'] or m['team2_slot']
    return {
        'num': m['num'], 'stage': m['stage'], 'round': m['round_label'],
        'group': m['group_letter'], 'date': m['date'],
        'local_time': m['local_time'], 'utc_offset': m['utc_offset'],
        'utc_datetime': m['utc_datetime'],
        'team1': t1, 'team2': t2,
        'team1_resolved': m['team1'] is not None,
        'team2_resolved': m['team2'] is not None,
        'team1_code': flag_code(m['team1']), 'team2_code': flag_code(m['team2']),
        'score1': m['score1'], 'score2': m['score2'],
        'status': m['status'],
        'ground': m['ground'], 'stadium': v.get('stadium'),
        'city': v.get('city'), 'country': v.get('country'),
        'lat': v.get('lat'), 'lng': v.get('lng'), 'tz': v.get('tz'),
    }


def _standings_by_group(conn):
    rows = conn.execute(
        "SELECT * FROM standings ORDER BY group_letter, rank"
    ).fetchall()
    groups = {}
    for r in rows:
        groups.setdefault(r['group_letter'], []).append(dict(r))
    return groups


# ── page routes ────────────────────────────────────────────────────────────

@app.route('/')
def index():
    conn = db.connect()
    venues = _venues_map(conn)
    # Send the whole schedule; the page picks "today" (and the next match day) in
    # the VIEWER's device timezone from each match's UTC kickoff, so the grouping
    # matches their local calendar day rather than a server-chosen timezone.
    rows = conn.execute(
        "SELECT * FROM matches ORDER BY utc_datetime").fetchall()
    matches = [_match_dict(m, venues) for m in rows]
    n_finished = conn.execute(
        "SELECT COUNT(*) FROM matches WHERE status='finished'").fetchone()[0]
    conn.close()
    return render_template('index.html', matches=matches, n_finished=n_finished)


@app.route('/groups')
def groups():
    conn = db.connect()
    groups_data = _standings_by_group(conn)
    conn.close()
    return render_template('groups.html', groups=groups_data)


# Group rail order: each group's WINNER (1X) feeds exactly one R32 match; this
# orders the 12 group tables top→bottom to roughly track the R32 column, so the
# connector lines from groups into the bracket stay as untangled as possible.
GROUP_RAIL_ORDER = ['E', 'F', 'C', 'I', 'A', 'L', 'D', 'G', 'H', 'B', 'J', 'K']


def _slot_group(slot):
    """'1E'/'2A' -> 'E'/'A' (a concrete group feeder); else None."""
    m = re.match(r'^[12]([A-L])$', slot or '')
    return m.group(1) if m else None


def _slot_wildcards(slot):
    """'3A/B/C/D/F' -> ['A','B','C','D','F'] (best-3rd candidates); else []."""
    if not slot or not slot.startswith('3'):
        return []
    return [c for c in re.split(r'[/]', slot[1:]) if c]


def _slot_source(slot):
    """'W74'/'L101' -> 74/101 (the match this slot is fed from); else None."""
    m = re.match(r'^[WL](\d+)$', slot or '')
    return int(m.group(1)) if m else None


@app.route('/bracket')
def bracket():
    conn = db.connect()
    venues = _venues_map(conn)
    ko_rows = conn.execute(
        "SELECT * FROM matches WHERE stage='knockout' ORDER BY num"
    ).fetchall()
    by_round = {}
    group_feeds = {}  # letter -> {'1': num, '2': num, '3': [nums]}
    for row in ko_rows:
        m = _match_dict(row, venues)
        # concrete feeder groups (for R32 lines into the group rail)
        groups = [g for g in (_slot_group(row['team1_slot']),
                              _slot_group(row['team2_slot'])) if g]
        wilds = sorted(set(_slot_wildcards(row['team1_slot'])
                           + _slot_wildcards(row['team2_slot'])))
        # source matches (for R16+ lines back to the previous round)
        srcs = [s for s in (_slot_source(row['team1_slot']),
                            _slot_source(row['team2_slot'])) if s]
        m['feeder_groups'] = groups
        m['wildcards'] = wilds
        m['sources'] = srcs
        by_round.setdefault(row['round_label'], []).append(m)
        # record where each group's finishers go (R32 only)
        for slot in (row['team1_slot'], row['team2_slot']):
            g = _slot_group(slot)
            if g:
                group_feeds.setdefault(g, {'1': None, '2': None, '3': []})
                group_feeds[g][slot[0]] = row['num']
            for c in _slot_wildcards(slot):
                group_feeds.setdefault(c, {'1': None, '2': None, '3': []})
                group_feeds[c]['3'].append(row['num'])
    standings = _standings_by_group(conn)
    conn.close()
    cols = {
        'r32': by_round.get('Round of 32', []),
        'r16': by_round.get('Round of 16', []),
        'qf': by_round.get('Quarter-final', []),
        'sf': by_round.get('Semi-final', []),
        'final': by_round.get('Final', []),
        'third': by_round.get('Match for third place', []),
    }

    # Walk the knockout tree from the Final to order rounds by bracket position
    # and to split the draw into two halves that converge on the Final.
    ko_by_num = {m['num']: m for r in by_round.values() for m in r}

    def subtree_leaves(num):
        m = ko_by_num.get(num)
        if not m or not m['sources']:
            return [num]
        out = []
        for s in m['sources']:
            out += subtree_leaves(s)
        return out

    def subtree_all(num):
        m = ko_by_num.get(num)
        if not m:
            return {num}
        s = {num}
        for c in m['sources']:
            s |= subtree_all(c)
        return s

    # left/right halves + per-side group rails
    left = {k: [] for k in ('r32', 'r16', 'qf', 'sf')}
    right = {k: [] for k in ('r32', 'r16', 'qf', 'sf')}
    left_rail, right_rail = [], []

    if cols['final']:
        roots = cols['final'][0]['sources']  # the two semi-finals
        leaf_order = subtree_leaves(cols['final'][0]['num'])
        leaf_index = {n: i for i, n in enumerate(leaf_order)}

        def sort_key(m):
            idxs = [leaf_index.get(l, 0) for l in subtree_leaves(m['num'])]
            return sum(idxs) / len(idxs) if idxs else 0

        for key in ('r32', 'r16', 'qf', 'sf'):
            cols[key].sort(key=sort_key)

        left_nums = subtree_all(roots[0]) if roots else set()
        right_nums = subtree_all(roots[1]) if len(roots) > 1 else set()
        for key in ('r32', 'r16', 'qf', 'sf'):
            for m in cols[key]:
                (left if m['num'] in left_nums else right)[key].append(m)

        # Each group's winner & runner-up land in opposite halves, so the group
        # table appears on both rails — beside the R32 match it feeds on that side.
        for letter in sorted(group_feeds):
            gf = group_feeds[letter]
            rows = standings.get(letter, [])
            for place in ('1', '2'):
                t = gf.get(place)
                if not t:
                    continue
                entry = {'letter': letter, 'rows': rows, 'target': t, 'place': place}
                if t in left_nums:
                    left_rail.append(entry)
                elif t in right_nums:
                    right_rail.append(entry)
        left_rail.sort(key=lambda e: leaf_index.get(e['target'], 0))
        right_rail.sort(key=lambda e: leaf_index.get(e['target'], 0))

    return render_template('bracket.html', cols=cols, left=left, right=right,
                           left_rail=left_rail, right_rail=right_rail)


@app.route('/map')
def map_view():
    return render_template('map.html', start=config.TOURNAMENT_START,
                           end=config.TOURNAMENT_END,
                           owm_key=config.OPENWEATHER_API_KEY)


@app.route('/schedule-map')
def schedule_map():
    return render_template('schedulemap.html', start=config.TOURNAMENT_START,
                           end=config.TOURNAMENT_END)


@app.route('/team-map')
def team_map():
    return render_template('teammap.html', start=config.TOURNAMENT_START,
                           end=config.TOURNAMENT_END)


# ── JSON API ───────────────────────────────────────────────────────────────

@app.route('/api/venues')
def api_venues():
    conn = db.connect()
    venues = [dict(v) for v in conn.execute("SELECT * FROM venues")]
    conn.close()
    return jsonify(venues)


@app.route('/api/teams')
def api_teams():
    """All 48 teams with flag code and group letter, for the follow-a-team picker."""
    conn = db.connect()
    rows = conn.execute(
        "SELECT name, group_letter FROM teams ORDER BY group_letter, name").fetchall()
    conn.close()
    return jsonify([
        {'name': r['name'], 'group': r['group_letter'], 'code': flag_code(r['name'])}
        for r in rows
    ])


@app.route('/api/matches')
def api_matches():
    conn = db.connect()
    venues = _venues_map(conn)
    d = request.args.get('date')
    if d:
        rows = conn.execute(
            "SELECT * FROM matches WHERE date=? ORDER BY utc_datetime", (d,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM matches ORDER BY utc_datetime").fetchall()
    out = [_match_dict(m, venues) for m in rows]
    conn.close()
    return jsonify(out)


@app.route('/api/weather')
def api_weather():
    """Per-match kickoff weather for a given date (forecast / current / historical)."""
    d = request.args.get('date')
    if not d:
        return jsonify({})
    conn = db.connect()
    data = weather.weather_for_date(conn, d)
    conn.close()
    return jsonify(data)


@app.route('/api/alerts')
def api_alerts():
    """Live weather advisories near the host venues (GeoJSON)."""
    conn = db.connect()
    fc = alerts.active_alerts(conn)
    conn.close()
    return jsonify(fc)


@app.route('/api/live')
def api_live():
    """Matches currently in play (live score + state), for the site-wide ticker."""
    conn = db.connect()
    data = live.live_matches(conn)
    conn.close()
    return jsonify({'matches': data, 'now': datetime.utcnow().isoformat() + 'Z'})


# Title odds come from a Monte-Carlo sim that's too costly to run per request, so
# cache them briefly; phase + today's matches are cheap SQL and stay fresh.
_LANDING_ODDS = {'odds': None, 'ts': 0.0}
_LANDING_ODDS_TTL = 600  # seconds


def _landing_title_odds(conn):
    now = time.time()
    if _LANDING_ODDS['odds'] is None or now - _LANDING_ODDS['ts'] > _LANDING_ODDS_TTL:
        _LANDING_ODDS['odds'] = publish_pages._title_odds(conn)
        _LANDING_ODDS['ts'] = now
    return _LANDING_ODDS['odds']


@app.route('/api/landing')
def api_landing():
    """Live landing-page payload for the GitHub Pages site: current phase, today's
    matches (with live scores), and cached title odds. CORS-open so the .io page
    can poll the droplet directly instead of reading a git-committed snapshot."""
    conn = db.connect()
    payload = {
        'phase': publish_pages._phase(conn),
        'today': publish_pages._today(conn),
        'title_odds': _landing_title_odds(conn),
        'live_url': 'https://droplet.josephborrello.com/worldcup/',
        'generated': datetime.utcnow().isoformat() + 'Z',
    }
    conn.close()
    resp = jsonify(payload)
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Cache-Control'] = 'public, max-age=120'
    return resp


@app.route('/api/days')
def api_days():
    """All tournament dates that have at least one match, with counts."""
    conn = db.connect()
    rows = conn.execute(
        "SELECT date, COUNT(*) n FROM matches GROUP BY date ORDER BY date"
    ).fetchall()
    conn.close()
    return jsonify([{'date': r['date'], 'count': r['n']} for r in rows])


@app.route('/api/standings')
def api_standings():
    conn = db.connect()
    data = _standings_by_group(conn)
    conn.close()
    return jsonify(data)


@app.route('/api/bracket')
def api_bracket():
    conn = db.connect()
    venues = _venues_map(conn)
    rows = conn.execute(
        "SELECT * FROM matches WHERE stage='knockout' ORDER BY num").fetchall()
    conn.close()
    return jsonify([_match_dict(m, venues) for m in rows])


# ── predictions ──────────────────────────────────────────────────────────────
DEPTH_ORDER = ['r32', 'r16', 'qf', 'sf', 'final']


def _depth_rounds(depth):
    depth = depth if depth in DEPTH_ORDER else 'final'
    allowed = set(DEPTH_ORDER[:DEPTH_ORDER.index(depth) + 1])
    if depth == 'final':
        allowed.add('third')
    return allowed


@app.route('/api/predictions')
def api_predictions():
    conn = db.connect()
    data = predict.predictions(conn)
    conn.close()
    # per-team odds + flag code + meta (projected slots live on /api/bracket/predicted)
    teams = {t: {**v, 'code': flag_code(t)} for t, v in data['teams'].items()}
    return jsonify({'sims': data['sims'], 'n_finished': data['n_finished'],
                    'generated': data['generated'], 'teams': teams})


def _parse_overrides(raw):
    """Parse the ?overrides= query param (JSON object {match_num: team}) into a
    {int: str} mapping. Anything malformed is ignored — the engine further drops
    any override whose team isn't actually in that match, so bad input is safe."""
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(obj, dict):
        return {}
    out = {}
    for k, v in obj.items():
        try:
            out[int(k)] = str(v)
        except (TypeError, ValueError):
            continue
    return out


@app.route('/api/bracket/predicted')
def api_bracket_predicted():
    """Projected (most-likely) team per knockout slot, up to a chosen depth.

    Accepts an optional ?overrides=<json {match_num: forced_winner}> for
    interactive "what-if" manipulation: a forced winner advances and every later
    slot it feeds is re-resolved to honor the change. The applied overrides are
    echoed back so the client can drop any that no longer take effect.
    """
    depth = request.args.get('depth', 'final')
    allowed = _depth_rounds(depth)
    overrides = _parse_overrides(request.args.get('overrides'))
    conn = db.connect()
    data = predict.projected_bracket(conn, overrides)
    conn.close()

    def side(s):                       # fresh copy + flag code (don't mutate cache)
        return None if not s else {'team': s['team'], 'conf': s['conf'],
                                   'code': flag_code(s['team'])}
    slots = {num: {'round': e['round'], 'team1': side(e['team1']), 'team2': side(e['team2'])}
             for num, e in data['slots'].items() if e['round'] in allowed}
    return jsonify({'depth': depth, 'n_finished': data['n_finished'], 'slots': slots,
                    'overrides': data['overrides']})


@app.route('/api/pundits')
def api_pundits():
    """MiroFish-inspired AI pundit panel for a scope ('group:A' | 'knockout')."""
    scope = request.args.get('scope', 'knockout')
    conn = db.connect()
    data = pundits.panel(conn, scope)
    conn.close()
    return jsonify(data)


@app.route('/api/pundits/budget')
def api_pundits_budget():
    """Current pundit usage vs. the daily cap and monthly $ budget."""
    conn = db.connect()
    bs = pundits.budget_status(conn)
    conn.close()
    bs['enabled'] = bool(config.ANTHROPIC_API_KEY)
    return jsonify(bs)


@app.route('/predictions')
def predictions_page():
    return render_template('predictions.html')


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=config.PORT, debug=True)
