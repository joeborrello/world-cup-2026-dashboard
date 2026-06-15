"""Team strength priors for the prediction engine.

World-Football-Elo-style ratings (~early-2026 snapshot) for the 48 finalists,
keyed by the exact `teams.name` used in the DB. These are static priors; the
Monte-Carlo holds finished results fixed, so the projection self-corrects as the
tournament unfolds even if a prior is a little off. A live refresh (e.g. scraping
eloratings.net) is a possible later enhancement.
"""

# Elo scale: ~2100+ elite, ~1900 strong, ~1750 mid, ~1600 weaker.
ELO = {
    # Group A
    "Mexico": 1850, "South Korea": 1790, "Czech Republic": 1780, "South Africa": 1700,
    # Group B
    "Switzerland": 1870, "Canada": 1760, "Bosnia & Herzegovina": 1740, "Qatar": 1700,
    # Group C
    "Brazil": 2050, "Morocco": 1900, "Scotland": 1780, "Haiti": 1560,
    # Group D
    "USA": 1860, "Turkey": 1800, "Australia": 1740, "Paraguay": 1730,
    # Group E
    "Germany": 1960, "Ecuador": 1820, "Ivory Coast": 1770, "Curaçao": 1570,
    # Group F
    "Netherlands": 2000, "Japan": 1880, "Sweden": 1780, "Tunisia": 1720,
    # Group G
    "Belgium": 1950, "Iran": 1780, "Egypt": 1760, "New Zealand": 1600,
    # Group H
    "Spain": 2080, "Uruguay": 1920, "Saudi Arabia": 1670, "Cape Verde": 1640,
    # Group I
    "France": 2100, "Senegal": 1850, "Norway": 1840, "Iraq": 1640,
    # Group J
    "Argentina": 2140, "Austria": 1800, "Algeria": 1770, "Jordan": 1620,
    # Group K
    "Portugal": 2010, "Colombia": 1900, "DR Congo": 1700, "Uzbekistan": 1680,
    # Group L
    "England": 2010, "Croatia": 1900, "Ghana": 1720, "Panama": 1680,
}

# Co-hosts get a modest home advantage when playing on home soil.
HOSTS = {"USA", "Canada", "Mexico"}
HOST_BONUS = 60          # Elo points, applied to a host nation's rating
DEFAULT_ELO = 1700       # fallback for any name not found


def get_rating(team, host_match=False):
    """Elo for a team; add the host bonus for co-hosts (always at home venues)."""
    base = ELO.get(team or "", DEFAULT_ELO)
    if team in HOSTS:
        base += HOST_BONUS
    return base
