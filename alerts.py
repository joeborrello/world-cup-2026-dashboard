"""Live weather advisories near the host venues, normalized to one GeoJSON.

Sources (all free, no key):
  * USA    — NWS api.weather.gov active alerts (point query per venue)
  * Canada — MSC GeoMet 'weather-alerts' collection (bbox query per venue)
  * Mexico — no reliable free machine feed; provider is a graceful no-op.

Alerts without polygon geometry (common for US zone-based advisories) are
surfaced as a point at the venue so they're still visible on the map.

Results are cached in-process for a few minutes (advisories are 'live' but
don't change second-to-second).
"""

import time
from datetime import datetime, timezone

import requests

import db

UA = "WorldCup2026Dashboard/1.0 (joe@josephborrello.com)"
TTL_SECONDS = 600
_CACHE = {"fc": None, "ts": 0.0}

# severity / risk -> color
COLOR = {
    "Extreme": "#6a1b9a", "Severe": "#d0432f", "Moderate": "#e08a1e",
    "Minor": "#c9a400", "Unknown": "#8a8a8a",
    "red": "#d0432f", "orange": "#e08a1e", "yellow": "#c9a400",
}


def _venues_by_country(conn):
    rows = conn.execute(
        "SELECT stadium, city, country, lat, lng FROM venues WHERE lat IS NOT NULL").fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["country"], []).append(dict(r))
    return out


def _feature(geometry, lat, lng, **props):
    """A normalized alert feature; falls back to a venue point when geometry-less."""
    at_venue = geometry is None
    return {
        "type": "Feature",
        "geometry": geometry or {"type": "Point", "coordinates": [lng, lat]},
        "properties": {**props, "at_venue": at_venue},
    }


def _us_alerts(venues):
    feats, seen = [], set()
    for v in venues:
        try:
            r = requests.get(
                "https://api.weather.gov/alerts/active",
                params={"point": f"{v['lat']},{v['lng']}"},
                headers={"User-Agent": UA, "Accept": "application/geo+json"},
                timeout=12)
            r.raise_for_status()
            data = r.json()
        except Exception:
            continue
        for f in data.get("features", []):
            p = f.get("properties", {})
            aid = f.get("id") or p.get("id")
            if aid in seen:
                continue
            seen.add(aid)
            sev = p.get("severity") or "Unknown"
            feats.append(_feature(
                f.get("geometry"), v["lat"], v["lng"],
                event=p.get("event"), severity=sev, color=COLOR.get(sev, "#8a8a8a"),
                headline=p.get("headline"),
                description=(p.get("description") or "")[:600],
                expires=p.get("expires"), country="USA", venue=v["city"]))
    return feats


def _ca_alerts(venues):
    feats, seen = [], set()
    for v in venues:
        bbox = f"{v['lng']-0.7},{v['lat']-0.6},{v['lng']+0.7},{v['lat']+0.6}"
        try:
            r = requests.get(
                "https://api.weather.gc.ca/collections/weather-alerts/items",
                params={"bbox": bbox, "f": "json", "limit": 50}, timeout=12)
            r.raise_for_status()
            data = r.json()
        except Exception:
            continue
        for f in data.get("features", []):
            p = f.get("properties", {})
            aid = p.get("id")
            if aid in seen:
                continue
            seen.add(aid)
            risk = (p.get("risk_colour_en") or "").lower()
            atype = (p.get("alert_type") or "").title()       # Warning/Watch/Advisory
            feats.append(_feature(
                f.get("geometry"), v["lat"], v["lng"],
                event=p.get("alert_name_en"), severity=atype or "Unknown",
                color=COLOR.get(risk, "#e08a1e"),
                headline=f"{p.get('alert_name_en')} — {p.get('feature_name_en')}",
                description=(p.get("alert_text_en") or "")[:600],
                expires=p.get("expiration_datetime"), country="Canada", venue=v["city"]))
    return feats


def _mx_alerts(venues):
    # No reliable free machine-readable feed for Mexico (SMN/CONAGUA). Intentionally
    # empty so the rest of the map still works; revisit if a feed becomes available.
    return []


def active_alerts(conn, force=False):
    """FeatureCollection of active advisories near the venues (cached)."""
    now = time.time()
    if not force and _CACHE["fc"] is not None and now - _CACHE["ts"] < TTL_SECONDS:
        return _CACHE["fc"]

    by_country = _venues_by_country(conn)
    feats = []
    feats += _us_alerts(by_country.get("USA", []))
    feats += _ca_alerts(by_country.get("Canada", []))
    feats += _mx_alerts(by_country.get("Mexico", []))

    fc = {
        "type": "FeatureCollection",
        "features": feats,
        "generated": datetime.now(timezone.utc).isoformat(),
        "coverage": {"USA": "nws", "Canada": "msc-geomet", "Mexico": "unavailable"},
    }
    _CACHE.update(fc=fc, ts=now)
    return fc
