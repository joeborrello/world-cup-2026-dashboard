"""Tests for historical / kickoff weather (JOE-15).

Weather is shown on two maps: the daily map (one date at a time) and the
follow-a-team map (a team's matches, which span many dates). Both pull from
`weather.py` and the `/api/weather` endpoint. These tests pin down:

  * per-match `kind` labeling (historical / current / forecast) derived from each
    match's own date — so a team's mixed run gets the right label on every match;
  * the new `weather_for_nums` entry point + `/api/weather?nums=` route;
  * the front-end wiring that surfaces it on both maps.

The Open-Meteo HTTP call is stubbed so the suite is offline and deterministic.
"""

import os
from datetime import date, datetime, timedelta

import pytest

import app as flask_app
import config
import db
import weather

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as fh:
        return fh.read()


@pytest.fixture
def client():
    flask_app.app.config['TESTING'] = True
    with flask_app.app.test_client() as c:
        yield c


def _fake_hourly(lat, lng, start_date, end_date):
    """Synthetic Open-Meteo hourly arrays covering [start_date, end_date] in UTC.

    Constant, plausible values for every hour so `_at_hour` always finds the
    match's kickoff hour regardless of when it falls.
    """
    times = []
    d = datetime.strptime(start_date, "%Y-%m-%d").date()
    last = datetime.strptime(end_date, "%Y-%m-%d").date()
    while d <= last:
        for h in range(24):
            times.append(f"{d.isoformat()}T{h:02d}:00")
        d += timedelta(days=1)
    n = len(times)
    return {
        "time": times,
        "temperature_2m": [72.0] * n,
        "precipitation_probability": [10] * n,
        "precipitation": [0.0] * n,
        "weathercode": [1] * n,
        "wind_speed_10m": [8.0] * n,
        "relative_humidity_2m": [55] * n,
        "dew_point_2m": [50.0] * n,
    }


@pytest.fixture
def stub_meteo(monkeypatch):
    """Stub the network fetch so weather tests never hit Open-Meteo."""
    monkeypatch.setattr(weather, "_fetch_hourly", _fake_hourly)


def _pin_today(monkeypatch, today):
    """Freeze the tournament 'today' used to classify a date's kind."""
    monkeypatch.setattr(config, "tournament_today", lambda: today)


def _sample_matches(conn, n=3):
    return conn.execute(
        """SELECT m.num, m.date, m.utc_datetime, v.lat, v.lng
           FROM matches m JOIN venues v ON v.ground = m.ground
           WHERE v.lat IS NOT NULL ORDER BY m.utc_datetime LIMIT ?""", (n,)).fetchall()


# ── kind classification ──────────────────────────────────────────────────────

def test_kind_for_past_today_future(monkeypatch):
    _pin_today(monkeypatch, date(2026, 6, 20))
    assert weather._kind_for("2026-06-19") == "historical"
    assert weather._kind_for("2026-06-20") == "current"
    assert weather._kind_for("2026-06-21") == "forecast"


def test_weather_for_date_marks_past_as_historical(stub_meteo, monkeypatch):
    conn = db.connect()
    row = _sample_matches(conn, 1)[0]
    # pretend "today" is well after this match -> it's history
    after = datetime.strptime(row["date"], "%Y-%m-%d").date() + timedelta(days=5)
    _pin_today(monkeypatch, after)
    data = weather.weather_for_date(conn, row["date"])
    conn.close()
    assert row["num"] in data
    w = data[row["num"]]
    assert w["available"] is True
    assert w["kind"] == "historical"
    assert w["temp_f"] == 72.0
    assert w["desc"]  # WMO label resolved


# ── weather_for_nums: the follow-a-team path ─────────────────────────────────

def test_weather_for_nums_mixed_dates_get_per_match_kind(stub_meteo, monkeypatch):
    conn = db.connect()
    rows = _sample_matches(conn, 3)
    # anchor "today" on the middle match's date so the three span past/today/future
    mid = datetime.strptime(rows[1]["date"], "%Y-%m-%d").date()
    _pin_today(monkeypatch, mid)
    nums = [r["num"] for r in rows]
    data = weather.weather_for_nums(conn, nums)
    conn.close()
    for r in rows:
        assert r["num"] in data
        expected = weather._kind_for(r["date"])  # uses the same pinned today
        assert data[r["num"]]["kind"] == expected
        assert data[r["num"]]["available"] is True


