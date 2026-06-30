"""Match cards show the penalty-shootout winner (JOE-26).

JOE-16 taught the data layer to *store* a knockout decided on penalties (the
Germany–Paraguay Round-of-32 match finished 1–1 and Paraguay won the shootout
4–3). But the match cards still rendered only the level 1–1 scoreline, so a
shootout result read as a draw with no winner. These tests pin the *display*:

  * the home/"Today" `match_card` macro shows each side's shootout score and a
    "won … on penalties" caption, and flags the winning row,
  * the GitHub Pages landing payload carries the penalty fields, and
  * the landing strip's own render() names the shootout winner.
"""

import json
import os
import shutil
import subprocess
import sqlite3
import tempfile

import pytest

import app as flask_app
import publish_pages


BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _render_card(m):
    """Render the match_card macro for a single match dict and return its HTML."""
    tmpl = flask_app.app.jinja_env.get_template("macros.html")
    return tmpl.module.match_card(m)


def _base_match(**over):
    m = {
        "num": 74, "stage": "knockout", "round": "Round of 32", "group": None,
        "date": "2026-06-29", "local_time": "12:00 PM", "utc_offset": "+00:00",
        "utc_datetime": "2026-06-29T16:00:00+00:00",
        "team1": "Germany", "team2": "Paraguay",
        "team1_resolved": True, "team2_resolved": True,
        "team1_code": "de", "team2_code": "py",
        "score1": 1, "score2": 1, "pen1": 3, "pen2": 4,
        "winner_side": 2, "status": "finished",
        "stadium": "MetLife Stadium", "city": "East Rutherford",
    }
    m.update(over)
    return m


# ── 1. the home/"Today" match card ───────────────────────────────────────────

def test_card_shows_shootout_score_for_each_side():
    html = _render_card(_base_match())
    # the standing 1–1 is still shown, with the shootout score in parentheses
    assert '<span class="pens">(3)</span>' in html
    assert '<span class="pens">(4)</span>' in html


def test_card_flags_the_shootout_winner_row():
    html = _render_card(_base_match())
    # Paraguay (team2) won the shootout, so only the second row carries `win`
    rows = html.split('<div class="row')
    assert " win" in rows[2]            # Paraguay's row
    assert " win" not in rows[1]        # Germany's row


def test_card_names_the_penalty_winner():
    html = _render_card(_base_match())
    assert "match-pens" in html
    assert "Paraguay won 4–3 on penalties" in html


def test_card_other_winner_side():
    html = _render_card(_base_match(winner_side=1, pen1=5, pen2=4))
    assert "Germany won 5–4 on penalties" in html
    rows = html.split('<div class="row')
    assert " win" in rows[1] and " win" not in rows[2]


def test_card_plain_finished_match_has_no_penalty_chrome():
    html = _render_card(_base_match(
        score1=2, score2=0, pen1=None, pen2=None, winner_side=1))
    assert "pens" not in html
    assert "match-pens" not in html


def test_card_scheduled_match_shows_no_scores():
    html = _render_card(_base_match(
        score1=None, score2=None, pen1=None, pen2=None,
        winner_side=None, status="scheduled"))
    assert "pens" not in html
    assert "match-pens" not in html
    # no score digits rendered for an unplayed match
    assert '<span class="score" data-score="1"></span>' in html


# ── 2. the GitHub Pages landing payload carries the penalty fields ───────────

def _conn_with(row):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    import db
    db.init_schema(conn)
    cols = ", ".join(row)
    ph = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO matches ({cols}) VALUES ({ph})", tuple(row.values()))
    # a venue so flag_code/_today don't choke on the join (defensive)
    conn.commit()
    return conn


def test_today_payload_includes_penalty_winner(monkeypatch):
    import config
    monkeypatch.setattr(config, "tournament_today", lambda: __import__("datetime").date(2026, 6, 29))
    row = {
        "num": 74, "stage": "knockout", "round_label": "Round of 32",
        "date": "2026-06-29", "utc_datetime": "2026-06-29T16:00:00+00:00",
        "team1_slot": "Germany", "team2_slot": "Paraguay",
        "team1": "Germany", "team2": "Paraguay",
        "score1": 1, "score2": 1, "pen1": 3, "pen2": 4, "status": "finished",
    }
    conn = _conn_with(row)
    today = publish_pages._today(conn)
    assert len(today) == 1
    entry = today[0]
    assert (entry["pen1"], entry["pen2"]) == (3, 4)
    assert entry["winner_side"] == 2          # Paraguay


# ── 3. the landing strip's own render() names the shootout winner ────────────

PAGE = os.path.join(BASE, "docs", "index.html")

SAMPLE = {
    "phase": "Round of 32",
    "generated": "2026-06-29T18:30:00Z",
    "today": [
        {"num": 74, "team1": "Germany", "team2": "Paraguay",
         "code1": "de", "code2": "py", "score1": 1, "score2": 1,
         "pen1": 3, "pen2": 4, "winner_side": 2,
         "status": "finished", "state": None,
         "utc_datetime": "2026-06-29T16:00:00Z"},
    ],
    "title_odds": [],
}

HARNESS = r"""
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.error('no inline <script> found'); process.exit(2); }
const SAMPLE = JSON.parse(process.argv[3]);
const els = {};
const mk = () => ({ className: '', textContent: '', innerHTML: '', hidden: true });
global.document = { getElementById: (id) => (els[id] = els[id] || mk()) };
global.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve(SAMPLE) });
global.setInterval = () => 0;
eval(m[1]);
setTimeout(() => { process.stdout.write(els['lsToday'].innerHTML); }, 50);
"""


def _render_strip():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to exercise the landing page's render()")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(HARNESS)
        harness_path = fh.name
    try:
        out = subprocess.run(
            [node, harness_path, PAGE, json.dumps(SAMPLE)],
            capture_output=True, text=True, timeout=30)
    finally:
        os.unlink(harness_path)
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_landing_strip_shows_penalty_score_and_bolds_winner():
    html = _render_strip()
    assert "ls-pk" in html                    # the "(3–4 pens)" annotation
    # the shootout score follows team1–team2 order to match the scoreline and the
    # left-to-right layout: Germany (team1) 3, Paraguay (team2) 4 — NOT max–min,
    # which used to read as Germany winning 4–3 (JOE-31).
    assert "3–4 pens" in html
    assert "4–3 pens" not in html
    # Paraguay is bolded as the shootout winner; Germany is not
    assert '<b class="ls-win">Paraguay</b>' in html
    assert '<b class="ls-win">Germany</b>' not in html


def test_landing_strip_penalty_order_matches_team_layout():
    """The shootout digits line up with the teams left→right: the loser's count is
    never printed before the winner's just because it's the smaller number. With
    Germany (team1) on the left, the pens read `3–4` and the score row reads
    Germany … 3–4 pens … Paraguay (so Paraguay's 4 sits on Paraguay's side)."""
    html = _render_strip()
    pk_idx = html.index("3–4 pens")
    g_idx = html.index("Germany")
    p_idx = html.index("Paraguay")
    # Germany appears before the pens annotation, Paraguay after it
    assert g_idx < pk_idx < p_idx, html


def test_landing_strip_css_defines_penalty_styles():
    with open(PAGE, encoding="utf-8") as fh:
        css = fh.read()
    assert ".ls-pk{" in css
    assert ".ls-win{" in css
