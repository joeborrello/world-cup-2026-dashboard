"""Roll the tournament back to an earlier matchday (JOE-50).

Pure "as-of" view helpers: given the live match rows, strip every result played
after a cutoff date so the prediction engine re-forecasts the tournament exactly
as it stood at the end of that matchday. Rolling results back also rolls back the
model inputs — dynamic Elo replays only the surviving results, and simulated
standings/brackets are rebuilt from them — so the forecast is a genuine
re-forecast, not the live odds with a few boxes blanked.

Everything here is read-only: the matches table is never written (the 15-minute
updater cron remains its sole owner). A cutoff is either a 'YYYY-MM-DD' matchday
(results ON that date are kept, later ones stripped) or the START sentinel
(before the first whistle — every result stripped).
"""

import re

import compute

# Sentinel cutoff: roll all the way back to before the tournament started.
START = "start"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_param(raw):
    """Validate a ?rollback= query value.

    Returns START, a 'YYYY-MM-DD' string, or None (absent/malformed input rolls
    nothing back — the live view). Anything else is ignored rather than erroring,
    matching how the overrides param is handled."""
    if not raw:
        return None
    raw = raw.strip()
    if raw == START:
        return START
    if _DATE_RE.match(raw):
        return raw
    return None


def is_rolled_back(row, cutoff):
    """True if this match's finished result falls after the cutoff matchday."""
    if row["status"] != "finished":
        return False
    return cutoff == START or (row["date"] or "") > cutoff


def apply(rows, cutoff):
    """Strip results played after `cutoff` from match-row dicts, in place.

    A stripped match reverts to 'scheduled' with no scores, so every downstream
    consumer (Monte-Carlo sim, dynamic Elo, locked flags, standings) treats it as
    unplayed. Knockout team1/team2 resolutions are deliberately kept: they are
    only read when a match is decisively finished, and the group-based
    elimination check requires every group result to survive the cutoff — at
    which point the stored Round-of-32 field is exactly the field that was known
    at that moment. Returns (rows, rolled_back_nums).
    """
    rolled = []
    for r in rows:
        if is_rolled_back(r, cutoff):
            r["status"] = "scheduled"
            for k in ("score1", "score2", "pen1", "pen2"):
                if k in r:
                    r[k] = None
            rolled.append(r["num"])
    return rows, rolled


def points(conn):
    """Matchdays a user can roll back to: dates with finished results, ascending,
    each with how many results that day holds."""
    return [{"date": r["date"], "n": r["n"]} for r in conn.execute(
        "SELECT date, COUNT(*) n FROM matches WHERE status='finished' "
        "GROUP BY date ORDER BY date")]


def standings_as_of(conn, cutoff):
    """Group standings computed from only the results on/before `cutoff`.

    Reuses the exact FIFA-tiebreaker ordering the live standings use
    (compute.order_group), so a rolled-back rail shows what the tables really
    looked like at the end of that matchday. Returns {group_letter: ordered rows}
    with `rank` filled in; every team appears even with zero matches played.
    """
    rows = [dict(r) for r in conn.execute(
        "SELECT num, group_letter, team1, team2, score1, score2, status, date "
        "FROM matches WHERE stage='group' ORDER BY num")]
    apply(rows, cutoff)
    table, finished = {}, []
    for m in rows:
        g = m["group_letter"]
        table.setdefault(g, {})
        for t in (m["team1"], m["team2"]):
            if t and t not in table[g]:
                table[g][t] = compute._blank(t, g)
        if m["status"] == "finished" and m["team1"] and m["team2"]:
            compute._apply(table[g][m["team1"]], m["score1"], m["score2"])
            compute._apply(table[g][m["team2"]], m["score2"], m["score1"])
            finished.append(m)
    return {g: compute.order_group(teams, finished)
            for g, teams in table.items()}
