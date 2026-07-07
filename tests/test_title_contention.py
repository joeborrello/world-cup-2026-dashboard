"""Teams out of title contention are flagged and crossed out (JOE-43).

The Title Odds table on /predictions used to keep listing teams that could no
longer win the tournament (knockout losers, group-stage exits) as if they were
still in the race. The fix:

  * predict.py computes a deterministic per-team ``eliminated`` flag — judged
    only from decisively finished results, NEVER from Monte-Carlo sampling (a
    live longshot whose title odds round to 0.0% must not be branded out).
  * static/js/predictions.js sorts teams still in contention first and renders
    eliminated rows crossed out with an "out" badge.

The Python tests pin the elimination rules; the Node test runs the page's real
render() against a stubbed DOM (same pattern as test_live_strip_layout.py) and
asserts on the emitted markup.
"""

import json
import os
import shutil
import subprocess
import tempfile

import pytest

import db
import predict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(BASE, "static", "js", "predictions.js")


# ── _eliminated_teams unit tests (fabricated fixtures) ──────────────────────

def _gm(num, group, t1, t2, status="finished", s1=1, s2=0):
    if status != "finished":
        s1 = s2 = None
    return {"num": num, "group": group, "team1": t1, "team2": t2,
            "status": status, "score1": s1, "score2": s2}


def _ko(num, t1, t2, status="scheduled", s1=None, s2=None, p1=None, p2=None):
    return {"num": num, "slot1": "W0", "slot2": "W0", "status": status,
            "team1": t1, "team2": t2, "score1": s1, "score2": s2,
            "pen1": p1, "pen2": p2}


TEAMS = {"AA": "A", "AB": "A", "BA": "B", "BB": "B"}


def test_knockout_loser_is_eliminated():
    ko = [_ko(89, "AA", "BA", status="finished", s1=2, s2=0)]
    out = predict._eliminated_teams([], ko, TEAMS)
    assert out == {"BA"}


def test_shootout_loser_is_eliminated():
    ko = [_ko(89, "AA", "BA", status="finished", s1=1, s2=1, p1=3, p2=4)]
    out = predict._eliminated_teams([], ko, TEAMS)
    assert out == {"AA"}


def test_level_knockout_without_penalties_eliminates_nobody():
    """A finished-but-level match with no shootout recorded decides nothing yet
    (the JOE-16 rule) — neither side may be declared out on it."""
    ko = [_ko(89, "AA", "BA", status="finished", s1=1, s2=1)]
    assert predict._eliminated_teams([], ko, TEAMS) == set()


def test_unplayed_knockout_eliminates_nobody():
    ko = [_ko(89, "AA", "BA")]
    assert predict._eliminated_teams([], ko, TEAMS) == set()


def test_no_group_elimination_while_group_stage_is_in_progress():
    """A team on zero points is mathematically hard to call (best-thirds span
    groups), so nobody is eliminated on group results until the stage is done."""
    fixtures = [_gm(1, "A", "AA", "AB"),
                _gm(2, "B", "BA", "BB", status="scheduled")]
    r32 = [_ko(73, "AA", "BA")]
    assert predict._eliminated_teams(fixtures, r32, TEAMS) == set()


def test_group_stage_complete_eliminates_teams_outside_the_r32_field():
    fixtures = [_gm(1, "A", "AA", "AB"), _gm(2, "B", "BA", "BB")]
    r32 = [_ko(73, "AA", "BA")]  # AB and BB missed the knockout field
    out = predict._eliminated_teams(fixtures, r32, TEAMS)
    assert out == {"AB", "BB"}


def test_no_group_elimination_until_every_r32_slot_is_resolved():
    """Best-third assignment can lag the final whistle; until the feed names
    all R32 teams, missing from a partial field proves nothing."""
    fixtures = [_gm(1, "A", "AA", "AB"), _gm(2, "B", "BA", "BB")]
    r32 = [_ko(73, "AA", "BA"), _ko(74, None, None)]
    assert predict._eliminated_teams(fixtures, r32, TEAMS) == set()


# ── integration: the API payload carries a sound flag ────────────────────────

