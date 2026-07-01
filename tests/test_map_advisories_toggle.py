"""The daily map's async overlay toggles must respect the checkbox (JOE-34).

Ticking "Advisories" starts a fetch of `/api/alerts` — a slow call, since the
server polls NWS + Environment Canada. The old handler added the layer whenever
that fetch landed, regardless of the checkbox's *current* state. So the
reported symptom: tick (nothing appears yet), untick, and the layer pops up
*after* the untick — orphaned, because the untick branch only removes the layer
it knows about (`advisLayer`), which a later tick overwrites with a second
copy. The overlay "never goes away". The radar toggle had the identical race
around the RainViewer catalogue fetch.

The fix: when an in-flight fetch lands, bail unless the box is still ticked and
no competing fetch has already added the layer.

These tests run the real `map.js` in node under a stubbed DOM/Leaflet/fetch,
replay the tick → untick → fetch-lands sequence, and count the layers actually
sitting on the map at each step.
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
# elements that record listeners, a Leaflet whose map tracks its layer set, and
# a fetch whose resolution the scenario script controls. Each scenario replays
# a toggle sequence and reports the number of matching layers on the map after
# each step.
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
els.layRadar = makeEl(); els.layRadar.parentElement = makeEl();
els.layTemp = makeEl(); els.layTemp.parentElement = makeEl();
els.layIsobars = makeEl(); els.layIsobars.parentElement = makeEl();
els.layAdvis = makeEl(); els.layAdvis.parentElement = makeEl();
global.document = {
  getElementById: byId,
  createElement: () => makeEl(),
  addEventListener: noop,
  querySelectorAll: () => [],
};
global.localStorage = { getItem: () => null, setItem: noop };

// ── Leaflet: the map is just a set of layers ────────────────────────────────
const mapLayers = new Set();
const mapObj = {
  setView() { return this; },
  removeLayer: l => mapLayers.delete(l),
  fitBounds: noop,
};
const layerOf = kind => ({ kind, addTo() { mapLayers.add(this); return this; } });
global.L = {
  map: () => mapObj,
  tileLayer: url => layerOf('tile:' + url),
  geoJSON: () => layerOf('advisories'),
  divIcon: () => ({}),
  marker: () => ({ addTo: () => ({ bindPopup: noop }) }),
  circleMarker: () => ({}),
};
const count = prefix => [...mapLayers].filter(l => l.kind.startsWith(prefix)).length;

// ── fetch the scenario resolves by hand ─────────────────────────────────────
const pending = {};
global.fetch = url => new Promise(res => { (pending[url] = pending[url] || []).push(res); });
const land = (url, data) =>
  (pending[url] || []).splice(0).forEach(res => res({ json: () => Promise.resolve(data) }));
const flush = () => new Promise(r => setImmediate(r));

// ── page globals map.js expects ─────────────────────────────────────────────
global.window = {
  WC: { matchesUrl: '/m', weatherUrl: '/w', venuesUrl: '/v', daysUrl: '/d',
        alertsUrl: '/alerts', owmKey: '' },
};
global.WCDay = { today: () => '2026-07-01' };
global.WCWx = { chip: () => '', line: () => '', unit: 'C', setUnit: noop };
global.WCTime = { time: () => '', datetime: () => '', tz: '' };
global.wcFlag = () => '';

const ALERT = { features: [{ type: 'Feature', properties: { color: '#f00', severity: 'Severe' },
                             geometry: { type: 'Point', coordinates: [0, 0] } }] };
const CATALOGUE = { host: 'https://rv.example', radar: { past: [{ path: '/latest' }] } };

(async () => {
  eval(src);
  const out = [];
  const step = (label, prefix) => out.push([label, count(prefix)]);

  if (scenario === 'advis-untick-during-fetch') {
    const cb = els.layAdvis;
    cb.checked = true; cb.dispatch('change');           // tick: alerts fetch in flight
    cb.checked = false; cb.dispatch('change');          // untick before it lands
    land('/alerts', ALERT); await flush(); await flush();
    step('after late fetch, box unticked', 'advisories');
    cb.checked = true; cb.dispatch('change'); await flush();
    step('re-ticked', 'advisories');
    cb.checked = false; cb.dispatch('change');
    step('unticked again', 'advisories');
  }

  if (scenario === 'advis-double-fetch') {
    const cb = els.layAdvis;
    cb.checked = true; cb.dispatch('change');           // first fetch in flight
    cb.checked = false; cb.dispatch('change');
    cb.checked = true; cb.dispatch('change');           // second fetch in flight
    land('/alerts', ALERT); await flush(); await flush(); // both land, box ticked
    step('both fetches landed, box ticked', 'advisories');
    cb.checked = false; cb.dispatch('change');
    step('unticked', 'advisories');
  }

  if (scenario === 'radar-untick-during-fetch') {
    const cb = els.layRadar;
    const radar = 'tile:' + CATALOGUE.host + CATALOGUE.radar.past[0].path;
    cb.checked = true; cb.dispatch('change');           // catalogue fetch in flight
    cb.checked = false; cb.dispatch('change');
    land('https://api.rainviewer.com/public/weather-maps.json', CATALOGUE);
    await flush(); await flush();
    step('after late fetch, box unticked', radar);
    cb.checked = true; cb.dispatch('change'); await flush();
    step('re-ticked', radar);
    cb.checked = false; cb.dispatch('change');
    step('unticked again', radar);
  }

  process.stdout.write(JSON.stringify(out));
})();
"""


def _run(scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to exercise map.js")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(HARNESS)
        harness_path = fh.name
    try:
        out = subprocess.run([node, harness_path, MAP_JS, scenario],
                             capture_output=True, text=True, timeout=30)
    finally:
        os.unlink(harness_path)
    assert out.returncode == 0, out.stderr
    return dict(json.loads(out.stdout))


def test_advisories_fetch_landing_after_untick_adds_nothing():
    """The reported bug: tick, untick, then the slow alerts fetch lands.

    The layer must NOT appear (the box is unticked), and subsequent toggles
    must add exactly one copy and remove it cleanly — no orphan.
    """
    steps = _run("advis-untick-during-fetch")
    assert steps["after late fetch, box unticked"] == 0
    assert steps["re-ticked"] == 1
    assert steps["unticked again"] == 0


def test_advisories_competing_fetches_add_a_single_layer():
    """Tick → untick → tick before the first fetch lands: two fetches race.

    Only one layer may end up on the map, and unticking must remove it.
    """
    steps = _run("advis-double-fetch")
    assert steps["both fetches landed, box ticked"] == 1
    assert steps["unticked"] == 0


def test_radar_has_the_same_guard():
    """The radar toggle races its RainViewer catalogue fetch identically."""
    steps = _run("radar-untick-during-fetch")
    assert steps["after late fetch, box unticked"] == 0
    assert steps["re-ticked"] == 1
    assert steps["unticked again"] == 0
