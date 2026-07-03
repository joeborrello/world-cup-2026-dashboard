"""Currently in-play matches, for the live ticker shown across the site.

Pulls football-data.org and keeps IN_PLAY / PAUSED fixtures, mapped to our
matches by UTC kickoff (the same key the scheduled updater uses). Cached briefly
in-process so football-data calls stay bounded no matter how many people are
viewing.

football-data can be slow to flip a fixture's status to IN_PLAY after the real
kickoff (JOE-35: Spain–Austria still read TIMED 18+ minutes into the match), so
a fixture whose scheduled kickoff has passed is *presumed* live until the feed
catches up: shown in play at 0–0 with a kickoff-estimated minute, flagged
``presumed`` so the frontend can say the score is unconfirmed. The presumption
lapses after a regulation match's real duration.

The minute of play is resolved *at the moment of each check* (JOE-17): we use
football-data's own `minute` when the feed carries one, and otherwise estimate it
from elapsed time since kickoff. Either way it is a snapshot — "the minute as of
the most recent check" — not a clock the browser keeps ticking on its own, so the
`checked_at` timestamp travels with it. PAUSED is surfaced as half-time.
"""

import time
from datetime import datetime, timezone

import requests

import config
from flags import flag_code

TTL_SECONDS = 45
_CACHE = {"data": None, "ts": 0.0, "checked_at": None}

# A regulation half is 45'; the interval between halves is ~15 real minutes, so
# once we're past the break the elapsed clock runs ~15' ahead of the match clock.
_HALF_MINUTES = 45
_HALF_TIME_BREAK = 15

# Extra time (JOE-37) — knockout matches only. The formal signal is the feed's
# own minute running past 90 (ET is 91'–120'). The kickoff-elapsed *estimate*
# can't observe stoppage, so it only presumes ET once the estimated match
# minute clears regulation plus a generous stoppage-and-break allowance; before
# that a "90+'" could still be second-half stoppage.
_REGULATION_MINUTES = 2 * _HALF_MINUTES
_ET_MAX_MINUTES = _REGULATION_MINUTES + 30
_ET_PRESUME_MINUTE = _REGULATION_MINUTES + 10

# Feed statuses that mean "not started yet" — eligible for the kicked-off
# presumption once the scheduled kickoff has passed. The presumption expires
# after a full regulation match of real time (90' + break + stoppage): if the
# feed still hasn't flipped by then, the match was likely postponed or the feed
# is wrong in some other way, and a phantom 0–0 must not outlive the game.
_NOT_STARTED_STATUSES = ("TIMED", "SCHEDULED")
_PRESUMED_MAX_MINUTES = 2 * _HALF_MINUTES + _HALF_TIME_BREAK + 15


def _utcnow():
    return datetime.now(timezone.utc)


def _feed_minute_int(fx):
    """The feed's ``minute`` as an int, or None (absent or e.g. "45+2")."""
    try:
        return int(fx.get("minute"))
    except (TypeError, ValueError):
        return None


def _past_regulation(fx, kickoff, now):
    """True when the 90' of a knockout match must already be over — the feed's
    own minute has reached 90, or enough real time has elapsed since kickoff
    for two full halves plus the break."""
    minute = _feed_minute_int(fx)
    if minute is not None:
        return minute >= _REGULATION_MINUTES
    if kickoff is None or now is None:
        return False
    elapsed = (now - kickoff).total_seconds() / 60.0
    return elapsed >= _REGULATION_MINUTES + _HALF_TIME_BREAK


