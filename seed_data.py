"""
Build data/worldcup.db from the openfootball dataset + the static venue table.

Run once to initialize. Idempotent: rebuilds the matches/venues/teams tables
from scratch each run. Use update_results.py afterwards to refresh live scores.
"""

import sys

import compute
import config
import data_source
import db
import goldenboot
from venues import VENUES


def seed(prefer_remote=True):
    conn = db.connect()
    db.init_schema(conn)

    # venues
    conn.execute("DELETE FROM venues")
    for ground, v in VENUES.items():
        conn.execute(
            """INSERT INTO venues (ground, stadium, city, country, lat, lng, tz)
               VALUES (?,?,?,?,?,?,?)""",
            (ground, v["stadium"], v["city"], v["country"],
             v["lat"], v["lng"], v["tz"]),
        )

    # matches
    raw = data_source.fetch_raw(prefer_remote=prefer_remote)
    matches = data_source.normalize(raw)

    # sanity: every ground must map to a known venue
    unknown = {m["ground"] for m in matches} - set(VENUES)
    if unknown:
        raise SystemExit(f"Unmapped venue(s) in feed: {sorted(unknown)}")

    conn.execute("DELETE FROM matches")
    for m in matches:
        conn.execute(
            """INSERT INTO matches
               (num, stage, round_label, group_letter, date, local_time,
                utc_offset, utc_datetime, ground, team1_slot, team2_slot,
                team1, team2, score1, score2, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (m["num"], m["stage"], m["round_label"], m["group_letter"],
             m["date"], m["local_time"], m["utc_offset"], m["utc_datetime"],
             m["ground"], m["team1_slot"], m["team2_slot"], m["team1"],
             m["team2"], m["score1"], m["score2"], m["status"]),
        )

    # teams + group membership (from group-stage fixtures)
    conn.execute("DELETE FROM teams")
    seen = {}
    for m in matches:
        if m["stage"] == "group":
            for t in (m["team1"], m["team2"]):
                seen[t] = m["group_letter"]
    for name, g in sorted(seen.items()):
        conn.execute(
            "INSERT INTO teams (name, group_letter) VALUES (?,?)", (name, g)
        )
    # goalscorers (Golden Boot tracker) from the same openfootball feed
    goldenboot.rebuild_scorers(conn, matches)

    conn.commit()

    # derive standings + bracket
    compute.recompute_all(conn)

    n_matches = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    n_teams = conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
    n_venues = conn.execute("SELECT COUNT(*) FROM venues").fetchone()[0]
    n_finished = conn.execute(
        "SELECT COUNT(*) FROM matches WHERE status='finished'").fetchone()[0]
    conn.close()
    print(f"Seeded: {n_matches} matches ({n_finished} finished), "
          f"{n_teams} teams, {n_venues} venues -> {config.DB_PATH}")


if __name__ == "__main__":
    # `python seed_data.py --offline` to seed from the local cached JSON only
    seed(prefer_remote="--offline" not in sys.argv)
