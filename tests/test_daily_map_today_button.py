"""The daily map's TODAY button jumps back to the device's current day (JOE-36).

The map boots on the device's current day, but once the viewer scrolls off it
(slider, ‹/› buttons) the only way back was to hunt for it manually. The TODAY
button snaps the slider back: to the current day if it has matches, else the
next match day (rest day), else — tournament over — the last day. It re-reads
`WCDay.today()` on every click rather than the load-time constant, so a tab
left open across the 2am rollover jumps to the day it is *now*. While the map
is already showing today's slate the button is disabled.

These tests run the real `map.js` in node under a stubbed DOM/Leaflet/fetch,
boot it against a fixed day list, replay slider moves and button clicks, and
check where the slider actually lands.
"""

import json
import os
import shutil
import subprocess
import tempfile

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP_JS = os.path.join(BASE, "static", "js", "map.js")

# Node harness: eval the real map.js with just enough browser stubbed in —
# elements that record listeners, a no-op Leaflet, and a hand-resolved fetch.
# Each scenario boots the map on a fixed day list, drives the controls, and
# reports the slider position / button state after each step.
HARNESS = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const scenario = process.argv[3];
const noop = () => {};

// ── DOM ──────────────────────────────────────────────────────────────────────
function makeEl() {
  return {
    hidden: false, checked: false, disabled: false, value: '', max: 0,
    textContent: '', innerHTML: '', dataset: {}, title: '',
    listeners: {},
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
    dispatch(type) { (this.listeners[type] || []).forEach(fn => fn()); },
    querySelectorAll: () => [],
    classList: { add: noop, remove: noop, toggle: noop },
    appendChild: noop,
  };
}
const els = {};
const byId = id => (els[id] = els[id] || makeEl());
['layRadar', 'layTemp', 'layIsobars', 'layAdvis'].forEach(id => {
  els[id] = makeEl(); els[id].parentElement = makeEl();
});
global.document = {
  getElementById: byId,
  createElement: () => makeEl(),
  addEventListener: noop,
  querySelectorAll: () => [],
};
global.localStorage = { getItem: () => null, setItem: noop };

// ── Leaflet: enough to boot and render markers ──────────────────────────────
const mapObj = { setView() { return this; }, removeLayer: noop, fitBounds: noop };
global.L = {
  map: () => mapObj,
  tileLayer: () => ({ addTo: () => ({}) }),
  geoJSON: () => ({ addTo: () => ({}) }),
  divIcon: () => ({}),
  marker: () => ({ addTo() { return this; }, bindPopup: noop, getLatLng: () => ({ lat: 0, lng: 0 }) }),
  circleMarker: () => ({}),
};

// ── fetch the scenario resolves by hand ─────────────────────────────────────
const pending = {};
global.fetch = url => new Promise((res, rej) => { (pending[url] = pending[url] || []).push({ res, rej }); });
const land = (url, data) =>
  (pending[url] || []).splice(0).forEach(p => p.res({ json: () => Promise.resolve(data) }));
const flush = () => new Promise(r => setImmediate(r));

// ── page globals map.js expects ─────────────────────────────────────────────
global.window = {
  WC: { matchesUrl: '/m', weatherUrl: '/w', venuesUrl: '/v', daysUrl: '/d',
        alertsUrl: '/alerts', owmKey: '' },
};
// mutable so a scenario can simulate the 2am rollover while the tab sits open
let deviceToday = '2026-06-20';
global.WCDay = { today: () => deviceToday };
global.WCWx = { chip: () => '', line: () => '', unit: 'C', setUnit: noop };
global.WCTime = { time: () => '', datetime: () => '', tz: '' };
global.wcFlag = () => '';

// four straight match days, a rest-day gap, then the final day
const DAYS = ['2026-06-18', '2026-06-19', '2026-06-20', '2026-06-21', '2026-06-25']
  .map(date => ({ date, count: 2 }));

(async () => {
  eval(src);
  land('/v', []); land('/d', DAYS);
  await flush(); await flush();

  const slider = byId('daySlider'), btn = byId('todayBtn');
  const out = [];
  const step = label => out.push([label, { at: +slider.value, disabled: btn.disabled }]);
  const move = i => { slider.value = i; slider.dispatch('input'); };

  step('boot');

  if (scenario === 'jump-back') {
    move(0); step('moved to first day');
    btn.dispatch('click'); step('clicked TODAY');
  }

  if (scenario === 'rest-day') {          // deviceToday sits in the gap
    move(4);
    btn.dispatch('click'); step('clicked TODAY');
  }

  if (scenario === 'tournament-over') {   // deviceToday after the last day
    move(1);
    btn.dispatch('click'); step('clicked TODAY');
  }

  if (scenario === 'rollover-while-open') {
    move(0);
    deviceToday = '2026-06-21';           // 2am passes with the tab open
    btn.dispatch('click'); step('clicked TODAY after rollover');
  }

  process.stdout.write(JSON.stringify(out));
})();
"""


def _run(scenario, device_today=None):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to exercise map.js")
    harness = HARNESS
    if device_today:
        harness = harness.replace("let deviceToday = '2026-06-20';",
                                  f"let deviceToday = '{device_today}';")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(harness)
        harness_path = fh.name
    try:
        out = subprocess.run([node, harness_path, MAP_JS, scenario],
                             capture_output=True, text=True, timeout=30)
    finally:
        os.unlink(harness_path)
    assert out.returncode == 0, out.stderr
    return dict(json.loads(out.stdout))


def test_today_button_jumps_back_to_the_current_day():
    """Scroll away, click TODAY: the slider snaps back to the device's day.

    The button reads disabled while the map is on today and re-enables once
    the viewer moves off it.
    """
    steps = _run("jump-back")
    assert steps["boot"] == {"at": 2, "disabled": True}          # opens on today
    assert steps["moved to first day"] == {"at": 0, "disabled": False}
    assert steps["clicked TODAY"] == {"at": 2, "disabled": True}


def test_today_on_a_rest_day_lands_on_the_next_match_day():
    """No matches today (mid-tournament rest day) → the next match day."""
    steps = _run("rest-day", device_today="2026-06-23")
    assert steps["clicked TODAY"]["at"] == 4                     # 06-25, not 06-21


def test_today_after_the_tournament_lands_on_the_final_day():
    steps = _run("tournament-over", device_today="2026-07-30")
    assert steps["boot"]["at"] == 4                              # boot falls back too
    assert steps["clicked TODAY"] == {"at": 4, "disabled": True}


def test_today_is_recomputed_at_click_time_not_page_load():
    """A tab open across the 2am rollover must jump to the *current* day.

    The map booted when today was Jun 20; by click time the device has rolled
    to Jun 21. The stale load-time constant would land on 20 — the button must
    land on 21.
    """
    steps = _run("rollover-while-open")
    assert steps["boot"]["at"] == 2                              # booted on Jun 20
    assert steps["clicked TODAY after rollover"] == {"at": 3, "disabled": True}


def test_map_page_has_the_today_button():
    """The template ships the button the script wires up."""
    with open(os.path.join(BASE, "templates", "map.html"), encoding="utf-8") as fh:
        html = fh.read()
    assert 'id="todayBtn"' in html
