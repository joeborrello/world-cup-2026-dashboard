"""Flag coverage (JOE-52): every real team in every edition's dataset renders
a flag image; bracket slot placeholders (1A, W49, ...) render nothing.

The women's 2027 dataset introduced ten teams that never appear in the men's
edition (China, Denmark, Iceland, ...) — this pins that CODES keeps up with
whatever teams the datasets contain, so a new edition or a dataset refresh
can't silently ship flagless team rows.
"""

import json
import re

import pytest
from markupsafe import escape

import editions
import flags

SLOT_RE = re.compile(r"^([12][A-H]|[WL]\d+)$")


def dataset_teams(edition):
    """All team1/team2 strings in an edition's local dataset."""
    with open(edition.openfootball_local, encoding="utf-8") as fh:
        data = json.load(fh)
    teams = set()
    for m in data["matches"]:
        for side in ("team1", "team2"):
            t = m.get(side)
            if isinstance(t, dict):
                t = t.get("name")
            if isinstance(t, str):
                teams.add(t)
    return teams


@pytest.mark.parametrize("key", sorted(editions.EDITIONS))
def test_every_real_team_has_a_flag(key):
    teams = dataset_teams(editions.get(key))
    real = {t for t in teams if not SLOT_RE.match(t)}
    assert real, f"no teams found in {key} dataset"
    missing = sorted(t for t in real if not flags.flag_code(t))
    assert not missing, f"{key} teams without a flag code: {missing}"


@pytest.mark.parametrize("key", sorted(editions.EDITIONS))
def test_every_real_team_renders_flag_img(key):
    teams = dataset_teams(editions.get(key))
    for t in sorted(teams):
        html = flags.flag(t)
        if SLOT_RE.match(t):
            assert html == "", f"slot placeholder {t!r} should render empty"
        else:
            assert "flagcdn.com" in html and str(escape(t)) in html


def test_womens_debut_teams_have_flags():
    """The ten teams the women's edition added over the men's pool."""
    debutants = {
        "China": "cn", "Denmark": "dk", "Iceland": "is", "Italy": "it",
        "Jamaica": "jm", "Nigeria": "ng", "North Korea": "kp",
        "Venezuela": "ve", "Vietnam": "vn", "Zambia": "zm",
    }
    for team, code in debutants.items():
        assert flags.flag_code(team) == code
        assert team in flags.FLAGS  # emoji fallback table stays in sync


def test_codes_and_emoji_tables_cover_same_teams():
    assert set(flags.CODES) == set(flags.FLAGS)


def test_unknown_team_renders_empty():
    assert flags.flag("Winner of Group Q") == ""
    assert flags.flag(None) == ""
    assert flags.flag_code("") is None
