"""
Build an edition's SQLite DB from its openfootball dataset + static venue table.

Run once per edition to initialize (`python seed_data.py [--edition men]
[--offline]`). Idempotent: rebuilds the matches/venues/teams tables from scratch
each run. Use update_results.py afterwards to refresh live scores.

An edition whose fixtures aren't published anywhere yet (no remote feed and no
offline snapshot) still seeds fully — schema, venues and an empty schedule — so
the site can serve its pages in a pre-draw state and lights up the moment a
feed exists.
"""

import sys

import compute
import data_source
import db
import editions
import goldenboot
import ratings


def seed(prefer_remote=True, edition=editions.DEFAULT):
    conn = db.connect(edition.db_path)
    db.init_schema(conn)

    # venues
    conn.execute("DELETE FROM venues")
    for ground, v in edition.venues.items():
        conn.execute(
            """INSERT INTO venues (ground, stadium, city, country, lat, lng, tz, roof)
               VALUES (?,?,?,?,?,?,?,?)""",
            (ground, v["stadium"], v["city"], v["country"],
             v["lat"], v["lng"], v["tz"], v["roof"]),
        )

    # matches
    raw = data_source.fetch_raw(prefer_remote=prefer_remote,
                                url=edition.openfootball_url,
                                local=edition.openfootball_local)
    matches = data_source.normalize(raw) if raw else []
    if raw is None:
        print(f"[{edition.key}] no fixture feed published yet — "
              "seeding venues + empty schedule (pre-draw state)")

    # sanity: every ground must map to a known venue
    unknown = {m["ground"] for m in matches} - set(edition.venues)
    if unknown:
        raise SystemExit(f"Unmapped venue(s) in feed: {sorted(unknown)}")

    conn.execute("DELETE FROM matches")
    for m in matches:
        conn.execute(
            """INSERT INTO matches
               (num, stage, round_label, group_letter, date, local_time,
                utc_offset, utc_datetime, ground, team1_slot, team2_slot,
                team1, team2, score1, score2, pen1, pen2, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (m["num"], m["stage"], m["round_label"], m["group_letter"],
             m["date"], m["local_time"], m["utc_offset"], m["utc_datetime"],
             m["ground"], m["team1_slot"], m["team2_slot"], m["team1"],
             m["team2"], m["score1"], m["score2"], m["pen1"], m["pen2"],
             m["status"]),
        )

    # teams + group membership (from group-stage fixtures), with the edition's
    # Elo priors seeded alongside so the DB carries everything edition-specific
    conn.execute("DELETE FROM teams")
    seen = {}
    for m in matches:
        if m["stage"] == "group":
            for t in (m["team1"], m["team2"]):
                seen[t] = m["group_letter"]
    for name, g in sorted(seen.items()):
        conn.execute(
            "INSERT INTO teams (name, group_letter, elo, is_host) VALUES (?,?,?,?)",
            (name, g, edition.elo.get(name, ratings.DEFAULT_ELO),
             1 if name in edition.elo_hosts else 0),
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
    print(f"Seeded [{edition.key}]: {n_matches} matches ({n_finished} finished), "
          f"{n_teams} teams, {n_venues} venues -> {edition.db_path}")


if __name__ == "__main__":
    # `python seed_data.py --offline` to seed from the local cached JSON only;
    # `--edition <key>` (or `--edition all`) selects which tournament to seed.
    which = "men"
    if "--edition" in sys.argv:
        which = sys.argv[sys.argv.index("--edition") + 1]
    keys = editions.EDITIONS if which == "all" else [which]
    for key in keys:
        seed(prefer_remote="--offline" not in sys.argv, edition=editions.get(key))