@pytest.fixture(scope="module")
def pred():
    conn = db.connect()
    try:
        return predict.predictions(conn, sims=500, seed=1)
    finally:
        conn.close()


def test_every_team_carries_an_eliminated_bool(pred):
    assert pred["teams"]
    for t, v in pred["teams"].items():
        assert isinstance(v["eliminated"], bool), t


def test_eliminated_teams_have_zero_title_odds(pred):
    """Elimination is judged from fixed results, and the sims hold those fixed —
    so a team flagged out must never win a single simulation."""
    for t, v in pred["teams"].items():
        if v["eliminated"]:
            assert v["champion"] == 0, t


def test_teams_in_undecided_knockouts_are_never_flagged(pred):
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT team1, team2 FROM matches WHERE stage='knockout' "
            "AND status != 'finished'").fetchall()
    finally:
        conn.close()
    still_playing = {t for r in rows for t in (r["team1"], r["team2"]) if t}
    for t in still_playing:
        assert not pred["teams"][t]["eliminated"], t


# ── the page's real render(): eliminated rows are crossed out ────────────────

def _team(group, code, champion, adv=1.0, r16=0.0, qf=0.0, sf=0.0, final=0.0,
          eliminated=False):
    return {"group": group, "code": code, "elo": 1800, "elo_prior": 1800,
            "advance": adv, "r16": r16, "qf": qf, "sf": sf, "final": final,
            "champion": champion, "eliminated": eliminated}


SAMPLE = {
    "sims": 1000, "n_finished": 90,
    "teams": {
        "Alivia": _team("A", "aa", 0.40, r16=1, qf=1, sf=1),
        # a live longshot: odds round to zero but they are NOT eliminated
        "Longshotia": _team("B", "bb", 0.0, r16=1, qf=0.2),
        # a semifinal loser — deep run, but out
        "Semiland": _team("C", "cc", 0.0, r16=1, qf=1, sf=1, eliminated=True),
        # a group-stage exit — out, shallow run
        "Groupout": _team("D", "dd", 0.0, eliminated=True),
    },
}

HARNESS = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const SAMPLE = JSON.parse(process.argv[3]);

const els = {};
const mk = () => ({ className: '', textContent: '', innerHTML: '',
                    addEventListener: () => {} });
global.document = { getElementById: (id) => (els[id] = els[id] || mk()) };
global.window = { WC: { predUrl: '/pred', punditUrl: '/p', budgetUrl: '/b' } };
global.fetch = (url) => Promise.resolve({
  ok: true,
  json: () => Promise.resolve(url === '/pred' ? SAMPLE : { enabled: false }),
});

eval(src);

setTimeout(() => { process.stdout.write(els['titleTable'].innerHTML); }, 50);
"""


def _render_table():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to exercise the predictions page render()")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(HARNESS)
        harness_path = fh.name
    try:
        out = subprocess.run(
            [node, harness_path, JS, json.dumps(SAMPLE)],
            capture_output=True, text=True, timeout=30)
    finally:
        os.unlink(harness_path)
    assert out.returncode == 0, out.stderr
    return out.stdout


def _row(html, team):
    rows = [c for c in html.split("<tr") if team in c]
    assert len(rows) == 1, f"expected one row for {team}, got {len(rows)}"
    return rows[0]


def test_eliminated_rows_are_marked_and_struck_through():
    html = _render_table()
    for team in ("Semiland", "Groupout"):
        row = _row(html, team)
        assert 'class="ot-out"' in row, row
        assert f'<span class="ot-name">{team}</span>' in row, row
        assert 'class="ot-elim">out</span>' in row, row


def test_live_teams_are_not_crossed_out_even_at_zero_odds():
    html = _render_table()
    for team in ("Alivia", "Longshotia"):
        row = _row(html, team)
        assert "ot-out" not in row, row
        assert "ot-elim" not in row, row


def test_contenders_sort_above_the_eliminated():
    """Alive teams come first (even a 0.0% longshot), then eliminated teams
    ordered by how deep their run went."""
    html = _render_table()
    order = [html.index(t) for t in
             ("Alivia", "Longshotia", "Semiland", "Groupout")]
    assert order == sorted(order), html
