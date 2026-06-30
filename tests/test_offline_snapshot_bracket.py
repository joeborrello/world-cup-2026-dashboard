"""Regression: the committed OFFLINE snapshot must carry post-match results so a
clean checkout builds an accurate bracket (JOE-16).

Why this test exists — the bug that survived five fixes:
    Every earlier attempt hardened the logic that reads the *remote* feed
    (penalty parsing, the football-data overlay, stale-source protection, cache
    busting). But `conftest.py` and any clean-checkout verification seed the DB
    **offline** (`seed_data.seed(prefer_remote=False)`), which reads the committed
    `data/openfootball-2026.json`. That snapshot was frozen at 9 group-stage
    matches with NO knockout results, so Germany-Paraguay (match 74) seeded as
    *scheduled* and the projected bracket fell through to "advance the Elo
    favourite" — Germany — exactly the premature/ wrong winner the reviewer kept
    seeing. No amount of remote-feed logic could fix a data file the offline path
    never even reached.

These tests fail loudly if the snapshot ever regresses to a pre-knockout state,
and pin the end-to-end offline result: Paraguay (not Germany) and Canada (not
South Africa) advance out of the Round of 32.
"""

import sqlite3

import compute
import data_source
import db
import predict


def _offline_matches():
    """The committed offline snapshot, normalized exactly as seeding reads it."""
    raw = data_source.fetch_raw(prefer_remote=False)
    return data_source.normalize(raw)


def _find(matches, t1, t2):
    """The normalized knockout match for a resolved t1-vs-t2 pairing."""
    for m in matches:
        if m["stage"] == "knockout" and {m["team1_slot"], m["team2_slot"]} == {t1, t2}:
            return m
    raise AssertionError(f"no knockout match {t1} vs {t2} in the offline snapshot")


def test_offline_snapshot_contains_knockout_results():
    """The snapshot is not frozen at the group stage — it carries finished
    knockout matches, which is the whole point of an offline fallback that the
    tests seed from."""
    matches = _offline_matches()
    finished_ko = [
        m for m in matches
        if m["stage"] == "knockout" and m["status"] == "finished"
    ]
    assert finished_ko, (
        "the committed offline snapshot has NO finished knockout results — a "
        "clean-checkout (offline) seed will leave the bracket unplayed and the "
        "projected view will advance the Elo favourite (the JOE-16 bug)."
    )


def test_offline_snapshot_germany_paraguay_is_a_shootout_loss_for_germany():
    """Germany-Paraguay: level after extra time, Paraguay won the shootout 4-3."""
    m = _find(_offline_matches(), "Germany", "Paraguay")
    assert m["status"] == "finished"
    assert (m["score1"], m["score2"]) == (1, 1)
    assert (m["pen1"], m["pen2"]) == (3, 4)
    # winner_side reads the shootout: side 2 == Paraguay, NOT Germany.
    assert compute.winner_side(
        m["score1"], m["score2"], m["pen1"], m["pen2"]) == 2


def _seed_inmemory():
    """Build an in-memory DB from the offline snapshot and run the full
    derivation, mirroring seed_data.seed(prefer_remote=False) without touching
    the on-disk dashboard DB."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_schema(conn)
    for m in _offline_matches():
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
    conn.commit()
    compute.recompute_all(conn)
    return conn


def _teams_in_rounds_after_r32(conn):
    """Every team resolved into a Round-of-16-or-later match (num >= 89)."""
    teams = set()
    for r in conn.execute(
            "SELECT team1, team2 FROM matches WHERE stage='knockout' AND num >= 89"):
        teams.update(t for t in (r["team1"], r["team2"]) if t)
    return teams


def test_offline_seed_advances_the_real_shootout_winner_past_round_of_32():
    """End-to-end: seeding offline, Paraguay — the shootout winner — reaches the
    Round of 16, and Germany (the loser) never appears in any later round."""
    conn = _seed_inmemory()
    ger_par = conn.execute(
        "SELECT num, score1, score2, pen1, pen2 FROM matches WHERE stage='knockout' "
        "AND team1='Germany' AND team2='Paraguay'").fetchone()
    assert ger_par is not None
    assert compute.winner_side(*[ger_par[k] for k in
                                 ("score1", "score2", "pen1", "pen2")]) == 2  # Paraguay
    later = _teams_in_rounds_after_r32(conn)
    assert "Paraguay" in later
    assert "Germany" not in later


def test_offline_seed_advances_canada_not_south_africa():
    """The sibling case from the issue title: South Africa 0-1 Canada — Canada
    advances, South Africa does not."""
    conn = _seed_inmemory()
    m = conn.execute(
        "SELECT num, score1, score2 FROM matches WHERE stage='knockout' "
        "AND team1='South Africa' AND team2='Canada'").fetchone()
    assert m is not None
    assert compute.winner_side(m["score1"], m["score2"]) == 2  # Canada
    later = _teams_in_rounds_after_r32(conn)
    assert "Canada" in later
    assert "South Africa" not in later


def test_offline_seed_projected_bracket_never_advances_germany():
    """The projected (Elo) bracket must respect the finished result: Germany is
    eliminated, so it can appear in its own settled R32 box but never advance to
    a later round."""
    conn = _seed_inmemory()
    pb = predict.projected_bracket(conn)
    advanced_anywhere = set()
    for num, slot in pb["slots"].items():
        if predict.round_of(num) == "r32":
            continue  # the settled R32 box legitimately still shows both teams
        for side in ("team1", "team2"):
            if slot.get(side):
                advanced_anywhere.add(slot[side]["team"])
    assert "Germany" not in advanced_anywhere
    assert "Paraguay" in advanced_anywhere
