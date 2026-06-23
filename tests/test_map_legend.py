"""Tests for the daily-map weather legends (temperature + isobar/pressure).

JOE-5 adds an isobar (pressure) colour-bar key alongside the existing
temperature key. These tests pin down the rendered markup, the CSS palette and
the JS wiring so the two legends stay consistent and the temp legend is left
untouched.
"""

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


# ── rendered template ────────────────────────────────────────────────────────

def test_map_page_renders(client):
    resp = client.get('/map')
    assert resp.status_code == 200


def test_isobar_legend_present_in_markup(client):
    html = client.get('/map').get_data(as_text=True)
    # the isobar legend container, its caption and its ticks placeholder
    assert 'id="isoLegend"' in html
    assert 'id="isoCap"' in html
    assert 'id="isoTicks"' in html


def test_isobar_legend_uses_hpa_units(client):
    html = client.get('/map').get_data(as_text=True)
    assert 'hPa' in html


def test_isobar_legend_matches_temp_legend_structure(client):
    """Both legends share the .temp-legend base class + tl-bar/tl-scale/tl-ticks
    treatment, so they render at the same size/placement/styling."""
    html = client.get('/map').get_data(as_text=True)
    iso = re.search(r'<div class="[^"]*\biso-legend\b[^"]*"[^>]*id="isoLegend"', html)
    assert iso, 'iso legend should reuse the temp-legend base class'
    assert 'temp-legend' in iso.group(0)
    # same inner scaffolding as the temperature legend
    assert html.count('class="tl-scale"') >= 2
    assert html.count('<span class="tl-bar">') >= 2


def test_temp_legend_still_present(client):
    """The existing temperature legend must be unchanged/untouched."""
    html = client.get('/map').get_data(as_text=True)
    assert 'id="tempLegend"' in html
    assert 'id="tempCap"' in html
    assert 'id="tempTicks"' in html


# ── CSS palette ──────────────────────────────────────────────────────────────

def test_css_defines_isobar_bar_gradient():
    css = _read('static', 'css', 'style.css')
    assert '.iso-legend .tl-bar' in css
    # an isobar-specific gradient distinct from the temperature one
    iso_block = css.split('.iso-legend .tl-bar', 1)[1].split('}', 1)[0]
    assert 'linear-gradient' in iso_block


def test_temp_css_unchanged():
    css = _read('static', 'css', 'style.css')
    # the temperature bar keeps its original palette anchors
    assert 'rgb(35,221,221) 0%' in css


# ── JS wiring ────────────────────────────────────────────────────────────────

def test_js_renders_and_toggles_isobar_legend():
    js = _read('static', 'js', 'map.js')
    assert 'renderIsoLegend' in js
    assert "getElementById('isoLegend')" in js
    # legend is revealed when the isobar layer turns on...
    assert 'isoLegend.hidden = false' in js
    # ...and hidden again when overlays are cleared
    assert 'isoLegend.hidden = true' in js


def test_js_isobar_anchors_cover_pressure_range():
    js = _read('static', 'js', 'map.js')
    m = re.search(r'PRESSURE_ANCHORS_HPA\s*=\s*\[([^\]]+)\]', js)
    assert m, 'pressure anchors should be defined'
    anchors = [int(x) for x in m.group(1).split(',')]
    # sane, ascending sea-level pressure values (hPa)
    assert anchors == sorted(anchors)
    assert min(anchors) < 980 < max(anchors)
    assert all(900 <= a <= 1100 for a in anchors)
