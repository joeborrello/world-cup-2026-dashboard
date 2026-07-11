"""JOE-48: the 2027 Women's World Cup is mounted at /women.

The same blueprint is registered once per edition — the men's 2026 tournament
keeps the site root, the women's 2027 edition serves under /women — and every
page carries its own edition's branding. Since JOE-49 the women's DB is seeded
from the committed provisional snapshot (data/wwc-2027.json), so these tests
see fixtures under /women; the pre-draw (empty schedule) state that every page
must survive is covered explicitly by the gating test at the bottom.
"""

import pytest

import app as app_module
import editions
import ratings
import venues


@pytest.fixture()
def client():
    return app_module.app.test_client()


PAGES = ["/", "/groups", "/bracket", "/predictions", "/what-if",
         "/golden-boot", "/map", "/schedule-map", "/team-map"]


# ---------------------------------------------------------------- the edition

def test_women_edition_is_registered_under_women():
    w = editions.WOMEN
    assert editions.EDITIONS["women"] is w
    assert w.url_prefix == "/women"
    assert w.db_path != editions.MEN.db_path      # its own database
    assert w.venues is venues.WOMENS_VENUES
    assert w.elo is ratings.WOMENS_ELO
    assert w.elo_hosts == frozenset({"Brazil"})
    assert w.pre_draw_note                        # shown until the draw exists


def test_womens_venues_are_the_eight_brazilian_stadiums():
    assert len(venues.WOMENS_VENUES) == 8
    for ground, v in venues.WOMENS_VENUES.items():
        assert v["country"] == "Brazil", ground
        # same shape the seeder / maps expect of the men's table
        assert set(v) == {"stadium", "city", "country", "lat", "lng", "tz", "roof"}
        assert -34 < v["lat"] < 6 and -74 < v["lng"] < -34   # inside Brazil


def test_womens_elo_priors_are_on_the_same_scale():
    assert ratings.WOMENS_ELO, "priors table must not be empty"
    assert all(1400 < e < 2400 for e in ratings.WOMENS_ELO.values())
    assert ratings.WOMENS_HOSTS == {"Brazil"}
    assert "Brazil" in ratings.WOMENS_ELO


# ---------------------------------------------------------------- the mount

def test_every_page_serves_in_both_editions(client):
    for page in PAGES:
        for prefix in ("", "/women"):
            url = (prefix + page) if page != "/" else (prefix + "/" if prefix else "/")
            r = client.get(url)
            assert r.status_code == 200, f"{url} -> {r.status_code}"


def test_api_routes_serve_in_both_editions(client):
    for api in ["/api/venues", "/api/teams", "/api/matches", "/api/standings",
                "/api/bracket", "/api/days"]:
        for prefix in ("", "/women"):
            r = client.get(prefix + api)
            assert r.status_code == 200, f"{prefix + api} -> {r.status_code}"


def test_editions_read_their_own_databases(client):
    men = client.get("/api/venues").get_json()
    women = client.get("/women/api/venues").get_json()
    assert len(men) == 16
    assert len(women) == 8
    assert {v["country"] for v in women} == {"Brazil"}
    # each edition serves its own schedule: the men's 104 fixtures at the
    # root, the 64 provisional women's fixtures (JOE-49) under /women
    men_matches = client.get("/api/matches").get_json()
    women_matches = client.get("/women/api/matches").get_json()
    assert len(men_matches) == 104
    assert len(women_matches) == 64
    assert all(m["date"].startswith("2027-") for m in women_matches)


# ---------------------------------------------------------------- branding

def test_womens_pages_carry_womens_branding(client):
    html = client.get("/women/").get_data(as_text=True)
    assert "World Cup 2027" in html
    assert "🇧🇷" in html
    assert editions.WOMEN.footer in html
    # 2026 branding appears only as the edition switcher's link to the men's
    # site, never as this page's own title/footer
    assert editions.MEN.footer not in html
    assert "<title>Today · World Cup 2026" not in html


def test_mens_pages_are_unchanged(client):
    html = client.get("/").get_data(as_text=True)
    assert "World Cup 2026" in html
    assert "🇨🇦 🇺🇸 🇲🇽" in html
    assert editions.MEN.footer in html


def test_edition_switcher_links_the_same_page_in_the_other_edition(client):
    men_groups = client.get("/groups").get_data(as_text=True)
    women_groups = client.get("/women/groups").get_data(as_text=True)
    assert "edition-switch" in men_groups and "edition-switch" in women_groups
    assert "/worldcup/women/groups" in men_groups     # SUBPATH-prefixed URLs
    assert "/worldcup/groups" in women_groups


def test_pre_draw_note_gates_on_missing_fixtures(client, tmp_path, monkeypatch):
    """The note shows only while an edition has no fixtures. Both editions now
    seed a schedule (women's: the JOE-49 provisional snapshot), so neither
    shows it — but an edition whose draw isn't published yet still must."""
    assert "pre-draw-note" not in client.get("/women/").get_data(as_text=True)
    assert "pre-draw-note" not in client.get("/").get_data(as_text=True)

    import dataclasses
    import seed_data
    pre_draw = dataclasses.replace(
        editions.WOMEN, db_path=str(tmp_path / "pre-draw.db"),
        openfootball_local=str(tmp_path / "no-feed.json"))   # no snapshot
    seed_data.seed(prefer_remote=False, edition=pre_draw)    # venues, 0 matches
    monkeypatch.setitem(editions.EDITIONS, "women", pre_draw)
    html = client.get("/women/").get_data(as_text=True)
    assert "pre-draw-note" in html
    assert editions.WOMEN.pre_draw_note[:40] in html


def test_format_copy_follows_the_edition(client):
    women_groups = client.get("/women/groups").get_data(as_text=True)
    assert "Round of 16" in women_groups              # 32-team opening round
    assert "3rd-placed" not in women_groups           # no wildcard slots
    men_groups = client.get("/groups").get_data(as_text=True)
    assert "Round of 32" in men_groups
    assert "8 best 3rd-placed teams" in men_groups
