"""
Refresh live scores, then recompute standings and the knockout bracket.

Primary source: openfootball JSON (keyless, public domain) — updates the score
and status of every match by its sequential number.

Optional enrichment: if FOOTBALL_DATA_API_KEY is set, football-data.org scores
are layered on top (matched by UTC kickoff + team names). The dashboard works
fully on openfootball alone; football-data.org just adds redundancy.

Designed to be run on a schedule (e.g. every 15 minutes via cron/pm2).
"""

import re
import sys
from datetime import datetime

import requests

import compute
import config
import data_source
import db

# openfootball uses these as knockout slot placeholders until a matchup is decided.
_THIRD_SLOT_RE = re.compile(r"^3[A-L/]+$")
_ANY_SLOT_RE = re.compile(r"^(?:[12][A-L]|3[A-L/]+|W\d+|L\d+)$")


def _update_from_openfootball(conn, prefer_remote=True):
    raw = data_source.fetch_raw(prefer_remote=prefer_remote)
    matches = data_source.normalize(raw)
    changed = 0
    for m in matches:
        cur = conn.execute(
            "SELECT score1, score2, status, team1_slot, team2_slot FROM matches WHERE num=?",
            (m["num"],),
        ).fetchone()
        if cur is None:
            continue
        sets, params = [], []
        if (cur["score1"], cur["score2"], cur["status"]) != (
                m["score1"], m["score2"], m["status"]):
            sets += ["score1=?", "score2=?", "status=?"]
            params += [m["score1"], m["score2"], m["status"]]
        # Adopt openfootball's authoritative 3rd-place R32 assignment: once a
        # matchup is decided, the feed replaces the "3A/B/.." placeholder with the
        # real team. Our own matcher only finds *a* valid allocation (not FIFA's
        # official combination table), so trust the feed for these slots.
        for col, of_val in (("team1_slot", m["team1_slot"]),
                            ("team2_slot", m["team2_slot"])):
            if (_THIRD_SLOT_RE.match(cur[col] or "")
                    and of_val and not _ANY_SLOT_RE.match(of_val)):
                sets.append(col + "=?")
                params.append(of_val)
        if sets:
            conn.execute("UPDATE matches SET " + ", ".join(sets) + " WHERE num=?",
                         (*params, m["num"]))
            changed += 1
    conn.commit()
    return changed


# football-data.org names a few countries differently than openfootball/our DB.
_NAME_ALIASES = {
    "korea republic": "south korea",
    "korea dpr": "north korea",
    "ir iran": "iran",
    "united states": "usa",
    "côte d'ivoire": "ivory coast",
    "cote d'ivoire": "ivory coast",
    "czechia": "czech republic",
    "bosnia and herzegovina": "bosnia & herzegovina",
}


def _norm_team(name):
    n = (name or "").strip().lower()
    return _NAME_ALIASES.get(n, n)


def _aligned_scores(home_name, away_name, home_score, away_score, team1, team2):
    """Map football-data (home/away) onto our (team1/team2) by NAME, not position.

    Returns (score1, score2) aligned to team1/team2, or None when the teams can't
    be matched confidently. Position-based mapping was the bug that wrote scores
    to the wrong side and flipped a group's 2nd place.
    """
    h, a = _norm_team(home_name), _norm_team(away_name)
    t1, t2 = _norm_team(team1), _norm_team(team2)
    if h == t1 and a == t2:
        return home_score, away_score
    if h == t2 and a == t1:
        return away_score, home_score
    return None


def _update_from_football_data(conn):
    """Best-effort in-play overlay from football-data.org. Silent no-op without a key.

    openfootball is authoritative for final results; this only surfaces scores
    for matches openfootball hasn't already settled, and only when team names
    align — so it can speed up live scores without ever corrupting a final.
    """
    if not config.FOOTBALL_DATA_API_KEY:
        return 0
    try:
        r = requests.get(
            config.FOOTBALL_DATA_URL,
            headers={"X-Auth-Token": config.FOOTBALL_DATA_API_KEY},
            timeout=15,
        )
        r.raise_for_status()
        payload = r.json()
    except Exception as exc:
        print(f"football-data.org skipped: {exc}")
        return 0

    changed = 0
    for fx in payload.get("matches", []):
        ft = (fx.get("score") or {}).get("fullTime") or {}
        if ft.get("home") is None or ft.get("away") is None:
            continue
        utc = fx.get("utcDate")  # e.g. 2026-06-11T19:00:00Z
        if not utc:
            continue
        iso = utc.replace("Z", "+00:00")
        try:
            key = datetime.fromisoformat(iso).isoformat()
        except ValueError:
            continue
        row = conn.execute(
            "SELECT num, team1, team2, status FROM matches WHERE utc_datetime=?", (key,)
        ).fetchone()
        if row is None:
            continue
        if row["status"] == "finished":
            continue  # openfootball owns finals — don't overwrite a settled result
        scores = _aligned_scores(
            (fx.get("homeTeam") or {}).get("name"),
            (fx.get("awayTeam") or {}).get("name"),
            ft["home"], ft["away"], row["team1"], row["team2"],
        )
        if scores is None:
            continue  # can't confidently align teams — skip rather than corrupt
        conn.execute(
            "UPDATE matches SET score1=?, score2=?, status='finished' WHERE num=?",
            (scores[0], scores[1], row["num"]),
        )
        changed += 1
    conn.commit()
    return changed


def main(prefer_remote=True):
    conn = db.connect()
    a = _update_from_openfootball(conn, prefer_remote=prefer_remote)
    b = _update_from_football_data(conn)
    compute.recompute_all(conn)
    n_finished = conn.execute(
        "SELECT COUNT(*) FROM matches WHERE status='finished'").fetchone()[0]
    conn.close()
    print(f"Updated {a} (openfootball) + {b} (football-data) matches; "
          f"{n_finished} finished total.")


if __name__ == "__main__":
    main(prefer_remote="--offline" not in sys.argv)
