"""Monte-Carlo tournament prediction (pure stdlib, no numpy).

Holds *finished* results fixed and simulates everything still to play many times,
then aggregates probabilities. Each iteration:
  1. Simulate unplayed group matches with a Poisson goals model whose expected
     goals come from the Elo gap (ratings.py) — scorelines feed FIFA tiebreakers.
  2. Build standings (reusing compute.order_group / rank_third_place) and assign
     the 8 best-third teams to the R32 "3X/Y" slots.
  3. Resolve + simulate the knockout bracket to the Final (draws -> Elo-weighted
     shootout), reusing the W{n}/L{n} slot structure.

Outputs per-team group-finish + round-reach probabilities and, per knockout
slot, the modal (most likely) team with a confidence. Cached in-process keyed on
the number of finished matches, so it only recomputes when results change.
NOTE: read-only — never writes to the matches table (the 15-min cron owns that).
"""

import bisect
import math
import random
import re
import threading
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache

import compute
import config
import ratings

SIMS = getattr(config, "PREDICT_SIMS", 8000)

# Dixon-Coles low-score dependence parameter. Independent Poisson under-produces
# draws; this reweights the four lowest scorelines (0-0/1-1 up, 1-0/0-1 down) to
# add the correlation real football shows. rho<0 lifts the draw rate; -0.12 takes
# the average group-stage draw rate from ~22% to a more historical ~24%. Set
# PREDICT_DRAW_RHO=0 to fall back to plain independent Poisson.
DRAW_RHO = getattr(config, "PREDICT_DRAW_RHO", -0.12)

GROUP_SLOT_RE = re.compile(r"^([12])([A-L])$")
THIRD_SLOT_RE = re.compile(r"^3([A-L/]+)$")
WINNER_RE = re.compile(r"^W(\d+)$")
LOSER_RE = re.compile(r"^L(\d+)$")

BASE_GOALS = 2.7        # typical combined expected goals, split by Elo supremacy


# ── match model ──────────────────────────────────────────────────────────────
def _poisson(lam):
    """Knuth's sampler — fine for the small means here."""
    L = math.exp(-lam)
    k, p = 0, 1.0
    while True:
        k += 1
        p *= random.random()
        if p <= L:
            return k - 1


def _lambdas(ra, rb):
    sup = max(-2.5, min(2.5, (ra - rb) / 200.0))     # ~200 Elo ≈ 1 goal supremacy
    return max(0.18, (BASE_GOALS + sup) / 2), max(0.18, (BASE_GOALS - sup) / 2)


def _we(ra, rb):
    return 1.0 / (1.0 + 10 ** (-(ra - rb) / 400.0))


def _tau(x, y, la, lb, rho):
    """Dixon-Coles correction factor for the four lowest-scoring cells."""
    if x == 0 and y == 0:
        return 1.0 - la * lb * rho
    if x == 0 and y == 1:
        return 1.0 + la * rho
    if x == 1 and y == 0:
        return 1.0 + lb * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


_DC_MAXG = 11      # goals grid 0..10 per side (P(>10) is negligible)


@lru_cache(maxsize=8192)
def _dc_cdf(la, lb, rho):
    """Cumulative scoreline distribution with the Dixon-Coles correction, as
    (flat list of (x,y) cells, cumulative probabilities). Cached per rounded
    (la, lb) pair so repeated identical matchups cost only a bisect."""
    pa = [math.exp(-la) * la ** k / math.factorial(k) for k in range(_DC_MAXG)]
    pb = [math.exp(-lb) * lb ** k / math.factorial(k) for k in range(_DC_MAXG)]
    cells, probs, tot = [], [], 0.0
    for x in range(_DC_MAXG):
        for y in range(_DC_MAXG):
            p = pa[x] * pb[y] * _tau(x, y, la, lb, rho)
            if p < 0.0:
                p = 0.0
            cells.append((x, y))
            probs.append(p)
            tot += p
    cdf, c = [], 0.0
    for p in probs:
        c += p / tot
        cdf.append(c)
    return cells, cdf


