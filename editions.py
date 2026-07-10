"""Tournament editions served by this dashboard.

Everything edition-specific — database file, data feeds, tournament dates,
venues, Elo priors, branding — lives on an Edition object instead of on
module-level 2026 constants, so the backend (db.py, seed_data.py, and
eventually the routes) can serve more than one World Cup from one codebase.
The men's 2026 tournament is currently the only registered edition; the 2027
Women's World Cup joins the registry once its data and routes land.

The engine itself (compute.py / predict.py) stays *format*-agnostic by reading
the tournament's shape from the data (round labels, wildcard slots) rather than
from these objects; an Edition only says where the data lives and how the site
is branded, not how the bracket works.
"""

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import config
import ratings
import venues


@dataclass(frozen=True)
class Edition:
    key: str                    # 'men' | 'women' — also the blueprint name
    title: str                  # "World Cup 2026"
    nav_label: str              # short label for the edition switcher
    hosts_flags: str            # emoji flags shown next to the brand
    og_description: str
    footer: str
    db_path: str
    openfootball_url: str       # '' = no remote feed published yet
    openfootball_local: str     # committed offline snapshot (seed fallback)
    football_data_url: str      # '' = no live-score/enrichment feed yet
    start: str                  # tournament span (map day-slider bounds)
    end: str
    today_tz: str               # "today" anchor timezone (westmost host)
    venues: dict = field(repr=False)
    elo: dict = field(repr=False)
    url_prefix: str = ''        # blueprint mount point ('' = site root)
    prompt_name: str = ''       # how AI prompts name the tournament
    region: str = ''            # where it's played (follow-a-team blurb)
    opening_round: str = ''     # first knockout round label (pre-data fallback)
    groups_blurb: str = ''      # quicklink card copy (format-specific counts)
    bracket_blurb: str = ''
    cities_blurb: str = ''
    whatif_placeholder: str = ''
    whatif_examples: tuple = ()
    elo_hosts: frozenset = frozenset()
    og_image: str = ''          # static path of the link-preview image ('' = none)
    pre_draw_note: str = ''     # shown while the tournament has no fixtures yet

    def tournament_today(self):
        """Current date in this edition's reference timezone (datetime.date)."""
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(self.today_tz)
        except Exception:               # no tzdata -> fall back to a fixed offset
            tz = timezone(timedelta(hours=-7 if self.key == 'men' else -3))
        return datetime.now(tz).date()


MEN = Edition(
    key='men',
    title='World Cup 2026',
    nav_label="Men's 2026",
    hosts_flags='🇨🇦 🇺🇸 🇲🇽',
    og_description=(
        'Follow the 2026 FIFA World Cup in one place — a live bracket and group '
        'tables, interactive maps, match-day weather, a live score ticker, a '
        'Golden Boot tracker, and an Elo prediction engine.'),
    footer=('Jun 11 – Jul 19, 2026 · 48 teams · 16 cities · 104 matches · '
            'times shown in your local timezone'),
    db_path=config.DB_PATH,
    openfootball_url=config.OPENFOOTBALL_URL,
    openfootball_local=config.OPENFOOTBALL_LOCAL,
    football_data_url=config.FOOTBALL_DATA_URL,
    start=config.TOURNAMENT_START,
    end=config.TOURNAMENT_END,
    today_tz=os.environ.get('TOURNAMENT_TZ', 'America/Los_Angeles'),
    venues=venues.VENUES,
    elo=ratings.ELO,
    url_prefix='',
    prompt_name='the 2026 World Cup',
    region='North America',
    opening_round='Round of 32',
    groups_blurb='All 12 group standings, live.',
    bracket_blurb='Round of 32 → Final, with feeder groups.',
    cities_blurb='Every fixture mapped across the 16 host cities.',
    whatif_placeholder='e.g. What happens to Group C if Argentina lose their last group match?',
    whatif_examples=(
        'What if Brazil lose in the Round of 32?',
        'Who benefits most if the top seed in Group A gets upset?',
        'What does the final look like if both semi-final favourites fall?'),
    elo_hosts=frozenset(ratings.HOSTS),
    og_image='og/preview.png',
)

EDITIONS = {e.key: e for e in (MEN,)}
DEFAULT = MEN


def get(key):
    """Edition by key, defaulting to the men's edition for unknown keys."""
    return EDITIONS.get(key, DEFAULT)
