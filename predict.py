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

import math
import random
import re
import threading
from collections import Counter
from datetime import datetime, timezone

import compute
import config
import ratings

SIMS = getattr(config, "PREDICT_SIMS", 8000)

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


def _sim_scores(ta, tb):
    la, lb = _lambdas(ratings.get_rating(ta), ratings.get_rating(tb))
    return _poisson(la), _poisson(lb)


def _sim_winner(ta, tb):
    sa, sb = _sim_scores(ta, tb)
    if sa > sb:
        return ta, tb
    if sb > sa:
        return tb, ta
    # knockout draw -> penalties, Elo-weighted coin
    if random.random() < _we(ratings.get_rating(ta), ratings.get_rating(tb)):
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
def _sim_once(group_fixtures, ko, third_slots):
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
            s1, s2 = _sim_scores(gm["team1"], gm["team2"])
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
            w, l = _sim_winner(t1, t2)
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

    rank_counts = {t: Counter() for t in team_group}      # team -> {rank: n}
    reach = {t: Counter() for t in team_group}            # team -> {round: n}
    slot_counts = {m["num"]: {"team1": Counter(), "team2": Counter()} for m in ko}

    for _ in range(sims):
        standings, results = _sim_once(group_fixtures, ko, third_slots)
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
            "group": g, "elo": ratings.get_rating(t),
            "p_first": rc[1] / n, "p_second": rc[2] / n, "p_third": rc[3] / n,
            "advance": rr["r32"] / n, "r16": rr["r16"] / n, "qf": rr["qf"] / n,
            "sf": rr["sf"] / n, "final": rr["final"] / n, "champion": rr["champion"] / n,
        }
    slots_out = {}
    for num in slot_counts:
        entry = {"round": round_of(num)}
        for side in ("team1", "team2"):
            ctr = slot_counts[num][side]
            if ctr:
                team, cnt = ctr.most_common(1)[0]
                entry[side] = {"team": team, "conf": round(cnt / n, 3)}
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
