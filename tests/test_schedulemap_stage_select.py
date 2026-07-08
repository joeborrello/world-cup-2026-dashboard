"""Tests for the all-games map stage selector (JOE-44).

The schedule map's stage filter used to be a sidebar of checkboxes that were all
ticked by default — "selecting" the semi-finals meant unticking five boxes, and
on phones the sidebar sat below the fold. JOE-44 replaces it with a stage-chip
bar above the map: clicking a chip while everything is shown isolates that
stage, further clicks add/remove stages, and the selection round-trips through
a ?stages= query parameter so a filtered view is shareable.

These tests pin the rendered markup, the JS wiring/behavioural markers, the CSS
for the chips, and — most importantly — that the JS stage buckets stay in sync
with the round labels actually present in the seeded database.
"""

import json
import os
import re

import pytest

import app as flask_app

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)


@pytest.fixture
def client():
    flask_app.app.config['TESTING'] = True
    with flask_app.app.test_client() as c:
        yield c


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as fh:
        return fh.read()


def _js():
    return _read('static', 'js', 'schedulemap.js')


def _stage_defs():
    """Parse the STAGES array out of schedulemap.js → list of dicts."""
    js = _js()
    block = re.search(r'const STAGES = \[(.*?)\];', js, re.S)
    assert block, 'STAGES definition should exist in schedulemap.js'
    defs = []
    for entry in re.finditer(r'\{([^}]*)\}', block.group(1)):
        d = dict(re.findall(r"(\w+): '([^']*)'", entry.group(1)))
        defs.append(d)
    return defs


# ── rendered template ────────────────────────────────────────────────────────

def test_schedule_map_renders(client):
    assert client.get('/schedule-map').status_code == 200


def test_stage_chip_bar_present(client):
    html = client.get('/schedule-map').get_data(as_text=True)
    assert 'id="stageChips"' in html
    assert 'id="stageSummary"' in html


def test_chip_bar_is_labelled_for_accessibility(client):
    html = client.get('/schedule-map').get_data(as_text=True)
    m = re.search(r'id="stageChips"[^>]*|[^>]*id="stageChips"', html)
    chip_tag = re.search(r'<div[^>]*id="stageChips"[^>]*>', html)
    assert chip_tag
    assert 'role="group"' in chip_tag.group(0)
    assert 'aria-label' in chip_tag.group(0)


def test_old_checkbox_sidebar_gone(client):
    """The unticking-five-boxes UX is replaced, not duplicated."""
    html = client.get('/schedule-map').get_data(as_text=True)
    assert 'stageFilter' not in html
    assert 'Filter by stage' not in html
    assert 'schedLegend' not in html


def test_hero_invites_stage_selection(client):
    html = client.get('/schedule-map').get_data(as_text=True)
    assert 'one or more' in html


# ── JS ↔ data consistency ────────────────────────────────────────────────────

def _bucket(m):
    """Python mirror of schedulemap.js bucket()."""
    if m['group']:
        return 'group'
    if m['round'] in ('Final', 'Match for third place'):
        return 'final'
    return m['round']


def test_every_match_falls_in_a_selectable_stage(client):
    """Each of the 104 matches must bucket into one of the JS stage chips —
    otherwise selecting stages would silently drop matches from the map."""
    matches = json.loads(client.get('/api/matches').get_data(as_text=True))
    assert len(matches) == 104
    keys = {d['key'] for d in _stage_defs()}
    buckets = {_bucket(m) for m in matches}
    assert buckets <= keys, f'unmapped stage buckets: {buckets - keys}'


def test_every_stage_chip_has_matches(client):
    """No chip should be a dead toggle that can never light up a venue."""
    matches = json.loads(client.get('/api/matches').get_data(as_text=True))
    buckets = {_bucket(m) for m in matches}
    keys = {d['key'] for d in _stage_defs()}
    assert keys <= buckets, f'chips with zero matches: {keys - buckets}'


def test_stage_slugs_are_unique_and_url_safe():
    slugs = [d['slug'] for d in _stage_defs()]
    assert len(slugs) == 6
    assert len(set(slugs)) == len(slugs)
    assert all(re.fullmatch(r'[a-z0-9]+', s) for s in slugs)


# ── JS wiring ────────────────────────────────────────────────────────────────

def test_js_builds_chips_not_checkboxes():
    js = _js()
    assert "getElementById('stageChips')" in js
    assert 'aria-pressed' in js
    assert 'checkbox' not in js


def test_js_isolates_stage_on_first_click():
    """Clicking a chip while nothing is filtered selects ONLY that stage — the
    one-click answer to "where are the semi-finals played?"."""
    js = _js()
    m = re.search(r'function onChipClick\(key\) \{(.*?)\n  \}', js, re.S)
    assert m, 'onChipClick should exist'
    body = m.group(1)
    assert 'allOn()' in body
    assert 'active.clear()' in body       # isolate: drop everything else...
    assert 'active.add(key)' in body      # ...keep just the clicked stage
    assert 'active.delete(key)' in body   # later clicks toggle stages off


def test_js_empty_selection_falls_back_to_all_stages():
    """Deselecting the last stage must not strand the user on an empty map."""
    js = _js()
    assert 'selectAll()' in js
    assert re.search(r'if \(!active\.size\) selectAll\(\)', js)


def test_js_has_show_all_reset_chip():
    js = _js()
    assert 'stageAll' in js
    assert 'Show all stages' in js


def test_js_selection_round_trips_through_url():
    js = _js()
    # read ?stages= on load...
    assert re.search(r"URLSearchParams\(location\.search\)\.get\('stages'\)", js)
    # ...and write it back (without reloading) as the selection changes
    assert 'history.replaceState' in js
    assert re.search(r"searchParams\.set\('stages'", js)
    assert re.search(r"searchParams\.delete\('stages'\)", js)


def test_js_summary_reports_matches_and_venues():
    js = _js()
    assert "getElementById('stageSummary')" in js
    assert 'venues' in js


# ── CSS ──────────────────────────────────────────────────────────────────────

def test_css_styles_stage_chips():
    css = _read('static', 'css', 'style.css')
    assert '.stage-chips' in css
    assert '.stage-chip' in css
    assert '.stage-chip.off' in css
    assert '.stage-chip[aria-pressed="true"]' in css


def test_css_old_sidebar_rules_removed():
    css = _read('static', 'css', 'style.css')
    assert '.stage-filter' not in css
    assert '.sched-side' not in css
    assert '.sched-layout' not in css
