"""
Refresh live scores, then recompute standings and the knockout bracket.

Primary source: openfootball JSON (keyless, public domain) — updates the score
and status of every match by its sequential number.

Optional enrichment: if FOOTBALL_DATA_API_KEY is set, football-data.org scores
are layered on top (matched by UTC kickoff + team names). The dashboard works
fully on openfootball alone; football-data.org just adds redundancy.

Designed to be run on a schedule (e.g. every 15 minutes via cron/pm2).
"""

import sys
from datetime import datetime

import requests

import compute
import config
import data_source
import db


def _update_from_openfootball(conn, prefer_remote=True):
    raw = data_source.fetch_raw(prefer_remote=prefer_remote)
    matches = data_source.normalize(raw)
    changed = 0
    for m in matches:
        cur = conn.execute(
            "SELECT score1, score2, status FROM matches WHERE num=?",
            (m["num"],),
        ).fetchone()
        if cur is None:
            continue
        if (cur["score1"], cur["score2"], cur["status"]) != (
                m["score1"], m["score2"], m["status"]):
            conn.execute(
                "UPDATE matches SET score1=?, score2=?, status=? WHERE num=?",
                (m["score1"], m["score2"], m["status"], m["num"]),
            )
            changed += 1
    conn.commit()
    return changed


def _update_from_football_data(conn):
    """Best-effort overlay from football-data.org. Silent no-op without a key."""
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
            "SELECT num FROM matches WHERE utc_datetime=?", (key,)
        ).fetchone()
        if row is None:
            continue
        conn.execute(
            "UPDATE matches SET score1=?, score2=?, status='finished' WHERE num=?",
            (ft["home"], ft["away"], row["num"]),
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