def live_minute(fx, kickoff, now, state, knockout=False):
    """Best-effort minute of play, as of ``now`` (the check time).

    Prefers football-data's own ``minute`` when the feed provides it; otherwise
    estimates from elapsed time since ``kickoff``. Returns a short display label
    ("63'", "45+'", "90+'") or "HT" at the break, or ``None`` when it can't be
    placed (no kickoff / clock not started yet).

    ``knockout`` matches can go to extra time (JOE-37): a feed minute past 90
    is formally ET ("ET 105'", capped "ET 120+'"); a PAUSED knockout past
    regulation is the ET interval ("ET break"), not half-time; and the
    kickoff-elapsed estimate degrades from "90+'" to a minute-less "ET" once
    regulation plus stoppage must have run out.
    """
    if state == "paused":
        if knockout and _past_regulation(fx, kickoff, now):
            return "ET break"
        return "HT"
    api_min = fx.get("minute")
    if api_min not in (None, ""):
        minute = _feed_minute_int(fx)
        if knockout and minute is not None and minute > _REGULATION_MINUTES:
            if minute >= _ET_MAX_MINUTES:
                return f"ET {_ET_MAX_MINUTES}+'"
            return f"ET {minute}'"
        return f"{api_min}'"
    if kickoff is None or now is None:
        return None
    elapsed = (now - kickoff).total_seconds() / 60.0
    if elapsed < 0:
        return None
    if elapsed <= _HALF_MINUTES:                       # first half
        return f"{int(elapsed) + 1}'"
    if elapsed < _HALF_MINUTES + _HALF_TIME_BREAK:     # stoppage / approaching break
        return "45+'"
    minute = int(elapsed - _HALF_TIME_BREAK)           # second half, break removed
    if minute < _REGULATION_MINUTES:
        return f"{minute}'"
    if knockout and minute >= _ET_PRESUME_MINUTE:      # stoppage can't explain it
        return "ET"
    return "90+'"


def presumed_live(kickoff, now):
    """True when the scheduled kickoff has passed recently enough that the match
    should be underway, even though the feed hasn't flipped it to IN_PLAY yet."""
    if kickoff is None or now is None:
        return False
    elapsed = (now - kickoff).total_seconds() / 60.0
    return 0 <= elapsed <= _PRESUMED_MAX_MINUTES


def _parse_kickoff(utc):
    """Parse a football-data ``utcDate`` (e.g. 2026-06-11T19:00:00Z) to aware UTC."""
    if not utc:
        return None
    try:
        return datetime.fromisoformat(utc.replace("Z", "+00:00"))
    except ValueError:
        return None


def last_checked():
    """ISO timestamp of the most recent successful live check, or None."""
    return _CACHE["checked_at"]


def live_matches(conn):
    now = time.time()
    if _CACHE["data"] is not None and now - _CACHE["ts"] < TTL_SECONDS:
        return _CACHE["data"]
    if not config.FOOTBALL_DATA_API_KEY:
        _CACHE.update(data=[], ts=now, checked_at=_utcnow().isoformat())
        return []

    try:
        r = requests.get(config.FOOTBALL_DATA_URL,
                         headers={"X-Auth-Token": config.FOOTBALL_DATA_API_KEY}, timeout=12)
        r.raise_for_status()
        payload = r.json()
    except Exception:
        return _CACHE["data"] or []        # serve last-known on error

    checked_at = _utcnow()
    out = []
    for fx in payload.get("matches", []):
        status = fx.get("status")
        kickoff = _parse_kickoff(fx.get("utcDate"))
        if kickoff is None:
            continue
        if status in ("IN_PLAY", "PAUSED"):
            presumed = False
        elif status in _NOT_STARTED_STATUSES and presumed_live(kickoff, checked_at):
            presumed = True
        else:
            continue
        key = kickoff.isoformat()
        row = conn.execute(
            "SELECT num, team1, team2, team1_slot, team2_slot, utc_datetime, "
            "group_letter, round_label FROM matches WHERE utc_datetime=?", (key,)).fetchone()
        if row is None:
            continue
        ft = (fx.get("score") or {}).get("fullTime") or {}
        state = "paused" if status == "PAUSED" else "in_play"
        knockout = not row["group_letter"]             # only knockouts have ET
        minute = live_minute(fx, kickoff, checked_at, state, knockout=knockout)
        out.append({
            "num": row["num"],
            "team1": row["team1"] or row["team1_slot"],
            "team2": row["team2"] or row["team2_slot"],
            "team1_code": flag_code(row["team1"]),
            "team2_code": flag_code(row["team2"]),
            "score1": ft.get("home") or 0,
            "score2": ft.get("away") or 0,
            "state": state,
            "presumed": presumed,
            "minute": minute,
            "extra_time": bool(minute) and minute.startswith("ET"),
            "utc_datetime": row["utc_datetime"],
            "tag": (f"Group {row['group_letter']}" if row["group_letter"] else row["round_label"]),
        })
    _CACHE.update(data=out, ts=now, checked_at=checked_at.isoformat())
    return out
