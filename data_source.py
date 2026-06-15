"""
Load and normalize the openfootball 2026 World Cup dataset.

openfootball ships one JSON document with 104 matches in chronological order.
Group-stage entries carry real team names; knockout entries carry slot
placeholders ("2A", "1E", "3A/B/C/D/F", "W74", "L101"). We assign each match a
sequential number (1..104) — the same numbering the W{n}/L{n} references use.
"""

import json
import re
from datetime import datetime, timedelta, timezone

import requests

import config

GROUP_RE = re.compile(r"^Group ([A-L])$")
_KNOCKOUT_ROUNDS = {
    "Round of 32", "Round of 16", "Quarter-final",
    "Semi-final", "Match for third place", "Final",
}


def fetch_raw(prefer_remote=True):
    """Return the parsed openfootball JSON, preferring the live remote copy."""
    if prefer_remote:
        try:
            r = requests.get(config.OPENFOOTBALL_URL, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception:
            pass  # fall back to the local cached copy
    with open(config.OPENFOOTBALL_LOCAL, encoding="utf-8") as fh:
        return json.load(fh)


def _parse_time(date_str, time_str):
    """
    Parse openfootball's "13:00 UTC-6" into (local_time, offset, utc_iso).
    Returns (None, None, None) if the time is missing/unparseable.
    """
    if not time_str:
        return None, None, None
    m = re.match(r"^(\d{1,2}):(\d{2})\s+UTC([+-]\d{1,2})$", time_str.strip())
    if not m:
        return None, None, None
    hh, mm, off = int(m.group(1)), int(m.group(2)), int(m.group(3))
    local = f"{hh:02d}:{mm:02d}"
    # UTC = local_time - offset
    naive = datetime.strptime(f"{date_str} {local}", "%Y-%m-%d %H:%M")
    utc_dt = (naive - timedelta(hours=off)).replace(tzinfo=timezone.utc)
    return local, off, utc_dt.isoformat()


def normalize(raw):
    """Turn the raw feed into a list of match dicts ready for the DB."""
    out = []
    for i, mt in enumerate(raw["matches"], start=1):
        group_letter = None
        gm = GROUP_RE.match(mt.get("group") or "")
        if gm:
            group_letter = gm.group(1)
        stage = "group" if group_letter else "knockout"

        local_time, offset, utc_iso = _parse_time(mt["date"], mt.get("time"))

        score = mt.get("score") or {}
        ft = score.get("ft")
        if ft and len(ft) == 2:
            score1, score2, status = ft[0], ft[1], "finished"
        else:
            score1, score2, status = None, None, "scheduled"

        # For group matches the slots ARE the team names.
        team1_slot = mt["team1"]
        team2_slot = mt["team2"]
        team1 = team1_slot if stage == "group" else None
        team2 = team2_slot if stage == "group" else None

        out.append({
            "num": i,
            "stage": stage,
            "round_label": mt["round"],
            "group_letter": group_letter,
            "date": mt["date"],
            "local_time": local_time,
            "utc_offset": offset,
            "utc_datetime": utc_iso,
            "ground": mt["ground"],
            "team1_slot": team1_slot,
            "team2_slot": team2_slot,
            "team1": team1,
            "team2": team2,
            "score1": score1,
            "score2": score2,
            "status": status,
        })
    return out
