"""
Derive group standings, the best-third-place ranking, and resolved knockout
matchups from finished results.

Tiebreakers follow FIFA's primary order: points, goal difference, goals scored,
then head-to-head (points/GD/GF among the tied teams), then team name as a
stable final fallback (in place of fair-play points / drawing of lots).
"""

import re

GROUP_SLOT_RE = re.compile(r"^([12])([A-L])$")
THIRD_SLOT_RE = re.compile(r"^3([A-L/]+)$")
WINNER_RE = re.compile(r"^W(\d+)$")
LOSER_RE = re.compile(r"^L(\d+)$")


def _blank(team, group_letter):
    return {
        "group_letter": group_letter, "team": team, "played": 0,
        "win": 0, "draw": 0, "loss": 0, "gf": 0, "ga": 0, "gd": 0, "points": 0,
    }


def _apply(row, gf, ga):
    row["played"] += 1
    row["gf"] += gf
    row["ga"] += ga
    row["gd"] = row["gf"] - row["ga"]
    if gf > ga:
        row["win"] += 1
        row["points"] += 3
    elif gf == ga:
        row["draw"] += 1
        row["points"] += 1
    else:
        row["loss"] += 1


def _group_matches(conn):
    return conn.execute(
        "SELECT * FROM matches WHERE stage='group' ORDER BY num"
    ).fetchall()


def order_group(table, played):
    """Order one group's rows best->worst with FIFA tiebreakers, filling `rank`.

    `table`: {team -> standings row dict} with W/D/L/points/gf/gd accumulated.
    `played`: iterable of finished matches (each indexable by team1/team2/
    score1/score2) used for the head-to-head tiebreak.

    Shared by the DB path (compute_standings) and the prediction Monte-Carlo, so
    both apply identical tiebreakers. Returns the ordered list (rows mutated with
    `rank`).
    """
    def h2h(teams):
        mini = {t: _blank(t, None) for t in teams}
        for m in played:
            if m["team1"] in teams and m["team2"] in teams:
                _apply(mini[m["team1"]], m["score1"], m["score2"])
                _apply(mini[m["team2"]], m["score2"], m["score1"])
        return mini

    ordered = sorted(
        table.values(), key=lambda r: (-r["points"], -r["gd"], -r["gf"], r["team"]))
    # resolve blocks tied on (points, gd, gf) via head-to-head
    i = 0
    while i < len(ordered):
        j = i + 1
        while (j < len(ordered)
               and (ordered[j]["points"], ordered[j]["gd"], ordered[j]["gf"])
               == (ordered[i]["points"], ordered[i]["gd"], ordered[i]["gf"])):
            j += 1
        if j - i > 1:
            mini = h2h([r["team"] for r in ordered[i:j]])
            ordered[i:j] = sorted(
                ordered[i:j],
                key=lambda r: (-mini[r["team"]]["points"], -mini[r["team"]]["gd"],
                               -mini[r["team"]]["gf"], r["team"]))
        i = j
    for rank, r in enumerate(ordered, start=1):
        r["rank"] = rank
    return ordered


def compute_standings(conn):
    """Return {group_letter: [rows sorted best->worst]} with rank filled in."""
    rows = _group_matches(conn)

    # team -> group, and seed blank rows for every team that appears
    table = {}            # group -> {team -> row}
    finished = []         # finished matches for head-to-head
    for m in rows:
        g = m["group_letter"]
        table.setdefault(g, {})
        for t in (m["team1"], m["team2"]):
            if t and t not in table[g]:
                table[g][t] = _blank(t, g)
        if m["status"] == "finished" and m["team1"] and m["team2"]:
            _apply(table[g][m["team1"]], m["score1"], m["score2"])
            _apply(table[g][m["team2"]], m["score2"], m["score1"])
            finished.append(m)

    return {g: order_group(teams, finished) for g, teams in table.items()}


def rank_third_place(standings):
    """Return the 3rd-placed teams ranked best->worst; top 8 qualify."""
    thirds = [g[2] for g in standings.values() if len(g) >= 3]
    thirds = sorted(
        thirds, key=lambda r: (-r["points"], -r["gd"], -r["gf"], r["team"])
    )
    for i, r in enumerate(thirds, start=1):
        r["third_rank"] = i
        r["qualified_third"] = i <= 8
    return thirds


