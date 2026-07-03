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


def _parse_goals(mt, team1_name, team2_name):
    """Flatten the openfootball goals1/goals2 arrays into scorer dicts.

    Each goal carries the scoring player's name, the minute, and optional
    `penalty` / `owngoal` flags. The team credited is the side the goal arrays
    belong to (goals1 -> team1, goals2 -> team2); for an own goal that is the
    *beneficiary* side, and the entry is flagged so the Golden Boot never credits
    it to the named player. `team1_name`/`team2_name` are the raw feed team names
    (real names even for finished knockout matches, where our slot is "W74").
    """
    out = []
    for side, team in (("goals1", team1_name), ("goals2", team2_name)):
        for g in mt.get(side) or []:
            name = g.get("name")
            if not name:
                continue
            out.append({
                "team": team,
                "player": name,
                "minute": str(g.get("minute")) if g.get("minute") is not None else None,
                "penalty": bool(g.get("penalty")),
                "owngoal": bool(g.get("owngoal")),
            })
    return out


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

        # openfootball records a knockout that goes the distance across several
        # keys: ht (half time), ft (90'), et (after extra time), p (shootout).
        # The score that *stands* is the extra-time score when there was extra
        # time, else the full-time score; a level result is then decided by the
        # penalty shootout (p). Reading ft alone stored Germany-Paraguay as a 1-1
        # draw and handed the tie to the wrong side (JOE-16).
        score = mt.get("score") or {}
        et, ft, pens = score.get("et"), score.get("ft"), score.get("p")
        standing = et if (et and len(et) == 2) else ft
        if standing and len(standing) == 2:
            score1, score2, status = standing[0], standing[1], "finished"
        else:
            score1, score2, status = None, None, "scheduled"
        # A shootout cannot end level: a level `p` (e.g. [0, 0]) is a mid-update
        # placeholder, not a result. Storing it made the match look settled and
        # blocked the football-data tie-break backfill (JOE-38) — treat as absent.
        if pens and len(pens) == 2 and pens[0] != pens[1]:
            pen1, pen2 = pens[0], pens[1]
        else:
            pen1, pen2 = None, None

        # For group matches the slots ARE the team names.
        team1_slot = mt["team1"]
        team2_slot = mt["team2"]
        team1 = team1_slot if stage == "group" else None
        team2 = team2_slot if stage == "group" else None

        out.append({
            "num": i,
            "goals": _parse_goals(mt, team1_slot, team2_slot),
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
            "pen1": pen1,
            "pen2": pen2,
            "status": status,
        })
    return out
