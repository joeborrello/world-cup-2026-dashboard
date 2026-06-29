"""
Golden Boot tracker: who is in contention to finish as the tournament's top
scorer, plus a projection of how many more goals each contender is on course for.

Scorers come from the openfootball goals feed (`goals1`/`goals2` per match) — the
same keyless, public-domain source the rest of the dashboard runs on, so the
tracker works with no API key. Rules follow the real Golden Boot:

  * penalties DO count toward a player's tally,
  * own goals do NOT (they are recorded with `owngoal=1` but never credited),
  * the leader is whoever has the most goals; players within CONTENTION_GAP of
    that lead are flagged "in contention".

The projection reuses the Monte-Carlo prediction engine (predict.py). A
contender's scoring *rate* (goals per match their team has played) is carried
forward over the number of matches their team is still expected to play, derived
from each team's round-reach probabilities. So a prolific striker on a team
projected to reach the final is credited more upside than an equally prolific one
whose team is likely to go out early. The projection is read-only and additive:
it never rewrites any goal already on the board.
"""

from datetime import datetime, timezone

import predict

# A player is "in contention" if within this many goals of the current leader.
CONTENTION_GAP = 2

# Projection shrinkage. A raw goals-per-match rate is wild early on — a 2-in-1
# opening game would extrapolate to a record-shattering tally. We regress each
# rate toward a modest prior with a few pseudo-matches, so early projections stay
# sane and converge to the observed rate as a player's team plays more matches:
#     rate = (goals + PRIOR_RATE·PRIOR_MATCHES) / (matches_played + PRIOR_MATCHES)
PRIOR_RATE = 0.5       # goals/match a fresh scorer is regressed toward
PRIOR_MATCHES = 2.0    # strength of that prior, in pseudo-matches


# ── persistence ──────────────────────────────────────────────────────────────
def rebuild_scorers(conn, matches):
    """Replace the scorers table from normalized match dicts (idempotent).

    Called by seed_data.py and the 15-minute updater. openfootball only carries
    goal arrays for matches that have been played, so this naturally tracks the
    live picture as results land. Wholesale rebuild mirrors how standings are
    recomputed: simple, and never leaves a stale half-state behind.
    """
    conn.execute("DELETE FROM scorers")
    for m in matches:
        for g in m.get("goals", []):
            conn.execute(
                "INSERT INTO scorers (match_num, team, player, minute, penalty, owngoal) "
                "VALUES (?,?,?,?,?,?)",
                (m["num"], g["team"], g["player"], g["minute"],
                 1 if g["penalty"] else 0, 1 if g["owngoal"] else 0),
            )
    conn.commit()


# ── leaderboard ──────────────────────────────────────────────────────────────
def leaderboard(conn):
    """Players ranked by goals (own goals excluded), best first.

    Tiebreak order: most goals, then fewest penalties (open-play goals valued
    above spot-kicks, since we have no assist/minutes data for FIFA's official
    tiebreak), then name for a stable ordering. `rank` ties players on equal
    goals (standard competition ranking), so joint leaders share rank 1.
    """
    rows = conn.execute(
        "SELECT player, team, "
        "       COUNT(*)        AS goals, "
        "       SUM(penalty)    AS penalties, "
        "       COUNT(DISTINCT match_num) AS matches_scored "
        "FROM scorers WHERE owngoal=0 "
        "GROUP BY player, team"
    ).fetchall()

    board = [{
        "player": r["player"],
        "team": r["team"],
        "goals": r["goals"],
        "penalties": r["penalties"] or 0,
        "matches_scored": r["matches_scored"],
    } for r in rows]

    board.sort(key=lambda p: (-p["goals"], p["penalties"], p["player"]))

    # standard competition ranking on goals (joint leaders share rank 1)
    prev_goals, rank = None, 0
    for i, p in enumerate(board, start=1):
        if p["goals"] != prev_goals:
            rank = i
            prev_goals = p["goals"]
        p["rank"] = rank
    return board