def persist_standings(conn, standings, thirds):
    """Write computed standings (with qualified flags) into the DB."""
    third_qualified = {r["team"] for r in thirds if r.get("qualified_third")}
    third_rank = {r["team"]: r["third_rank"] for r in thirds}
    all_groups_complete = all(
        all(r["played"] == 3 for r in rows) for rows in standings.values()
    )

    conn.execute("DELETE FROM standings")
    for g, rows in standings.items():
        complete = all(r["played"] == 3 for r in rows)
        for r in rows:
            # qualification is only asserted once it is mathematically settled:
            # top-2 once the group is done; 3rd place once every group is done.
            if r["rank"] <= 2 and complete:
                qualified = 1
            elif (r["rank"] == 3 and all_groups_complete
                  and r["team"] in third_qualified):
                qualified = 1
            else:
                qualified = 0
            conn.execute(
                """INSERT INTO standings
                   (group_letter, team, played, win, draw, loss, gf, ga, gd,
                    points, rank, third_rank, qualified)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (g, r["team"], r["played"], r["win"], r["draw"], r["loss"],
                 r["gf"], r["ga"], r["gd"], r["points"], r["rank"],
                 third_rank.get(r["team"]), qualified),
            )
    conn.commit()


def _group_complete(standings, letter):
    rows = standings.get(letter, [])
    return bool(rows) and all(r["played"] == 3 for r in rows)


def _all_groups_complete(standings):
    return bool(standings) and all(
        all(r["played"] == 3 for r in rows) for rows in standings.values()
    )


def assign_third_place_slots(conn, standings, thirds):
    """
    Map the 8 qualifying 3rd-placed teams onto the 8 "3X/Y/Z" Round-of-32 slots.

    Each slot lists the groups eligible to feed it (e.g. "3A/B/C/D/F"). We find a
    perfect matching that respects those constraints, most-constrained slot first.
    NOTE: this yields a *valid* allocation but may differ from FIFA's official
    fixed combination table; returns {} until every group is complete.
    """
    if not _all_groups_complete(standings):
        return {}
    qualified = [r for r in thirds if r.get("qualified_third")]
    if len(qualified) < 8:
        return {}
    team_of_group = {r["group_letter"]: r["team"] for r in qualified}
    avail = sorted(team_of_group)  # qualifying groups, e.g. ['A','C',...]

    slots = []  # (match_num, side, {eligible groups})
    rows = conn.execute(
        "SELECT num, team1_slot, team2_slot FROM matches "
        "WHERE round_label='Round of 32'"
    ).fetchall()
    for m in rows:
        for side, slot in (("team1", m["team1_slot"]), ("team2", m["team2_slot"])):
            tm = THIRD_SLOT_RE.match(slot)
            if tm:
                slots.append((m["num"], side, set(tm.group(1).split("/"))))
    slots.sort(key=lambda s: len(s[2]))  # fewest options first

    assignment, used = {}, set()

    def backtrack(i):
        if i == len(slots):
            return True
        num, side, eligible = slots[i]
        for g in avail:
            if g in eligible and g not in used:
                used.add(g)
                assignment[(num, side)] = g
                if backtrack(i + 1):
                    return True
                used.remove(g)
                del assignment[(num, side)]
        return False

    if not backtrack(0):
        return {}
    return {key: team_of_group[g] for key, g in assignment.items()}


def resolve_bracket(conn, standings, thirds=None):
    """
    Fill matches.team1/team2 for knockout games whose feeder results are known.

    Resolves group winner/runner-up slots ("1A"/"2A") once a group is complete,
    and W{n}/L{n} references once match n is finished. Third-place slots
    ("3A/B/C/D/F") are left unresolved — assigning them requires FIFA's fixed
    combination table (a future enhancement); the candidate groups are still
    shown to the user via the raw slot text.
    """
    ko = conn.execute(
        "SELECT * FROM matches WHERE stage='knockout' ORDER BY num"
    ).fetchall()

    # mutable working copy: num -> {team1, team2, score1, score2, status}
    state = {m["num"]: {
        "team1": m["team1"], "team2": m["team2"],
        "score1": m["score1"], "score2": m["score2"],
        "status": m["status"],
    } for m in ko}
    slots = {m["num"]: (m["team1_slot"], m["team2_slot"]) for m in ko}

    # pre-seed best-3rd-place slots (only resolves once all groups are complete).
    # Overwrite rather than fill-if-empty: standings can change as late results
    # land, and a stale assignment must be corrected, not kept.
    third_assign = assign_third_place_slots(conn, standings, thirds or [])
    for (num, side), team in third_assign.items():
        if num in state:
            state[num][side] = team

    def winner(n):
        s = state.get(n)
        if not s or s["status"] != "finished":
            return None
        if s["score1"] is None or s["score2"] is None:
            return None
        return s["team1"] if s["score1"] >= s["score2"] else s["team2"]

    def loser(n):
        s = state.get(n)
        if not s or s["status"] != "finished":
            return None
        if s["score1"] is None or s["score2"] is None:
            return None
        return s["team2"] if s["score1"] >= s["score2"] else s["team1"]

    def resolve(slot):
        gm = GROUP_SLOT_RE.match(slot)
        if gm:
            pos, letter = int(gm.group(1)), gm.group(2)
            if _group_complete(standings, letter):
                return standings[letter][pos - 1]["team"]
            return None
        wm = WINNER_RE.match(slot)
        if wm:
            return winner(int(wm.group(1)))
        lm = LOSER_RE.match(slot)
        if lm:
            return loser(int(lm.group(1)))
        return None  # "3A/B/C/D/F" and anything else

    # iterate to a fixed point (later rounds depend on earlier ones). Re-resolve
    # every slot each pass and OVERWRITE when the derived team changes — a score
    # correction upstream (e.g. a fixed group result) must re-seed the bracket,
    # not be ignored because the slot was already filled with a now-stale team.
    # `resolve` only returns a team once its feeder is settled, so we never erase
    # a known team back to None.
    for _ in range(len(ko) + 1):
        changed = False
        for n in sorted(state):
            s1, s2 = slots[n]
            for key, slot in (("team1", s1), ("team2", s2)):
                r = resolve(slot)
                if r is not None and state[n][key] != r:
                    state[n][key] = r
                    changed = True
        if not changed:
            break

    for n, s in state.items():
        conn.execute(
            "UPDATE matches SET team1=?, team2=? WHERE num=?",
            (s["team1"], s["team2"], n),
        )
    conn.commit()


def recompute_all(conn):
    """Run the full derivation pipeline. Safe to call after any score update."""
    standings = compute_standings(conn)
    thirds = rank_third_place(standings)
    persist_standings(conn, standings, thirds)
    resolve_bracket(conn, standings, thirds)
    return standings, thirds