def _sim_scores(ta, tb, R):
    la, lb = _lambdas(R[ta], R[tb])
    if not DRAW_RHO:
        return _poisson(la), _poisson(lb)        # plain independent Poisson
    cells, cdf = _dc_cdf(round(la, 4), round(lb, 4), DRAW_RHO)
    i = bisect.bisect_left(cdf, random.random())
    return cells[i if i < len(cells) else -1]


def _sim_winner(ta, tb, R):
    sa, sb = _sim_scores(ta, tb, R)
    if sa > sb:
        return ta, tb
    if sb > sa:
        return tb, ta
    # knockout draw -> penalties, Elo-weighted coin
    if random.random() < _we(R[ta], R[tb]):
        return ta, tb
    return tb, ta


# ── bracket structure helpers ────────────────────────────────────────────────
def round_of(num):
    if 73 <= num <= 88:
        return "r32"
    if 89 <= num <= 96:
        return "r16"
    if 97 <= num <= 100:
        return "qf"
    if 101 <= num <= 102:
        return "sf"
    if num == 103:
        return "third"
    if num == 104:
        return "final"
    return None


def _assign_thirds(third_slots, qualified_groups, team_of_group):
    """Backtracking match of qualifying groups onto '3X/Y' slots (most-constrained
    first), mirroring compute.assign_third_place_slots but pure in-memory."""
    avail = sorted(qualified_groups)
    slots = sorted(third_slots, key=lambda s: len(s[2]))
    assignment, used = {}, set()

    def bt(i):
        if i == len(slots):
            return True
        num, side, elig = slots[i]
        for g in avail:
            if g in elig and g not in used:
                used.add(g)
                assignment[(num, side)] = g
                if bt(i + 1):
                    return True
                used.remove(g)
                del assignment[(num, side)]
        return False

    if not bt(0):
        return {}
    return {k: team_of_group[g] for k, g in assignment.items()}


# ── one simulation ───────────────────────────────────────────────────────────
def _sim_once(group_fixtures, ko, third_slots, R):
    # 1) group standings
    table, played = {}, {}
    for gm in group_fixtures:
        g = gm["group"]
        table.setdefault(g, {})
        played.setdefault(g, [])
        for t in (gm["team1"], gm["team2"]):
            if t not in table[g]:
                table[g][t] = compute._blank(t, g)
        if gm["status"] == "finished":
            s1, s2 = gm["score1"], gm["score2"]
        else:
            s1, s2 = _sim_scores(gm["team1"], gm["team2"], R)
        compute._apply(table[g][gm["team1"]], s1, s2)
        compute._apply(table[g][gm["team2"]], s2, s1)
        played[g].append({"team1": gm["team1"], "team2": gm["team2"],
                          "score1": s1, "score2": s2})
    standings = {g: compute.order_group(tbl, played[g]) for g, tbl in table.items()}

    # 2) best-third assignment onto the R32 "3X/Y" slots
    thirds = compute.rank_third_place(standings)
    team_of_group = {r["group_letter"]: r["team"]
                     for r in thirds if r.get("qualified_third")}
    third_assign = _assign_thirds(third_slots, set(team_of_group), team_of_group)

    # 3) knockout, in ascending num (every feeder is a lower-numbered match)
    results = {}

    def from_slot(slot, num, side):
        gm = GROUP_SLOT_RE.match(slot)
        if gm:
            pos, letter = int(gm.group(1)), gm.group(2)
            return standings[letter][pos - 1]["team"]
        if THIRD_SLOT_RE.match(slot):
            return third_assign.get((num, side))
        wm = WINNER_RE.match(slot)
        if wm:
            r = results.get(int(wm.group(1)))
            return r["winner"] if r else None
        lm = LOSER_RE.match(slot)
        if lm:
            r = results.get(int(lm.group(1)))
            return r["loser"] if r else None
        return None

    for m in ko:
        num = m["num"]
        if m["status"] == "finished" and m["score1"] is not None:
            t1, t2 = m["team1"], m["team2"]
            w, l = (t1, t2) if m["score1"] >= m["score2"] else (t2, t1)
        else:
            t1 = from_slot(m["slot1"], num, "team1")
            t2 = from_slot(m["slot2"], num, "team2")
            if not t1 or not t2:
                results[num] = {"team1": t1, "team2": t2, "winner": None, "loser": None}
                continue
            w, l = _sim_winner(t1, t2, R)
        results[num] = {"team1": t1, "team2": t2, "winner": w, "loser": l}

    return standings, results


