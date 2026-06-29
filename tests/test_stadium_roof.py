"""Tests for the open-vs-covered stadium note on the daily map (JOE-20).

Each host venue carries a ``roof`` type — open / retractable / fixed — and the
daily map shows a note next to every stadium saying whether it's open-air or
covered. These tests pin down:
  * the venue data (every venue classified, with the right kind of roof),
  * the DB column + the startup migration that backfills it on an old DB,
  * the JSON the map fetches (/api/venues and /api/matches both carry roof),
  * the map's front-end wiring (helper, popup, list row) and CSS.
"""

import os
import sqlite3

import pytest

import app as flask_app
import db
from venues import VENUES

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)

ROOF_TYPES = {"open", "retractable", "fixed"}
COVERED_TYPES = {"retractable", "fixed"}

# A few well-known classifications, so a future data edit that flips one is caught.
KNOWN_COVERED = {
    "Atlanta",                    # Mercedes-Benz Stadium — retractable
    "Dallas (Arlington)",         # AT&T Stadium — retractable
    "Houston",                    # NRG Stadium — retractable
    "Los Angeles (Inglewood)",    # SoFi Stadium — fixed roof
    "Vancouver",                  # BC Place — retractable
}
KNOWN_OPEN = {
    "Kansas City",                # Arrowhead Stadium
    "New York/New Jersey (East Rutherford)",  # MetLife Stadium
    "Mexico City",                # Estadio Azteca
}


@pytest.fixture
def client():
    flask_app.app.config['TESTING'] = True
    with flask_app.app.test_client() as c:
        yield c


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as fh:
        return fh.read()


# ── venue data ───────────────────────────────────────────────────────────────

def test_every_venue_has_a_known_roof_type():
    for ground, v in VENUES.items():
        assert v.get("roof") in ROOF_TYPES, f"{ground} has bad roof {v.get('roof')!r}"


def test_known_covered_and_open_venues_classified_correctly():
    for ground in KNOWN_COVERED:
        assert VENUES[ground]["roof"] in COVERED_TYPES, f"{ground} should be covered"
    for ground in KNOWN_OPEN:
        assert VENUES[ground]["roof"] == "open", f"{ground} should be open-air"


def test_both_open_and_covered_venues_exist():
    roofs = {v["roof"] for v in VENUES.values()}
    assert "open" in roofs
    assert roofs & COVERED_TYPES


# ── DB column + API ──────────────────────────────────────────────────────────

def test_venues_schema_has_roof_column():
    conn = db.connect()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(venues)")]
    conn.close()
    assert "roof" in cols


def test_api_venues_includes_roof_for_every_venue(client):
    venues = client.get('/api/venues').get_json()
    assert venues, "expected seeded venues"
    for v in venues:
        assert v.get("roof") in ROOF_TYPES, f"{v.get('ground')} -> {v.get('roof')!r}"


def test_api_matches_carry_their_venue_roof(client):
    matches = client.get('/api/matches').get_json()
    assert matches, "expected seeded matches"
    for m in matches:
        # every match's ground is a known venue, so roof must be populated
        assert m.get("roof") in ROOF_TYPES, f"match {m.get('num')} -> {m.get('roof')!r}"
        assert m["roof"] == VENUES[m["ground"]]["roof"]


# ── startup migration (old DB with no roof column) ───────────────────────────

def test_migration_adds_and_backfills_roof_on_old_db(tmp_path):
    """A DB seeded before this feature has no roof column; init_schema must add
    it and backfill the static roof type without a full reseed."""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # the pre-JOE-20 venues table (no roof column), with two real grounds
    conn.execute("""CREATE TABLE venues (
        ground TEXT PRIMARY KEY, stadium TEXT, city TEXT, country TEXT,
        lat REAL, lng REAL, tz TEXT)""")
    conn.execute("INSERT INTO venues (ground, stadium) VALUES (?, ?)",
                 ("Atlanta", "Mercedes-Benz Stadium"))
    conn.execute("INSERT INTO venues (ground, stadium) VALUES (?, ?)",
                 ("Kansas City", "Arrowhead Stadium"))
    conn.commit()

    db.init_schema(conn)  # applies schema + migration on startup

    cols = [r[1] for r in conn.execute("PRAGMA table_info(venues)")]
    assert "roof" in cols
    roofs = {r["ground"]: r["roof"] for r in conn.execute("SELECT ground, roof FROM venues")}
    assert roofs["Atlanta"] == "retractable"
    assert roofs["Kansas City"] == "open"
    conn.close()


def test_migration_is_idempotent_when_column_present(tmp_path):
    path = tmp_path / "fresh.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    db.init_schema(conn)          # creates schema (roof column present)
    db._migrate_venue_roof(conn)  # second pass must be a no-op, not error
    cols = [r[1] for r in conn.execute("PRAGMA table_info(venues)")]
    assert cols.count("roof") == 1
    conn.close()


# ── front-end wiring (map.js + CSS) ──────────────────────────────────────────

def test_map_js_defines_roof_note_for_each_type():
    js = _read('static', 'js', 'map.js')
    assert 'function roofNote' in js
    assert 'ROOF_NOTE' in js
    for t in ROOF_TYPES:
        assert t in js, f"roof type {t} should be handled in map.js"


def test_map_js_renders_roof_in_popup_and_list():
    js = _read('static', 'js', 'map.js')
    # popup uses the venue's roof, the day-panel list uses the match's roof
    assert 'roofNote(v.roof)' in js
    assert 'roofNote(m.roof)' in js


def test_css_defines_open_and_covered_styles():
    css = _read('static', 'css', 'style.css')
    assert '.roof-open' in css
    assert '.roof-covered' in css
