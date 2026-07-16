"""Women's 2027 seed data + predictions (JOE-49): the provisional dataset
seeds a complete women's DB and the Monte-Carlo engine produces sane odds
for it, with the men's edition untouched.

The dataset itself is provisional (no real draw exists yet — see
data/gen_wwc2027_provisional.py); these tests pin the *structure* FIFA has
published (window, venues, 32-team/8-group/64-match format) and the
invariants the engine needs (slot references, priors, simultaneous final
group matchdays), not the fabricated pairings.
"""

import json
import os
import re
from collections import Counter

import pytest

import db
import editions
import predict
import ratings
import seed_data
import venues

WOMEN = editions.get("women")
pytestmark = pytest.mark.skipif(
    WOMEN.key != "women",
    reason="women's edition not registered yet (JOE-48 not merged)")

SLOT_RE = re.compile(r"^([12][A-H]|[WL]\d+)$")


@pytest.fixture(scope="module")
def dataset():
    with open(WOMEN.openfootball_local, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def wconn(tmp_path_factory):
    """A women's DB seeded offline into a temp file (never the live DB)."""
    import dataclasses
    path = str(tmp_path_factory.mktemp("wwc") / "wwc-2027.db")
    ed = dataclasses.replace(WOMEN, db_path=path)
    seed_data.seed(prefer_remote=False, edition=ed)
    conn = db.connect(path)
    yield conn
    conn.close()


# ------------------------------------------------------------- the dataset

def test_dataset_is_marked_provisional(dataset):
    assert dataset["provisional"] is True
    assert "note" in dataset


def test_official_format_64_matches_8_groups_of_4(dataset):
    ms = dataset["matches"]
    assert len(ms) == 64
    group = [m for m in ms if m.get("group")]
    ko = [m for m in ms if not m.get("group")]
    assert len(group) == 48 and len(ko) == 16
    per_team = Counter(t for m in group for t in (m["team1"], m["team2"]))
    assert len(per_team) == 32
    assert set(per_team.values()) == {3}
    groups = {}
    for m in group:
        groups.setdefault(m["group"], set()).update((m["team1"], m["team2"]))
    assert len(groups) == 8
    assert all(len(ts) == 4 for ts in groups.values())


def test_official_window_and_venues(dataset):
    ms = dataset["matches"]
    dates = sorted(m["date"] for m in ms)
    assert dates[0] == "2027-06-24" and dates[-1] == "2027-07-25"
    grounds = {m["ground"] for m in ms}
    assert grounds == set(venues.WOMENS_VENUES)      # all 8, no strays
    assert ms[-1]["round"] == "Final"
    assert ms[-1]["ground"] == "Rio de Janeiro"


def test_knockout_slot_references_resolve(dataset):
    ms = dataset["matches"]
    ko = [(i, m) for i, m in enumerate(ms, start=1) if not m.get("group")]
    for num, m in ko:
        for slot in (m["team1"], m["team2"]):
            assert SLOT_RE.match(slot), (num, slot)
            wm = re.match(r"^[WL](\d+)$", slot)
            if wm:                       # W/L refs must point at an earlier KO match
                ref = int(wm.group(1))
                assert 49 <= ref < num
    # 32-team bracket: every group winner and runner-up feeds the R16 exactly once
    r16_slots = [s for _, m in ko for s in (m["team1"], m["team2"])
                 if re.match(r"^[12][A-H]$", s)]
    assert sorted(r16_slots) == sorted(
        f"{n}{g}" for n in "12" for g in "ABCDEFGH")


def test_final_group_matchday_kicks_off_simultaneously(dataset):
    md3 = {}
    for m in dataset["matches"]:
        if m.get("group") and m["round"] == "Matchday 3":
            md3.setdefault(m["group"], set()).add((m["date"], m["time"]))
    assert len(md3) == 8
    assert all(len(kickoffs) == 1 for kickoffs in md3.values())


def test_every_provisional_team_has_an_elo_prior(dataset):
    """No provisional qualifier should silently fall back to DEFAULT_ELO."""
    teams = {t for m in dataset["matches"] if m.get("group")
             for t in (m["team1"], m["team2"])}
    missing = teams - set(ratings.WOMENS_ELO)
    assert not missing, missing


# ------------------------------------------------------------- seeding

def test_seed_builds_a_complete_womens_db(wconn):
    n = lambda q: wconn.execute(q).fetchone()[0]
    assert n("SELECT COUNT(*) FROM matches") == 64
    assert n("SELECT COUNT(*) FROM matches WHERE status='finished'") == 0
    assert n("SELECT COUNT(*) FROM teams") == 32
    assert n("SELECT COUNT(*) FROM venues") == 8


def test_seed_carries_womens_priors_and_host_flag(wconn):
    rows = wconn.execute("SELECT name, elo, is_host FROM teams").fetchall()
    by_name = {r["name"]: r for r in rows}
    assert by_name["Brazil"]["is_host"] == 1
    assert sum(r["is_host"] for r in rows) == 1
    for name, r in by_name.items():
        assert r["elo"] == ratings.WOMENS_ELO[name]
    # and the DB round-trips as the edition's priors
    elo, hosts = ratings.db_priors(wconn)
    assert hosts == {"Brazil"}
    assert elo["Spain"] == ratings.WOMENS_ELO["Spain"]


def test_mens_db_is_not_touched_by_womens_seed(wconn):
    import config
    mconn = db.connect(config.DB_PATH)
    try:
        n_teams = mconn.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
        a_team = mconn.execute(
            "SELECT COUNT(*) FROM teams WHERE name='Argentina'").fetchone()[0]
        n_venues = mconn.execute("SELECT COUNT(*) FROM venues").fetchone()[0]
    finally:
        mconn.close()
    assert n_teams == 48
    assert a_team == 1
    assert n_venues == 16


# ------------------------------------------------------------- predictions

def test_monte_carlo_runs_for_the_womens_edition(wconn):
    p = predict.predictions(wconn, sims=300, seed=42)
    teams = p["teams"]
    assert len(teams) == 32
    assert p["n_finished"] == 0
    total = sum(d.get("champion", 0) for d in teams.values())
    assert total == pytest.approx(1.0, abs=0.02)


def test_womens_odds_are_sane(wconn):
    """Priors must drive the odds: the elite sit on top, hosts get a bump."""
    p = predict.predictions(wconn, sims=500, seed=42)
    teams = p["teams"]
    ranked = sorted(teams, key=lambda t: -teams[t].get("champion", 0))
    assert set(ranked[:4]) <= {"Spain", "USA", "England", "Germany", "Japan",
                               "Brazil", "Sweden", "France"}
    assert teams["Spain"]["champion"] > teams["Vietnam"].get("champion", 0)
    # host bonus: Brazil should outperform its raw-Elo peers
    assert teams["Brazil"]["champion"] > teams["Canada"].get("champion", 0)
