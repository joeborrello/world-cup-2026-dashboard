"""Layout of the GitHub Pages live strip's match rows (JOE-18).

The landing page (`docs/index.html`) renders today's matches in a "live strip".
Originally the LIVE / HT indicator was rendered *between* the two teams playing
(right after the score, before team 2). JOE-18 moved that indicator to the
left-hand side of the matchup and wrapped the two teams and the current score in
a thin box, so the row now reads:

    [LIVE]  ┌ flag Team1  score  flag Team2 ┐

These tests pin that contract down. Because the row HTML is produced by the
page's own inline `render()` (vanilla JS, no framework), we execute that real
function under Node against a stubbed DOM and assert on the markup it emits —
rather than re-implementing the rendering in Python, which could drift.
"""

import json
import os
import shutil
import subprocess
import tempfile

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(BASE, "docs", "index.html")

# A small, fixed slate: one in-play match, one at half-time, one not started.
SAMPLE = {
    "phase": "Group stage",
    "generated": "2026-06-29T15:30:00Z",
    "today": [
        {"num": 1, "team1": "Brazil", "team2": "Spain", "code1": "br", "code2": "es",
         "score1": 1, "score2": 2, "status": "scheduled", "state": "in_play",
         "utc_datetime": "2026-06-29T16:00:00Z"},
        {"num": 2, "team1": "France", "team2": "Japan", "code1": "fr", "code2": "jp",
         "score1": 0, "score2": 0, "status": "scheduled", "state": "paused",
         "utc_datetime": "2026-06-29T18:00:00Z"},
        {"num": 3, "team1": "Italy", "team2": "Ghana", "code1": "it", "code2": "gh",
         "score1": None, "score2": None, "status": "scheduled", "state": None,
         "utc_datetime": "2026-06-29T20:00:00Z"},
    ],
    "title_odds": [],
}

# Node harness: pull the page's inline <script>, stub just enough DOM/network to
# run it, drive render() through the real fetch->render path, and print the
# rendered #lsToday markup as the last line of stdout.
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


def _render_rows():
    """Run the page's real render() and return the #lsToday inner HTML."""
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


def _row(html, team):
    """Return the single `<span class="ls-m">…` row that mentions `team`.

    Rows are siblings (they don't nest), so splitting on the row marker yields
    one chunk per match; we return the chunk containing the team name."""
    marker = '<span class="ls-m">'
    chunks = [marker + c for c in html.split(marker)[1:]]
    matching = [c for c in chunks if team in c]
    assert len(matching) == 1, f"expected one row for {team}, got {len(matching)}"
    return matching[0]


def test_live_indicator_is_on_the_left_not_between_teams():
    """For an in-play match the LIVE badge precedes team 1 (left of the matchup),
    and never sits between the two team names."""
    html = _render_rows()
    row = _row(html, "Brazil")
    badge = row.index('class="ls-badge">LIVE')
    team1 = row.index("Brazil")
    score = row.index('class="ls-sc"')
    team2 = row.index("Spain")
    # badge first, then team1, score, team2 in reading order
    assert badge < team1 < score < team2, row
    # the old layout placed the badge between the score and team 2 — it must not
    # appear anywhere after team 1 now
    assert "ls-badge" not in row[team1:], row


def test_teams_and_score_share_a_thin_box():
    """The two teams and the current score live inside a single .ls-box, which is
    flagged `on` for a live match (so it can be visually emphasised)."""
    html = _render_rows()
    row = _row(html, "Brazil")
    assert 'class="ls-box on"' in row
    box_start = row.index('class="ls-box')
    box_inner_end = row.index("</span>", row.index("Spain"))
    box = row[box_start:box_inner_end]
    # team1, score and team2 are all within the box
    assert "Brazil" in box and "Spain" in box and 'class="ls-sc">1–2' in box
    # the LIVE badge is outside (before) the box
    assert row.index('class="ls-badge') < box_start


def test_half_time_match_uses_ht_badge_on_the_left():
    """A PAUSED match shows an HT badge, also to the left of a boxed matchup."""
    html = _render_rows()
    row = _row(html, "France")
    assert 'class="ls-badge">HT' in row
    assert 'class="ls-box on"' in row
    assert row.index("ls-badge") < row.index("France")


def test_scheduled_match_has_box_but_no_badge():
    """A not-yet-started match still gets the thin box (kept visually consistent)
    but no LIVE/HT badge, and the box is not flagged `on`."""
    html = _render_rows()
    row = _row(html, "Italy")
    assert "ls-badge" not in row
    assert 'class="ls-box"' in row          # plain box, no `on`
    assert 'class="ls-box on"' not in row
    assert 'class="ls-vs"' in row           # kickoff time, not a score


def test_box_style_is_defined_in_page_css():
    """The thin box has actual styling so it renders as a box, not bare text."""
    with open(PAGE, encoding="utf-8") as fh:
        css = fh.read()
    assert ".ls-box{" in css
    assert ".ls-box.on{" in css