def test_weather_for_nums_empty(stub_meteo):
    conn = db.connect()
    assert weather.weather_for_nums(conn, []) == {}
    conn.close()


def test_weather_for_rows_dedupes_fetches_per_venue_day(monkeypatch):
    """Two matches sharing a venue/day must share a single hourly fetch."""
    calls = []

    def counting_fetch(lat, lng, start_date, end_date):
        calls.append((lat, lng, start_date))
        return _fake_hourly(lat, lng, start_date, end_date)

    monkeypatch.setattr(weather, "_fetch_hourly", counting_fetch)
    _pin_today(monkeypatch, date(2026, 6, 1))   # all future, no cache short-circuit
    rows = [
        {"num": 9001, "ground": "TestPark", "date": "2026-06-12",
         "utc_datetime": "2026-06-12T18:00:00", "lat": 40.0, "lng": -74.0},
        {"num": 9002, "ground": "TestPark", "date": "2026-06-12",
         "utc_datetime": "2026-06-12T21:00:00", "lat": 40.0, "lng": -74.0},
    ]
    conn = db.connect()
    conn.execute("DELETE FROM weather_cache WHERE ground=?", ("TestPark",))
    conn.commit()
    out = weather._weather_for_rows(conn, rows)
    conn.close()
    assert len(calls) == 1, "matches sharing a venue/day must share one fetch"
    assert out[9001]["available"] and out[9002]["available"]


# ── /api/weather route ───────────────────────────────────────────────────────

def test_api_weather_by_nums(client, stub_meteo):
    conn = db.connect()
    rows = _sample_matches(conn, 2)
    conn.close()
    nums = ",".join(str(r["num"]) for r in rows)
    resp = client.get('/api/weather?nums=' + nums)
    assert resp.status_code == 200
    data = resp.get_json()
    for r in rows:
        assert str(r["num"]) in data
        assert data[str(r["num"])]["available"] is True


def test_api_weather_nums_ignores_garbage(client, stub_meteo):
    conn = db.connect()
    num = _sample_matches(conn, 1)[0]["num"]
    conn.close()
    resp = client.get(f'/api/weather?nums=abc,{num},,99999999')
    assert resp.status_code == 200
    data = resp.get_json()
    assert str(num) in data            # the valid one resolved
    assert "99999999" not in data      # no such match -> dropped


def test_api_weather_by_date_still_works(client, stub_meteo):
    conn = db.connect()
    d = _sample_matches(conn, 1)[0]["date"]
    conn.close()
    resp = client.get('/api/weather?date=' + d)
    assert resp.status_code == 200
    assert resp.get_json()  # non-empty


def test_api_weather_no_args_is_empty(client):
    assert client.get('/api/weather').get_json() == {}


# ── front-end wiring ─────────────────────────────────────────────────────────

def test_team_map_page_exposes_weather(client):
    html = client.get('/team-map').get_data(as_text=True)
    assert 'api/weather' in html          # weatherUrl injected
    assert 'js/wx.js' in html             # shared formatter loaded
    assert 'js/teammap.js' in html


def test_daily_map_page_loads_shared_wx(client):
    html = client.get('/map').get_data(as_text=True)
    assert 'js/wx.js' in html


def test_shared_wx_module_formats_weather():
    js = _read('static', 'js', 'wx.js')
    assert 'window.WCWx' in js
    # historical readings are labeled "Actual"
    assert "historical: 'Actual'" in js
    assert 'function chip' in js and 'function line' in js


def test_teammap_fetches_and_renders_weather():
    js = _read('static', 'js', 'teammap.js')
    assert 'ensureWeather' in js
    assert "weatherUrl + '?nums='" in js
    assert 'WCWx.line' in js   # popup detail
    assert 'WCWx.chip' in js   # pin chip


def test_map_uses_shared_wx_module():
    """The daily map should delegate formatting to WCWx, not keep its own copy."""
    js = _read('static', 'js', 'map.js')
    assert 'WCWx.chip' in js and 'WCWx.line' in js
    assert 'WCWx.setUnit' in js
