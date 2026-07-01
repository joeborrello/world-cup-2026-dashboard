"""The daily map's "today" rolls over at 2am local, like the landing page (JOE-32).

The daily map exposes *live* weather options — radar, isobars and advisories —
only while the selected day is "today", and it opens on that day. Both used
`new Date().toISOString().slice(0, 10)`, i.e. the browser's **UTC** calendar
date. For a viewer in the Americas, UTC midnight lands in the early evening
(≈5pm PT / 8pm ET), so "today" flipped to *tomorrow* while that day's matches
were still being played — the live weather options disappeared and the slider
jumped forward hours too early.

The landing-page live scores (`today.js`) already roll over at **2am local**:
a match kicking off between 00:00 and 01:59 still belongs to the previous day's
slate. JOE-32 makes the daily map use that same rule via the shared `WCDay`
helper, so everything swaps over at 2am local instead.

These tests exercise the real `WCDay` from `util.js` under a fixed US timezone,
and pin the map/landing-page wiring at the source level.
"""

import json
import os
import shutil
import subprocess
import tempfile

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UTIL = os.path.join(BASE, "static", "js", "util.js")


def _read(*parts):
    with open(os.path.join(BASE, *parts), encoding="utf-8") as fh:
        return fh.read()


# Node harness: load the real util.js under a stubbed browser environment, then
# report WCDay.key(...) for each supplied UTC instant. Run with TZ pinned so the
# local-calendar rollover is deterministic regardless of the host machine.
HARNESS = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const noop = () => {};
global.window = {};
global.document = { addEventListener: noop, querySelectorAll: () => [] };
global.localStorage = { getItem: () => null, setItem: noop };
eval(src);
const WCDay = global.window.WCDay;
const instants = JSON.parse(process.argv[3]);
const keys = instants.map(iso => WCDay.key(new Date(iso)));
process.stdout.write(JSON.stringify({ rollover: WCDay.ROLLOVER_HOURS, keys }));
"""


def _keys(instants, tz="America/New_York"):
    """WCDay.key(...) for each UTC instant, evaluated with the host TZ pinned."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to exercise WCDay from util.js")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(HARNESS)
        harness_path = fh.name
    try:
        env = dict(os.environ, TZ=tz)
        out = subprocess.run(
            [node, harness_path, UTIL, json.dumps(instants)],
            capture_output=True, text=True, timeout=30, env=env)
    finally:
        os.unlink(harness_path)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# ── the 2am-local rollover (real WCDay under US Eastern) ──────────────────────

def test_rollover_is_two_hours():
    assert _keys([])["rollover"] == 2


def test_midnight_to_2am_still_counts_as_the_previous_day():
    """00:00–01:59 ET on Jun 30 still belongs to Jun 29's slate."""
    # 05:30Z = 01:30 ET (Jun 30, EDT = UTC-4) → still the previous day.
    assert _keys(["2026-06-30T05:30:00Z"])["keys"] == ["2026-06-29"]


def test_after_2am_rolls_to_the_new_day():
    """02:00 ET is the cutover: from there it's the new day's slate."""
    # 06:30Z = 02:30 ET (Jun 30) → rolled over to Jun 30.
    assert _keys(["2026-06-30T06:30:00Z"])["keys"] == ["2026-06-30"]


def test_evening_stays_on_the_same_day_unlike_utc():
    """The regression: a normal ET evening must NOT read as tomorrow.

    22:00 ET on Jun 30 is 02:00Z Jul 1 — the old UTC slice returned '2026-07-01'
    (tomorrow) and hid the live weather options mid-evening. WCDay keeps it on
    Jun 30 until 2am ET.
    """
    instant = "2026-07-01T02:00:00Z"  # 22:00 ET, Jun 30
    assert instant[:10] == "2026-07-01"           # what toISOString().slice gave
    assert _keys([instant])["keys"] == ["2026-06-30"]  # what WCDay gives


# ── source wiring: map + landing page share the one rollover helper ──────────

def test_util_defines_shared_wcday_helper():
    js = _read("static", "js", "util.js")
    assert "window.WCDay" in js
    assert "ROLLOVER_HOURS = 2" in js


def test_map_uses_wcday_today_not_utc_slice():
    """The daily map keys its live overlays + default day off WCDay, not UTC."""
    js = _read("static", "js", "map.js")
    assert "WCDay.today()" in js
    # the buggy UTC-date computation must be gone
    assert "toISOString().slice(0, 10)" not in js


def test_landing_page_uses_the_same_helper():
    """today.js drives its slate off the same WCDay helper (single source)."""
    js = _read("static", "js", "today.js")
    assert "WCDay.key" in js
    assert "WCDay.today()" in js