# ── projection (ties into the prediction engine) ────────────────────────────
def _team_matches_played(conn):
    """team -> number of finished matches it has played (group + knockout)."""
    counts = {}
    for r in conn.execute(
        "SELECT team1, team2 FROM matches WHERE status='finished' "
        "AND team1 IS NOT NULL AND team2 IS NOT NULL"
    ):
        for t in (r["team1"], r["team2"]):
            counts[t] = counts.get(t, 0) + 1
    return counts


def _expected_remaining_matches(conn, teams_odds):
    """team -> expected number of matches it is still to play.

    Remaining group matches are deterministic (count the unfinished fixtures).
    Future knockout matches are an expectation from the sim's round-reach odds:
    a team's expected *total* knockout games is
        P(play R32) + P(play R16) + P(play QF) + 2·P(play SF)
    — the SF term is doubled because every semi-finalist also plays a sixth game
    (the final or the third-place match). Subtracting the knockout games already
    played leaves the expected number still to come.
    """
    rem_group, ko_played = {}, {}
    for r in conn.execute(
        "SELECT team1, team2 FROM matches "
        "WHERE stage='group' AND status!='finished'"
    ):
        for t in (r["team1"], r["team2"]):
            if t:
                rem_group[t] = rem_group.get(t, 0) + 1
    for r in conn.execute(
        "SELECT team1, team2 FROM matches WHERE stage='knockout' "
        "AND status='finished' AND team1 IS NOT NULL AND team2 IS NOT NULL"
    ):
        for t in (r["team1"], r["team2"]):
            ko_played[t] = ko_played.get(t, 0) + 1

    out = {}
    for t, o in teams_odds.items():
        exp_ko_total = o["advance"] + o["r16"] + o["qf"] + 2 * o["sf"]
        exp_ko_remaining = max(0.0, exp_ko_total - ko_played.get(t, 0))
        out[t] = rem_group.get(t, 0) + exp_ko_remaining
    return out


def project(board, teams_odds, played, remaining):
    """Annotate each leaderboard row with a remaining-goals projection.

    proj_additional = shrunk goals-per-match rate · expected remaining matches;
    proj_total adds that to the present tally. The rate is measured against the
    matches their *team* has played (we have no per-player appearance data) and
    is regressed toward PRIOR_RATE (see the module constants) so a hot opening
    game doesn't project an implausible final tally.
    """
    for p in board:
        tp = played.get(p["team"], 0)
        rate = (p["goals"] + PRIOR_RATE * PRIOR_MATCHES) / (tp + PRIOR_MATCHES)
        rem = remaining.get(p["team"], 0.0)
        proj_add = rate * rem
        p["rate"] = round(rate, 2)
        p["proj_remaining_matches"] = round(rem, 1)
        p["proj_additional"] = round(proj_add, 1)
        p["proj_total"] = round(p["goals"] + proj_add, 1)
    return board


# ── public entry point ───────────────────────────────────────────────────────
def tracker(conn, sims=None, seed=None):
    """Full Golden Boot payload: ranked contenders + projection + metadata.

    `sims`/`seed` are forwarded to the (cached) prediction engine so tests can
    pin a small, deterministic run.
    """
    board = leaderboard(conn)
    n_finished = conn.execute(
        "SELECT COUNT(*) FROM matches WHERE status='finished'").fetchone()[0]

    if board:
        odds = predict.predictions(conn, sims=sims, seed=seed)["teams"]
        played = _team_matches_played(conn)
        remaining = _expected_remaining_matches(conn, odds)
        project(board, odds, played, remaining)
        leader_goals = board[0]["goals"]
        for p in board:
            p["in_contention"] = p["goals"] >= leader_goals - CONTENTION_GAP
    else:
        leader_goals = 0

    return {
        "contenders": board,
        "leader_goals": leader_goals,
        "contention_gap": CONTENTION_GAP,
        "n_finished": n_finished,
        "total_goals": sum(p["goals"] for p in board),
        "generated": datetime.now(timezone.utc).isoformat(),
    }