# ── aggregate many simulations ───────────────────────────────────────────────
def _run(conn, sims):
    group_fixtures = [
        {"num": r["num"], "group": r["group_letter"], "team1": r["team1"],
         "team2": r["team2"], "status": r["status"],
         "score1": r["score1"], "score2": r["score2"]}
        for r in conn.execute(
            "SELECT num, group_letter, team1, team2, status, score1, score2 "
            "FROM matches WHERE stage='group' ORDER BY num")
    ]
    ko = [
        {"num": r["num"], "slot1": r["team1_slot"], "slot2": r["team2_slot"],
         "status": r["status"], "team1": r["team1"], "team2": r["team2"],
         "score1": r["score1"], "score2": r["score2"]}
        for r in conn.execute(
            "SELECT num, team1_slot, team2_slot, status, team1, team2, score1, score2 "
            "FROM matches WHERE stage='knockout' ORDER BY num")
    ]
    third_slots = []
    for m in ko:
        if round_of(m["num"]) == "r32":
            for side, slot in (("team1", m["slot1"]), ("team2", m["slot2"])):
                tm = THIRD_SLOT_RE.match(slot or "")
                if tm:
                    third_slots.append((m["num"], side, set(tm.group(1).split("/"))))

    team_group = {gm["team1"]: gm["group"] for gm in group_fixtures}
    team_group.update({gm["team2"]: gm["group"] for gm in group_fixtures})

    # Dynamic Elo: adjust the static priors by the finished results (in order),
    # then add the host edge. `R` (effective ratings) drives every simulated
    # match and the projected-bracket propagation, so the projection reflects
    # in-tournament form rather than only the pre-tournament snapshot.
    finished = conn.execute(
        "SELECT team1, team2, score1, score2 FROM matches "
        "WHERE status='finished' AND score1 IS NOT NULL "
        "AND team1 IS NOT NULL AND team2 IS NOT NULL "
        "ORDER BY utc_datetime, num").fetchall()
    base = ratings.dynamic_ratings(finished)
    R = {t: base.get(t, ratings.DEFAULT_ELO) + (ratings.HOST_BONUS if t in ratings.HOSTS else 0)
         for t in team_group}

    rank_counts = {t: Counter() for t in team_group}      # team -> {rank: n}
    reach = {t: Counter() for t in team_group}            # team -> {round: n}
    slot_counts = {m["num"]: {"team1": Counter(), "team2": Counter()} for m in ko}

    for _ in range(sims):
        standings, results = _sim_once(group_fixtures, ko, third_slots, R)
        for rows in standings.values():
            for r in rows:
                rank_counts[r["team"]][r["rank"]] += 1
        present = {"r32": set(), "r16": set(), "qf": set(), "sf": set(), "final": set()}
        for num, res in results.items():
            rnd = round_of(num)
            if rnd in present:
                for t in (res["team1"], res["team2"]):
                    if t:
                        present[rnd].add(t)
            for side in ("team1", "team2"):
                if res[side]:
                    slot_counts[num][side][res[side]] += 1
        for rnd, teams in present.items():
            for t in teams:
                reach[t][rnd] += 1
        champ = results.get(104, {}).get("winner")
        if champ:
            reach[champ]["champion"] += 1

    n = float(sims)
    teams_out = {}
    for t, g in team_group.items():
        rc, rr = rank_counts[t], reach[t]
        teams_out[t] = {
            "group": g, "elo": round(R.get(t, ratings.DEFAULT_ELO)),
            "elo_prior": round(ratings.get_rating(t)),
            "p_first": rc[1] / n, "p_second": rc[2] / n, "p_third": rc[3] / n,
            "advance": rr["r32"] / n, "r16": rr["r16"] / n, "qf": rr["qf"] / n,
            "sf": rr["sf"] / n, "final": rr["final"] / n, "champion": rr["champion"] / n,
        }
    # ── projected bracket: assemble ONE internally-consistent (realizable)
    # bracket instead of picking each slot's modal team independently (which
    # lets a strong team be the favorite in two mutually-exclusive slots at
    # once — e.g. both the Final and the 3rd-place match). We build a coherent
    # R32 field from the simulated standings, then propagate the favored winner
    # of each match forward so every later slot is fed by a real prior result.
    # Confidence is still the per-slot *marginal* P(team occupies this slot).
    group_teams = {}
    for t, g in team_group.items():
        group_teams.setdefault(g, []).append(t)

    def _exp_rank(t):                       # lower = finishes higher on average
        c = rank_counts[t]
        tot = sum(c.values()) or 1
        return sum(r * k for r, k in c.items()) / tot

    proj_pos = {}                           # (pos, group) -> projected team
    for g, ts in group_teams.items():
        for i, t in enumerate(sorted(ts, key=_exp_rank)):
            proj_pos[(i + 1, g)] = t

    # 8 best projected third-placers -> the "3X/Y" R32 slots (same matcher as live)
    third_groups = sorted(group_teams,
                          key=lambda g: -teams_out[proj_pos[(3, g)]]["advance"])[:8]
    team_of_group = {g: proj_pos[(3, g)] for g in third_groups}
    third_assign = _assign_thirds(third_slots, set(third_groups), team_of_group)

    res = {}                                # num -> {team1, team2, winner, loser}

    def _resolve(slot, num, side):
        if not slot:
            return None
        gm = GROUP_SLOT_RE.match(slot)
        if gm:
            return proj_pos.get((int(gm.group(1)), gm.group(2)))
        if THIRD_SLOT_RE.match(slot):
            return third_assign.get((num, side))
        wm = WINNER_RE.match(slot)
        if wm:
            r = res.get(int(wm.group(1)))
            return r["winner"] if r else None
        lm = LOSER_RE.match(slot)
        if lm:
            r = res.get(int(lm.group(1)))
            return r["loser"] if r else None
        return slot                         # already a literal team name (seeded/finished)

    ko_meta = {m["num"]: m for m in ko}
    for num in sorted(ko_meta):
        m = ko_meta[num]
        if m["status"] == "finished" and m["score1"] is not None:
            t1, t2 = m["team1"], m["team2"]
            w, l = (t1, t2) if m["score1"] >= m["score2"] else (t2, t1)
        else:
            t1 = _resolve(m["slot1"], num, "team1")
            t2 = _resolve(m["slot2"], num, "team2")
            if not t1 or not t2:
                res[num] = {"team1": t1, "team2": t2, "winner": None, "loser": None}
                continue
            # favored winner of the *projected* pairing advances (Elo == model pick)
            w, l = (t1, t2) if R.get(t1, 0) >= R.get(t2, 0) else (t2, t1)
        res[num] = {"team1": t1, "team2": t2, "winner": w, "loser": l}

    slots_out = {}
    for num in slot_counts:
        entry = {"round": round_of(num)}
        r = res.get(num, {})
        for side in ("team1", "team2"):
            team = r.get(side)
            if team:
                conf = slot_counts[num][side].get(team, 0) / n
                entry[side] = {"team": team, "conf": round(conf, 3)}
            else:
                entry[side] = None
        slots_out[num] = entry

    n_fin = sum(1 for gm in group_fixtures if gm["status"] == "finished") + \
        sum(1 for m in ko if m["status"] == "finished")
    return {
        "sims": sims, "n_finished": n_fin,
        "teams": teams_out, "slots": slots_out,
        "generated": datetime.now(timezone.utc).isoformat(),
    }


# ── cache (recompute only when the result set changes) ───────────────────────
_lock = threading.Lock()
_cache = {"key": None, "data": None}


def predictions(conn, sims=None, seed=None):
    sims = sims or SIMS
    n_fin = conn.execute(
        "SELECT COUNT(*) FROM matches WHERE status='finished'").fetchone()[0]
    key = (n_fin, sims, seed)
    with _lock:
        if _cache["key"] == key and _cache["data"] is not None:
            return _cache["data"]
    if seed is not None:
        random.seed(seed)
    data = _run(conn, sims)
    with _lock:
        _cache["key"], _cache["data"] = key, data
    return data
