"""
Refresh live scores, then recompute standings and the knockout bracket.

Primary source: openfootball JSON (keyless, public domain) — updates the score
and status of every match by its sequential number.

Optional enrichment: if FOOTBALL_DATA_API_KEY is set, football-data.org scores
are layered on top (matched by UTC kickoff + team names). The dashboard works
fully on openfootball alone; football-data.org just adds redundancy.

Designed to be run on a schedule (e.g. every 15 minutes via cron/pm2).
"""

import re
import sys
from datetime import datetime

import requests

import compute
import config
import data_source
import db
import editions
import goldenboot

# openfootball uses these as knockout slot placeholders until a matchup is decided.
_THIRD_SLOT_RE = re.compile(r"^3[A-L/]+$")
_ANY_SLOT_RE = re.compile(r"^(?:[12][A-L]|3[A-L/]+|W\d+|L\d+)$")


def _update_from_openfootball(conn, prefer_remote=True, edition=editions.DEFAULT):
    raw = data_source.fetch_raw(prefer_remote=prefer_remote,
                                url=edition.openfootball_url,
                                local=edition.openfootball_local)
    if raw is None:
        return 0        # edition has no published fixture data yet
    matches = data_source.normalize(raw)
    changed = 0
    for m in matches:
        cur = conn.execute(
            "SELECT score1, score2, pen1, pen2, status, team1_slot, team2_slot "
            "FROM matches WHERE num=?",
            (m["num"],),
        ).fetchone()
        if cur is None:
            continue
        sets, params = [], []
        # pen1/pen2 are part of the result: a knockout that goes to a shootout has
        # a level score and is settled only by the penalties, so they must sync
        # too or the bracket can't tell who advanced (JOE-16).
        result_changed = (
            cur["score1"], cur["score2"], cur["pen1"], cur["pen2"], cur["status"]
        ) != (m["score1"], m["score2"], m["pen1"], m["pen2"], m["status"])
        # Never let a source that simply LACKS this result downgrade one the DB
        # already has. The REMOTE openfootball feed is authoritative, but on a
        # remote-fetch outage fetch_raw() falls back to the committed offline
        # snapshot, which carries no knockout results. Syncing that verbatim would
        # wipe a finished shootout (Germany 1-1 Paraguay, pens 3-4) back to
        # "scheduled" — and once num 74 is unplayed again the projected bracket
        # re-advances the Elo favorite (Germany) past a tie Paraguay actually won,
        # which is exactly the premature bracket update this issue is about. Only
        # sync when the feed is at least as resolved as the DB: a brand-new result
        # (DB not finished) or a real correction (still finished), never
        # finished -> unplayed (JOE-16).
        of_finished = m["status"] == "finished" and m["score1"] is not None
        db_finished = cur["status"] == "finished" and cur["score1"] is not None
        if result_changed and not (db_finished and not of_finished):
            sets += ["score1=?", "score2=?", "pen1=?", "pen2=?", "status=?"]
            params += [m["score1"], m["score2"], m["pen1"], m["pen2"], m["status"]]
        # Adopt openfootball's authoritative 3rd-place R32 assignment: once a
        # matchup is decided, the feed replaces the "3A/B/.." placeholder with the
        # real team. Our own matcher only finds *a* valid allocation (not FIFA's
        # official combination table), so trust the feed for these slots.
        for col, of_val in (("team1_slot", m["team1_slot"]),
                            ("team2_slot", m["team2_slot"])):
            if (_THIRD_SLOT_RE.match(cur[col] or "")
                    and of_val and not _ANY_SLOT_RE.match(of_val)):
                sets.append(col + "=?")
                params.append(of_val)
        if sets:
            conn.execute("UPDATE matches SET " + ", ".join(sets) + " WHERE num=?",
                         (*params, m["num"]))
            changed += 1
    conn.commit()
    # refresh goalscorers (Golden Boot) from the same feed we just pulled
    goldenboot.rebuild_scorers(conn, matches)
    return changed


# football-data.org names a few countries differently than openfootball/our DB.
_NAME_ALIASES = {
    "korea republic": "south korea",
    "korea dpr": "north korea",
    "ir iran": "iran",
    "united states": "usa",
    "côte d'ivoire": "ivory coast",
    "cote d'ivoire": "ivory coast",
    "czechia": "czech republic",
    "bosnia and herzegovina": "bosnia & herzegovina",
}


def _norm_team(name):
    n = (name or "").strip().lower()
    return _NAME_ALIASES.get(n, n)


