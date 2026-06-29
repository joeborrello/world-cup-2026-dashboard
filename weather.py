"""Per-game weather for the daily map.

Each match's conditions are taken at its kickoff hour from Open-Meteo (free, no key):
  * future / today  -> forecast (temperature + precipitation probability)
  * past            -> observed values for that hour (probability not available)

Open-Meteo's forecast endpoint serves a window of roughly [today-92d, today+16d]
from a single call, so one source covers the whole tournament relative to "now".
Dates beyond that future horizon return ``available: False`` (e.g. late-July
knockouts seen from mid-June) until they come into range.

Results are cached per (venue, date) in SQLite: past dates never expire (history
is fixed), today/future are refreshed every few hours.
"""

import json
from datetime import date as _date, datetime, timedelta, timezone

import requests

import config
import db

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY = ("temperature_2m,precipitation_probability,precipitation,weathercode,"
          "wind_speed_10m,relative_humidity_2m,dew_point_2m")
FORECAST_HORIZON_DAYS = 16          # Open-Meteo free forecast range
FRESH_SECONDS = 3 * 3600            # TTL for today/future cache entries

# WMO weather interpretation codes -> (label, emoji)
WMO = {
    0: ("Clear sky", "☀️"), 1: ("Mainly clear", "🌤️"), 2: ("Partly cloudy", "⛅"),
    3: ("Overcast", "☁️"), 45: ("Fog", "🌫️"), 48: ("Rime fog", "🌫️"),
    51: ("Light drizzle", "🌦️"), 53: ("Drizzle", "🌦️"), 55: ("Dense drizzle", "🌧️"),
    56: ("Freezing drizzle", "🌧️"), 57: ("Freezing drizzle", "🌧️"),
    61: ("Light rain", "🌦️"), 63: ("Rain", "🌧️"), 65: ("Heavy rain", "🌧️"),
    66: ("Freezing rain", "🌧️"), 67: ("Freezing rain", "🌧️"),
    71: ("Light snow", "🌨️"), 73: ("Snow", "🌨️"), 75: ("Heavy snow", "❄️"),
    77: ("Snow grains", "🌨️"), 80: ("Rain showers", "🌦️"), 81: ("Rain showers", "🌧️"),
    82: ("Violent showers", "⛈️"), 85: ("Snow showers", "🌨️"), 86: ("Snow showers", "🌨️"),
    95: ("Thunderstorm", "⛈️"), 96: ("Thunderstorm, hail", "⛈️"), 99: ("Thunderstorm, hail", "⛈️"),
}


