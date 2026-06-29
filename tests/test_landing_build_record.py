"""Tests for the GitHub Pages landing page build record (docs/index.html).

JOE-19 brings the "How it was built" timeline and the "What's inside" feature
cards back in sync with the features that shipped after Stage 12 (local time):

  - Stage 13: a steerable/interactive projected bracket (JOE-10, JOE-11)
  - Stage 14: the Golden Boot tracker (JOE-13)
  - Stage 15: kickoff weather on the follow-a-team map (JOE-15)
  - Stage 16: faster live refresh + minute of play (JOE-17)

These tests pin the timeline so the published build record can't silently fall
behind the repo again, and check the headline features are surfaced as cards.
"""

import os
import re

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as fh:
        return fh.read()


def _html():
    return _read('docs', 'index.html')


# ── build-record timeline ────────────────────────────────────────────────────

def _stage_tags(html):
    """Every 'Stage N · …' tag in timeline order, as (number, label) pairs."""
    return [(int(n), label.strip())
            for n, label in re.findall(r'Stage (\d+)\s*·\s*([^<]+)</div>', html)]


def test_timeline_extends_past_stage_12():
    nums = [n for n, _ in _stage_tags(_html())]
    assert nums, 'no Stage tags found in the timeline'
    assert max(nums) >= 16, f'build record stops too early (max stage {max(nums)})'


def test_timeline_stage_numbers_contiguous():
    """Stages run 0,1,2,… with no gaps or duplicates, so the record reads cleanly."""
    nums = [n for n, _ in _stage_tags(_html())]
    assert nums == list(range(0, max(nums) + 1))


def test_new_stage_headings_present():
    html = _html()
    # the four features that shipped after Stage 12, each as a timeline entry
    assert 'Steerable projected bracket' in html
    assert 'Golden Boot tracker' in html
    assert 'Kickoff weather on the team map' in html
    assert 'minute of play' in html.lower()


def test_interactive_bracket_stage_described():
    html = _html()
    # the what-if mechanic, not just the title
    assert 'force it' in html
    assert 'Reset picks' in html


def test_golden_boot_stage_rules_described():
    html = _html()
    # the headline Golden Boot rules + the projection framing
    assert "own goals don't" in html
    assert 'whole-goal range' in html


# ── "What's inside" feature cards ────────────────────────────────────────────

def test_golden_boot_card_links_to_live_route():
    html = _html()
    # the card must point at the real Flask route (/golden-boot under /worldcup/)
    assert '/worldcup/golden-boot' in html
    assert '/worldcup/goldenboot"' not in html  # the route is hyphenated


def test_interactive_bracket_card_present():
    html = _html()
    assert 'Interactive bracket' in html


def test_team_map_card_mentions_kickoff_weather():
    html = _html()
    card = re.search(r'Follow-a-team map</h3>\s*<p>([^<]+)</p>', html)
    assert card, 'follow-a-team card not found'
    assert 'kickoff weather' in card.group(1)


def test_live_ticker_card_mentions_minute_and_refresh():
    html = _html()
    card = re.search(r'Live score ticker</h3>\s*<p>([^<]+)</p>', html)
    assert card, 'live ticker card not found'
    body = card.group(1)
    assert 'minute of play' in body
    assert '5 minutes' in body
