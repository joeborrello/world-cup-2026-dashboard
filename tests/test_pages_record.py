"""The GitHub Pages landing site must record the full build timeline (JOE-57).

The "How it was built" section is the project's public build record. These
checks pin that the timeline runs contiguously from Stage 0 through the final
stage (women's 2027), and that the newest features surface in the showcase —
so the record can't silently fall behind the app again.
"""

import os
import re

HERE = os.path.dirname(__file__)
PAGES = os.path.join(os.path.dirname(HERE), "docs", "index.html")

with open(PAGES, encoding="utf-8") as fh:
    HTML = fh.read()

LAST_STAGE = 24


def test_timeline_stages_are_contiguous_from_0_to_last():
    stages = sorted(int(n) for n in re.findall(r"Stage (\d+) ·", HTML))
    assert stages == list(range(LAST_STAGE + 1)), \
        f"timeline has gaps or duplicates: {stages}"


def test_timeline_records_the_post_tournament_era():
    # the big post-Stage-16 additions must appear in the build record
    for marker in ("What-if scenario mapper", "Roll back", "editions abstraction",
                   "Women's World Cup 2027"):
        assert marker in HTML, f"build record is missing: {marker}"


def test_showcase_links_to_the_newest_features():
    assert "https://droplet.josephborrello.com/worldcup/what-if" in HTML
    assert "https://droplet.josephborrello.com/worldcup/women/" in HTML
