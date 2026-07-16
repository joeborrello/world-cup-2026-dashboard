"""
Generate data/wwc-2027.json — a PROVISIONAL 2027 Women's World Cup dataset.

As of July 2026 FIFA has published only the facts: Brazil hosts, Jun 24 –
Jul 25 2027, 32 teams in 8 groups, 64 matches, and the eight host cities.
The draw has not been made and openfootball ships no 2027 dataset yet, so
this file fabricates the rest — a plausible draw of 32 likely qualifiers
(seeded from ratings.WOMENS_ELO into four pots, respecting FIFA's
confederation separation rules: max one team per confederation per group,
except UEFA max two) laid over the official window and venues — so the
prediction engine, odds pages and alerts can run for the women's edition
before the real fixtures exist.

The output sits at the women's Edition.openfootball_local snapshot path.
When the real draw/schedule is published (openfootball or FIFA), replace
the JSON and reseed (`python seed_data.py --edition women`); nothing else
references this generator.

Run from the repo root: python data/gen_wwc2027_provisional.py
"""

import json
import os

OUT = os.path.join(os.path.dirname(__file__), "wwc-2027.json")

# Provisional draw. Position in each group = draw pot (1 = top seed).
# Confederations per group satisfy FIFA separation (≤1 per confed, UEFA ≤2).
GROUPS = {
    "A": ["Brazil", "Canada", "China", "Zambia"],
    "B": ["Spain", "North Korea", "Colombia", "Haiti"],
    "C": ["USA", "Netherlands", "South Korea", "Paraguay"],
    "D": ["England", "Australia", "Mexico", "Venezuela"],
    "E": ["Germany", "Italy", "Nigeria", "Vietnam"],
    "F": ["Japan", "Denmark", "Portugal", "Jamaica"],
    "G": ["Sweden", "Norway", "Argentina", "South Africa"],
    "H": ["France", "Iceland", "Morocco", "New Zealand"],
}

# The eight official host cities (keys match venues.WOMENS_VENUES grounds).
CITIES = ["São Paulo", "Rio de Janeiro", "Brasília", "Belo Horizonte",
          "Fortaleza", "Recife (São Lourenço da Mata)", "Salvador",
          "Porto Alegre"]

# All hosts sit on UTC-3 (Brazil abolished DST in 2019).
TZ = "UTC-3"

# Matchday pairings by draw position, FIFA convention. MD3 kicks off
# simultaneously within a group.
MD_PAIRS = {1: [(0, 1), (2, 3)], 2: [(0, 2), (3, 1)], 3: [(3, 0), (1, 2)]}

# Group-stage calendar: two groups play per day (MD1/MD2), and on MD3 both
# fixtures of a group share a kickoff. Groups are spaced four days apart.
MD_DAYS = {  # matchday -> [(date, [group letters...]), ...]
    1: [("2027-06-24", "A"), ("2027-06-25", "BC"), ("2027-06-26", "DE"),
        ("2027-06-27", "FG"), ("2027-06-28", "H")],
    2: [("2027-06-28", "A"), ("2027-06-29", "BC"), ("2027-06-30", "DE"),
        ("2027-07-01", "FG"), ("2027-07-02", "H")],
    3: [("2027-07-02", "A"), ("2027-07-03", "BC"), ("2027-07-04", "DE"),
        ("2027-07-05", "FG"), ("2027-07-06", "H")],
}


def build():
    matches = []
    city_i = 0

    def city():
        nonlocal city_i
        c = CITIES[city_i % len(CITIES)]
        city_i += 1
        return c

    # ── group stage (matches 1..48) ─────────────────────────────────────
    for md in (1, 2, 3):
        for date, letters in MD_DAYS[md]:
            for gi, g in enumerate(letters):
                teams = GROUPS[g]
                if md == 3:
                    # final round: both fixtures of a group kick off together
                    group_times = ["16:00", "20:00"]
                    pair_times = [group_times[gi], group_times[gi]]
                else:
                    day_times = ["13:00", "16:00", "19:00", "21:00"]
                    pair_times = [day_times[gi * 2], day_times[gi * 2 + 1]]
                for (a, b), time in zip(MD_PAIRS[md], pair_times):
                    matches.append({
                        "round": f"Matchday {md}",
                        "date": date,
                        "time": f"{time} {TZ}",
                        "team1": teams[a],
                        "team2": teams[b],
                        "group": f"Group {g}",
                        "ground": city(),
                    })

    # the opener is the host's first match at the opening venue
    matches[0]["ground"] = "São Paulo"
    matches[0]["time"] = f"20:00 {TZ}"

    # ── knockout (matches 49..64), 2023-format bracket ──────────────────
    def ko(round_, date, time, t1, t2, ground):
        matches.append({"round": round_, "date": date, "time": f"{time} {TZ}",
                        "team1": t1, "team2": t2, "ground": ground})

    r16 = [("1A", "2C"), ("1C", "2A"), ("1E", "2G"), ("1G", "2E"),
           ("1B", "2D"), ("1D", "2B"), ("1F", "2H"), ("1H", "2F")]
    r16_days = ["2027-07-09", "2027-07-09", "2027-07-10", "2027-07-10",
                "2027-07-11", "2027-07-11", "2027-07-12", "2027-07-12"]
    for i, ((t1, t2), d) in enumerate(zip(r16, r16_days)):
        ko("Round of 16", d, "16:00" if i % 2 == 0 else "20:00", t1, t2, city())

    qf = [("W49", "W51"), ("W50", "W52"), ("W53", "W55"), ("W54", "W56")]
    qf_days = ["2027-07-15", "2027-07-15", "2027-07-16", "2027-07-16"]
    for i, ((t1, t2), d) in enumerate(zip(qf, qf_days)):
        ko("Quarter-final", d, "16:00" if i % 2 == 0 else "20:00", t1, t2, city())

    ko("Semi-final", "2027-07-19", "20:00", "W57", "W58", "Belo Horizonte")
    ko("Semi-final", "2027-07-20", "20:00", "W59", "W60", "São Paulo")
    ko("Match for third place", "2027-07-24", "16:00", "L61", "L62", "Brasília")
    ko("Final", "2027-07-25", "16:00", "W61", "W62", "Rio de Janeiro")

    return {
        "name": "Women's World Cup 2027",
        "provisional": True,
        "note": ("PROVISIONAL dataset: official window, venues and format; "
                 "fabricated draw of likely qualifiers (no real draw exists "
                 "yet). Replace with the real feed and reseed when published. "
                 "Generated by data/gen_wwc2027_provisional.py."),
        "matches": matches,
    }


if __name__ == "__main__":
    data = build()
    assert len(data["matches"]) == 64, len(data["matches"])
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    print(f"wrote {OUT}: {len(data['matches'])} matches")