def _ensure_cache(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS weather_cache (
            ground     TEXT,
            date       TEXT,
            payload    TEXT,
            fetched_at TEXT,
            PRIMARY KEY (ground, date)
        )""")
    conn.commit()


def _days_ahead(date_str):
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    return (d - config.tournament_today()).days


def _fetch_hourly(lat, lng, start_date, end_date):
    """Raw Open-Meteo hourly arrays (UTC) for a venue/range, or None on failure."""
    try:
        r = requests.get(FORECAST_URL, params={
            "latitude": lat, "longitude": lng, "hourly": HOURLY,
            "start_date": start_date, "end_date": end_date,
            "timezone": "UTC", "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph", "precipitation_unit": "inch",
        }, timeout=15)
        r.raise_for_status()
        return r.json().get("hourly")
    except Exception:
        return None


def _venue_hourly(conn, ground, lat, lng, date_str):
    """Cached hourly arrays for a venue/day (honoring the TTL rules)."""
    _ensure_cache(conn)
    row = conn.execute(
        "SELECT payload, fetched_at FROM weather_cache WHERE ground=? AND date=?",
        (ground, date_str)).fetchone()
    if row:
        fresh = _days_ahead(date_str) < 0  # past dates are immutable
        if not fresh:
            age = (datetime.now(timezone.utc)
                   - datetime.fromisoformat(row["fetched_at"])).total_seconds()
            fresh = age < FRESH_SECONDS
        if fresh:
            return json.loads(row["payload"])

    # Fetch a 2-day UTC window: a venue's kickoff (Americas, UTC-behind) can fall
    # on the *next* UTC day for evening games, so [date, date+1] always covers it.
    end_date = (datetime.strptime(date_str, "%Y-%m-%d").date() + timedelta(days=1)).isoformat()
    hourly = _fetch_hourly(lat, lng, date_str, end_date)
    if hourly is None:
        return json.loads(row["payload"]) if row else None  # fall back to stale
    conn.execute(
        "INSERT OR REPLACE INTO weather_cache (ground, date, payload, fetched_at) VALUES (?,?,?,?)",
        (ground, date_str, json.dumps(hourly), datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return hourly


def _at_hour(hourly, utc_dt_iso):
    """Pick the hourly sample nearest the match's kickoff hour."""
    target = utc_dt_iso[:13]                     # 'YYYY-MM-DDTHH'
    times = hourly.get("time", [])
    idx = next((i for i, t in enumerate(times) if t[:13] == target), None)
    if idx is None:
        return None

    def g(key):
        arr = hourly.get(key) or []
        return arr[idx] if idx < len(arr) else None

    code = g("weathercode")
    label, emoji = WMO.get(int(code) if code is not None else -1, ("—", "🌡️"))
    return {
        "temp_f": g("temperature_2m"),
        "precip_prob": g("precipitation_probability"),
        "precip_in": g("precipitation"),
        "humidity": g("relative_humidity_2m"),
        "dewpoint_f": g("dew_point_2m"),
        "wind_mph": g("wind_speed_10m"),
        "code": code, "desc": label, "emoji": emoji,
    }


def _kind_for(date_str):
    """'historical' (already played) | 'current' (today) | 'forecast' (future)."""
    ahead = _days_ahead(date_str)
    return "historical" if ahead < 0 else "current" if ahead == 0 else "forecast"


def _weather_for_rows(conn, rows):
    """Per-match kickoff weather for arbitrary match rows.

    Each row needs num, ground, date, utc_datetime, lat, lng. ``kind`` is derived
    per match from its own date, so a mixed set spanning several days (e.g. a
    single team's whole run) gets the right forecast/current/historical label on
    each match. Hourly fetches are deduped per (ground, date) within the batch.
    """
    out = {}
    hourly_by_key = {}
    for r in rows:
        date_str = r["date"]
        kind = _kind_for(date_str)
        if _days_ahead(date_str) > FORECAST_HORIZON_DAYS:   # beyond forecast horizon
            out[r["num"]] = {"available": False, "kind": kind}
            continue
        key = (r["ground"], date_str)
        if key not in hourly_by_key:
            hourly_by_key[key] = _venue_hourly(
                conn, r["ground"], r["lat"], r["lng"], date_str)
        hourly = hourly_by_key[key]
        w = _at_hour(hourly, r["utc_datetime"]) if hourly else None
        if w:
            w.update(available=True, kind=kind)
            out[r["num"]] = w
        else:
            out[r["num"]] = {"available": False, "kind": kind}
    return out


def weather_for_date(conn, date_str):
    """Per-match kickoff weather for every match on ``date_str`` (the daily map).

    Returns {match_num: {...weather, kind, available}}.  ``kind`` is
    'forecast' | 'current' | 'historical' for UI labeling.
    """
    rows = conn.execute("""
        SELECT m.num, m.ground, m.date, m.utc_datetime, v.lat, v.lng
        FROM matches m JOIN venues v ON v.ground = m.ground
        WHERE m.date = ? AND v.lat IS NOT NULL""", (date_str,)).fetchall()
    return _weather_for_rows(conn, rows)


def weather_for_nums(conn, nums):
    """Per-match kickoff weather for a specific set of match numbers.

    Used by the follow-a-team map, whose visible matches span many dates and
    venues: each gets its own forecast/current/historical reading at kickoff.
    Returns {match_num: {...weather, kind, available}}.
    """
    nums = [n for n in nums if n is not None]
    if not nums:
        return {}
    placeholders = ",".join("?" * len(nums))
    rows = conn.execute(f"""
        SELECT m.num, m.ground, m.date, m.utc_datetime, v.lat, v.lng
        FROM matches m JOIN venues v ON v.ground = m.ground
        WHERE m.num IN ({placeholders}) AND v.lat IS NOT NULL""", nums).fetchall()
    return _weather_for_rows(conn, rows)