def _side_of_home(home_name, away_name, team1, team2):
    """Which of our sides (1/2) is football-data's home team, or None if unsure.

    The single place that maps football-data's home/away onto our team1/team2 by
    NAME (not feed position, which was the bug that wrote scores to the wrong side
    and flipped a group's 2nd place).
    """
    h, a = _norm_team(home_name), _norm_team(away_name)
    t1, t2 = _norm_team(team1), _norm_team(team2)
    if h == t1 and a == t2:
        return 1
    if h == t2 and a == t1:
        return 2
    return None


def _aligned_scores(home_name, away_name, home_score, away_score, team1, team2):
    """Map football-data (home/away) scores onto our (team1/team2), or None."""
    side = _side_of_home(home_name, away_name, team1, team2)
    if side == 1:
        return home_score, away_score
    if side == 2:
        return away_score, home_score
    return None


# football-data's score.winner, expressed relative to home/away.
_FD_WINNER = {"HOME_TEAM": "home", "AWAY_TEAM": "away"}


def _decide_football_data(sc, home, away, team1, team2, is_knockout):
    """Read a finished football-data result into (score1, score2, pen1, pen2).

    Queries every part of the score the feed actually carries — not just
    ``fullTime``. In football-data's v4 match list the ``score`` object is
    ``{winner, duration, fullTime, halfTime}``: there is NO penalties breakdown,
    so a knockout settled on penalties arrives as a *level* ``fullTime`` with the
    shootout winner named only in ``score.winner``. Reading ``fullTime`` alone
    (or looking for a non-existent ``score.penalties``) therefore stored
    Germany-Paraguay as a 1-1 draw with no winner and the bracket couldn't
    advance the right side (JOE-16).

    Returns None — skip, don't corrupt — when teams can't be aligned, the result
    isn't a real result yet, or the feed's own ``winner`` contradicts the aligned
    score (a sign our name match is wrong).
    """
    ft = sc.get("fullTime") or {}
    if ft.get("home") is None or ft.get("away") is None:
        return None
    scores = _aligned_scores(home, away, ft["home"], ft["away"], team1, team2)
    if scores is None:
        return None
    s1, s2 = scores

    # Map football-data's authoritative winner onto our side (1/2), if it names one.
    home_side = _side_of_home(home, away, team1, team2)
    win_home = _FD_WINNER.get(sc.get("winner"))           # 'home' | 'away' | None
    win_side = None
    if win_home == "home":
        win_side = home_side
    elif win_home == "away":
        win_side = 3 - home_side if home_side else None

    # A penalties breakdown (present on some plans) is preferred when it's there.
    pen = sc.get("penalties") or {}
    if pen.get("home") is not None and pen.get("away") is not None:
        pens = _aligned_scores(home, away, pen["home"], pen["away"], team1, team2)
        if pens:
            return s1, s2, pens[0], pens[1]

    if s1 != s2:
        # Decisive on the pitch. If the feed names a winner, it must agree with
        # the score — disagreement means our home/away→team1/team2 match is wrong.
        score_side = 1 if s1 > s2 else 2
        if win_side is not None and win_side != score_side:
            return None
        return s1, s2, None, None

    # Level fullTime. For a knockout that means a shootout: encode the named
    # winner as a minimal (1-0) penalty result so the bracket advances correctly
    # even though football-data doesn't expose the actual shootout score. For a
    # group match a level result is a genuine draw — no winner, no penalties.
    if not is_knockout:
        return s1, s2, None, None
    if win_side == 1:
        return s1, s2, 1, 0
    if win_side == 2:
        return s1, s2, 0, 1
    return None  # level knockout with no winner named yet -> not actually settled


def _undecided_knockout(row):
    """True when ``row`` is a knockout the DB has marked finished but LEFT LEVEL
    with no shootout recorded — i.e. not actually resolved.

    openfootball's community feed regularly publishes a knockout's full-/extra-time
    score (``ft``/``et``) minutes-to-hours before it back-fills the penalty
    shootout (``p``). In that window the match is ``finished`` and level (e.g.
    Germany-Paraguay 1-1) with ``pen1``/``pen2`` still NULL, so ``winner_side`` is
    None and the bracket can't advance. We must keep consulting the OTHER data
    source (football-data, which names the shootout winner) to break that tie
    instead of treating the match as off-limits the instant openfootball finishes
    it (JOE-16)."""
    return (
        row["stage"] == "knockout"
        and row["status"] == "finished"
        and row["score1"] is not None
        and row["score1"] == row["score2"]
        and row["pen1"] is None
        and row["pen2"] is None
    )


