"""Currently in-play matches, for the live ticker shown across the site.

Pulls football-data.org and keeps only IN_PLAY / PAUSED fixtures, mapped to our
matches by UTC kickoff (the same key the 15-minute updater uses). Cached briefly
in-process so football-data calls stay bounded no matter how many people are
viewing. The live *minute* isn't on the free tier, so the frontend estimates it
from kickoff time; PAUSED is surfaced as half-time.
"""

import time
from datetime import datetime

import requests

import config
from flags import flag_code

TTL_SECONDS = 45
_CACHE = {"data": None, "ts": 0.0}


def live_matches(conn):
    now = time.time()
    if _CACHE["data"] is not None and now - _CACHE["ts"] < TTL_SECONDS:
        return _CACHE["data"]
    if not config.FOOTBALL_DATA_API_KEY:
        _CACHE.update(data=[], ts=now)
        return []

    try:
        r = requests.get(config.FOOTBALL_DATA_URL,
                         headers={"X-Auth-Token": config.FOOTBALL_DATA_API_KEY}, timeout=12)
        r.raise_for_status()
        payload = r.json()
    except Exception:
        return _CACHE["data"] or []        # serve last-known on error

    out = []
    for fx in payload.get("matches", []):
        if fx.get("status") not in ("IN_PLAY", "PAUSED"):
            continue
        utc = fx.get("utcDate")
        if not utc:
            continue
        try:
            key = datetime.fromisoformat(utc.replace("Z", "+00:00")).isoformat()
        except ValueError:
            continue
        row = conn.execute(
            "SELECT num, team1, team2, team1_slot, team2_slot, utc_datetime, "
            "group_letter, round_label FROM matches WHERE utc_datetime=?", (key,)).fetchone()
        if row is None:
            continue
        ft = (fx.get("score") or {}).get("fullTime") or {}
        out.append({
            "num": row["num"],
            "team1": row["team1"] or row["team1_slot"],
            "team2": row["team2"] or row["team2_slot"],
            "team1_code": flag_code(row["team1"]),
            "team2_code": flag_code(row["team2"]),
            "score1": ft.get("home") or 0,
            "score2": ft.get("away") or 0,
            "state": "paused" if fx["status"] == "PAUSED" else "in_play",
            "utc_datetime": row["utc_datetime"],
            "tag": (f"Group {row['group_letter']}" if row["group_letter"] else row["round_label"]),
        })
    _CACHE.update(data=out, ts=now)
    return out
