"""
Golden Boot tracker: who is in contention to finish as the tournament's top
scorer, plus a projection of how many more goals each contender is on course for.

Scorers come from the openfootball goals feed (`goals1`/`goals2` per match) — the
same keyless, public-domain source the rest of the dashboard runs on, so the
tracker works with no API key. Rules follow the real Golden Boot:

  * penalties DO count toward a player's tally,
  * own goals do NOT (they are recorded with `owngoal=1` but never credited),
  * the leader is whoever has the most goals; players within CONTENTION_GAP of
    that lead are flagged "in contention",
  * a player whose team can play no more matches and whose (now final) tally is
    short of the lead is flagged "out_of_race" — the UI crosses them off just
    like eliminated teams on the predictions page.

The projection reuses the Monte-Carlo prediction engine (predict.py). A
contender's scoring *rate* (goals per match their team has played) is carried
forward over the number of matches their team is still expected to play, derived
from each team's round-reach probabilities. So a prolific striker on a team
projected to reach the final is credited more upside than an equally prolific one
whose team is likely to go out early. The projection is read-only and additive:
it never rewrites any goal already on the board.
"""

import math
from datetime import datetime, timezone

import predict

# A player is "in contention" if within this many goals of the current leader.
CONTENTION_GAP = 2

# Central probability mass covered by the projected goal *range*. You can't score
# a fraction of a goal, so rather than a single decimal we report the band of
# whole-goal outcomes a contender is most likely to land in — the 10th-to-90th
# percentile (an 80% interval) of the additional-goals distribution.
PROJ_INTERVAL = 0.80

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


# ── race elimination ─────────────────────────────────────────────────────────
def _teams_done_playing(conn):
    """Teams whose tournament is over — they cannot appear in another match.

    Judged only from decisively finished results, mirroring the philosophy of
    predict._eliminated_teams (never from Monte-Carlo sampling), but answering a
    different question: a semi-final loser is out of the *title* race yet still
    has the third-place match to score in, so for the Golden Boot a team is only
    done when:

      * it lost a decisively finished knockout match that was NOT a semi-final
        (semi-final losers go on to the third-place match), or
      * it played in a finished third-place match or final — after those, both
        participants are out of matches whoever won, or
      * every group match is finished, the opening knockout round's pairings
        have all resolved to real team names, and it isn't in that field (the
        same both-conditions guard the predictions page uses, since best-third
        assignment can lag the final group whistle).
    """
    ko = [dict(r) for r in conn.execute(
        "SELECT num, team1, team2, status, score1, score2, pen1, pen2, "
        "round_label FROM matches WHERE stage='knockout'")]
    groups = conn.execute(
        "SELECT team1, team2, status FROM matches WHERE stage='group'").fetchall()

    done = set()
    for m in ko:
        rnd = predict.ROUND_KEYS.get(m["round_label"])
        if rnd in ("third", "final"):
            if m["status"] == "finished" and m["team1"] and m["team2"]:
                done.update((m["team1"], m["team2"]))
        else:
            decided = predict._finished_decision(m)
            if decided and rnd != "sf":
                done.add(decided[1])

    if groups and all(r["status"] == "finished" for r in groups):
        first = min(ko, key=lambda m: m["num"], default=None)
        opening = [m for m in ko
                   if first and m["round_label"] == first["round_label"]]
        if opening and all(m["team1"] and m["team2"] for m in opening):
            field = {m[side] for m in opening for side in ("team1", "team2")}
            group_teams = {r[side] for r in groups for side in ("team1", "team2")
                           if r[side]}
            done |= group_teams - field
    return done


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


def _poisson_interval(mean, mass=PROJ_INTERVAL):
    """Central whole-goal interval for a Poisson(`mean`) number of goals.

    Goals are discrete events, so the *additional* goals a contender scores over
    their remaining matches is naturally Poisson-distributed about the projected
    mean. We return the `(low, high)` integer band holding the central `mass` of
    that distribution (the 10th–90th percentile for the default 80%) — i.e. "on
    course for between `low` and `high` more goals" rather than a fictitious
    fractional tally. A zero (or negative) mean collapses to ``(0, 0)``.

    Pure stdlib, matching the rest of the projection: we walk the Poisson PMF
    (`p_0 = e^-mean`, `p_k = p_{k-1}·mean/k`) accumulating probability, take the
    first k whose cumulative mass crosses the lower tail as `low` and the first
    that reaches the upper edge as `high`.
    """
    if mean <= 0:
        return 0, 0
    lo_tail = (1.0 - mass) / 2.0          # e.g. 0.10 for an 80% interval
    hi_edge = 1.0 - lo_tail               # e.g. 0.90
    low = high = None
    cumulative = 0.0
    p = math.exp(-mean)
    k = 0
    while True:
        cumulative += p
        if low is None and cumulative >= lo_tail:
            low = k
        if cumulative >= hi_edge:
            high = k
            break
        k += 1
        p *= mean / k
        if k > 1000:                      # numerical safety net; never reached in practice
            high = k
            break
    return (low or 0), high


def project(board, teams_odds, played, remaining):
    """Annotate each leaderboard row with a remaining-goals projection.

    The expected additional goals = shrunk goals-per-match rate · expected
    remaining matches; the rate is measured against the matches their *team* has
    played (we have no per-player appearance data) and is regressed toward
    PRIOR_RATE (see the module constants) so a hot opening game doesn't project
    an implausible final tally.

    Because a player can only score whole goals, the projection is surfaced as a
    *range* rather than a single decimal: `proj_add_low..proj_add_high` is the
    central PROJ_INTERVAL band of the Poisson distribution about that mean, and
    `proj_total_low..proj_total_high` adds the present tally. The mean values
    (`proj_additional` / `proj_total`) are kept as the stable sort key for the
    "projected finish" ranking.
    """
    for p in board:
        tp = played.get(p["team"], 0)
        rate = (p["goals"] + PRIOR_RATE * PRIOR_MATCHES) / (tp + PRIOR_MATCHES)
        rem = remaining.get(p["team"], 0.0)
        proj_add = rate * rem
        lo, hi = _poisson_interval(proj_add)
        p["rate"] = round(rate, 2)
        p["proj_remaining_matches"] = round(rem, 1)
        # expected values — retained as the projected-finish sort key
        p["proj_additional"] = round(proj_add, 1)
        p["proj_total"] = round(p["goals"] + proj_add, 1)
        # whole-goal ranges — what actually gets shown (no fractional goals)
        p["proj_add_low"], p["proj_add_high"] = lo, hi
        p["proj_total_low"] = p["goals"] + lo
        p["proj_total_high"] = p["goals"] + hi
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
        done = _teams_done_playing(conn)
        for p in board:
            # Out of the race outright: the team can play no more matches, so
            # the tally is final — and it is already short of the current lead.
            # (A done player level with the leader can still finish top, so
            # they stay in.) Crossed off in the UI like eliminated teams on
            # the predictions page.
            p["out_of_race"] = p["team"] in done and p["goals"] < leader_goals
            p["in_contention"] = (p["goals"] >= leader_goals - CONTENTION_GAP
                                  and not p["out_of_race"])
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