def _update_from_football_data(conn, edition=editions.DEFAULT):
    """Best-effort overlay from football-data.org. Silent no-op without a key
    (or for an edition football-data.org has no competition feed for yet).

    openfootball is authoritative for final SCORES; this surfaces scores for
    matches openfootball hasn't settled, and — crucially — is still queried to
    supply the penalty-shootout winner for a knockout openfootball finished as a
    level result without one (so every data source is consulted before the
    bracket stalls or advances the wrong side). It only ever acts when team names
    align, so it can never corrupt a final.
    """
    if not config.FOOTBALL_DATA_API_KEY or not edition.football_data_url:
        return 0
    try:
        r = requests.get(
            edition.football_data_url,
            headers={"X-Auth-Token": config.FOOTBALL_DATA_API_KEY},
            timeout=15,
        )
        r.raise_for_status()
        payload = r.json()
    except Exception as exc:
        print(f"football-data.org skipped: {exc}")
        return 0

    changed = 0
    for fx in payload.get("matches", []):
        # football-data.org populates score.fullTime with the *live running*
        # score while a match is IN_PLAY/PAUSED — it is only the final result
        # once status is FINISHED. Marking anything else as finished advanced the
        # bracket prematurely (e.g. South Africa shown as beating Canada mid-game).
        if fx.get("status") != "FINISHED":
            continue
        sc = fx.get("score") or {}
        utc = fx.get("utcDate")  # e.g. 2026-06-11T19:00:00Z
        if not utc:
            continue
        iso = utc.replace("Z", "+00:00")
        try:
            key = datetime.fromisoformat(iso).isoformat()
        except ValueError:
            continue
        row = conn.execute(
            "SELECT num, team1, team2, status, stage, score1, score2, pen1, pen2 "
            "FROM matches WHERE utc_datetime=?",
            (key,)
        ).fetchone()
        if row is None:
            continue
        # openfootball owns a SETTLED final, but a knockout it finished as a level
        # result with no shootout is NOT settled — keep querying football-data for
        # the missing penalty winner so the bracket isn't left stalled (JOE-16).
        backfill_pens = _undecided_knockout(row)
        if row["status"] == "finished" and not backfill_pens:
            continue
        home, away = (fx.get("homeTeam") or {}).get("name"), (fx.get("awayTeam") or {}).get("name")
        decided = _decide_football_data(
            sc, home, away, row["team1"], row["team2"],
            is_knockout=(row["stage"] == "knockout"))
        if decided is None:
            continue  # can't align teams or no settled winner — skip, don't corrupt
        s1, s2, pen1, pen2 = decided
        if backfill_pens:
            # Only break the tie — never rewrite openfootball's authoritative
            # scoreline. Require both feeds to agree the match is level and that
            # football-data actually names a shootout winner, else leave it alone.
            if (s1, s2) != (row["score1"], row["score2"]) or (pen1 is None and pen2 is None):
                continue
            conn.execute(
                "UPDATE matches SET pen1=?, pen2=? WHERE num=?",
                (pen1, pen2, row["num"]),
            )
        else:
            conn.execute(
                "UPDATE matches SET score1=?, score2=?, pen1=?, pen2=?, status='finished' "
                "WHERE num=?",
                (s1, s2, pen1, pen2, row["num"]),
            )
        changed += 1
    conn.commit()
    return changed


def main(prefer_remote=True, edition=editions.DEFAULT):
    if not edition.openfootball_url and not edition.football_data_url:
        # nothing to poll for this edition yet (women's 2027 pre-dataset) —
        # skip quietly instead of hammering feeds that don't exist
        print(f"[{edition.key}] no data feeds configured yet; skipped.")
        return
    conn = db.connect(edition.db_path)
    # Idempotently apply the schema first so a deploy that adds a new table
    # (e.g. `scorers` for the Golden Boot tracker) reaches an already-seeded
    # production DB on the next cron run — pull + restart is then enough, no
    # manual migration needed. CREATE TABLE IF NOT EXISTS makes this a no-op
    # once the table exists.
    db.init_schema(conn)
    a = _update_from_openfootball(conn, prefer_remote=prefer_remote, edition=edition)
    b = _update_from_football_data(conn, edition=edition)
    compute.recompute_all(conn)
    n_finished = conn.execute(
        "SELECT COUNT(*) FROM matches WHERE status='finished'").fetchone()[0]
    conn.close()
    print(f"Updated [{edition.key}] {a} (openfootball) + {b} (football-data) "
          f"matches; {n_finished} finished total.")


if __name__ == "__main__":
    # one cron entry refreshes every edition that has a live feed
    for _key, _edition in editions.EDITIONS.items():
        main(prefer_remote="--offline" not in sys.argv, edition=_edition)
