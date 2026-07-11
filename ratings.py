"""Team strength priors for the prediction engine.

World-Football-Elo-style ratings (~early-2026 snapshot) per edition — the 48
men's finalists plus the likely 2027 Women's World Cup field below — keyed by
the exact `teams.name` used in the DB. These are static priors; the
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

# ── 2027 Women's World Cup priors ────────────────────────────────────────────
# Same scale as ELO above (~2100+ elite), for the women's national teams likely
# to reach Brazil 2027. Qualification runs into early 2027, so this covers the
# plausible field rather than a confirmed 32; anyone who qualifies without a row
# here starts at DEFAULT_ELO. seed_data copies the edition's priors into the DB
# (teams.elo / teams.is_host), which is what the prediction engine actually
# reads — so refining a rating only needs this table + a reseed.
WOMENS_ELO = {
    "Spain": 2190, "USA": 2170, "England": 2130, "Germany": 2110,
    "Japan": 2070, "Sweden": 2050, "France": 2040, "Brazil": 2030,
    "Canada": 2000, "North Korea": 1990, "Netherlands": 1980, "Australia": 1960,
    "Italy": 1940, "Denmark": 1930, "Norway": 1920, "Iceland": 1910,
    "China": 1880, "South Korea": 1860, "Austria": 1850, "Belgium": 1850,
    "Portugal": 1840, "Switzerland": 1830, "Colombia": 1820, "Ireland": 1810,
    "Scotland": 1790, "Finland": 1780, "Poland": 1770, "Czech Republic": 1760,
    "Mexico": 1760, "New Zealand": 1740, "Argentina": 1740, "Nigeria": 1730,
    "Wales": 1720, "Zambia": 1700, "South Africa": 1690, "Morocco": 1680,
    "Haiti": 1650, "Jamaica": 1650, "Paraguay": 1640, "Venezuela": 1630,
    "Vietnam": 1580, "Philippines": 1570, "Costa Rica": 1560, "Panama": 1540,
}
WOMENS_HOSTS = {"Brazil"}


def db_priors(conn):
    """(Elo dict, host set) as seeded into this DB's teams table.

    The DB is the edition-agnostic source of truth: seed_data writes each
    edition's priors into teams.elo / teams.is_host, so the prediction engine
    can serve any tournament from the connection alone. Teams without a
    stored prior (pre-migration rows) fall back to the men's static table —
    the only DB that can predate the columns."""
    elo, hosts = {}, set()
    try:
        rows = conn.execute("SELECT name, elo, is_host FROM teams").fetchall()
    except Exception:
        return dict(ELO), set(HOSTS)
    for r in rows:
        elo[r["name"]] = r["elo"] if r["elo"] is not None else ELO.get(r["name"], DEFAULT_ELO)
        if r["is_host"]:
            hosts.add(r["name"])
    return elo, hosts


def get_rating(team, host_match=False, elo=None, hosts=None):
    """Elo for a team; add the host bonus for hosts (always at home venues).
    `elo`/`hosts` select an edition's priors (default: the men's static tables)."""
    elo = ELO if elo is None else elo
    hosts = HOSTS if hosts is None else hosts
    base = elo.get(team or "", DEFAULT_ELO)
    if team in hosts:
        base += HOST_BONUS
    return base


# ── dynamic (in-tournament) ratings ──────────────────────────────────────────
# Update each team's base rating from its actual results so the projection
# reflects current form, not just the pre-tournament snapshot. Standard
# World-Football-Elo update applied match-by-match in chronological order:
#
#     R' = R + K · G · (W − We)
#
# with W the actual result (1/0.5/0), We the Elo win-expectancy, G a
# goal-difference multiplier, and K the weight (60 = World-Cup tier). The host
# bonus is treated as a positional edge: it enters the expectation We but the
# rating change accrues to the team's intrinsic base.
import config

ELO_K = float(getattr(config, "PREDICT_ELO_K", 60.0))


def _goal_mult(diff):
    """eloratings.net goal-difference multiplier."""
    if diff <= 1:
        return 1.0
    if diff == 2:
        return 1.5
    return (11 + diff) / 8.0


def dynamic_ratings(finished, k=None, elo=None, hosts=None):
    """Replay finished matches (chronological order) and return adjusted base
    ratings {team: rating}. `finished` rows expose team1/team2/score1/score2.
    k=0 leaves the static priors untouched. `elo`/`hosts` select the edition's
    priors (default: the men's static tables)."""
    k = ELO_K if k is None else k
    elo = ELO if elo is None else elo
    hosts = HOSTS if hosts is None else hosts
    rt = dict(elo)
    if not k:
        return rt
    for m in finished:
        ta, tb, sa, sb = m["team1"], m["team2"], m["score1"], m["score2"]
        if ta is None or tb is None or sa is None or sb is None:
            continue
        ra = rt.get(ta, DEFAULT_ELO) + (HOST_BONUS if ta in hosts else 0)
        rb = rt.get(tb, DEFAULT_ELO) + (HOST_BONUS if tb in hosts else 0)
        we_a = 1.0 / (1.0 + 10 ** (-(ra - rb) / 400.0))
        w_a = 1.0 if sa > sb else 0.5 if sa == sb else 0.0
        delta = k * _goal_mult(abs(sa - sb)) * (w_a - we_a)
        rt[ta] = rt.get(ta, DEFAULT_ELO) + delta
        rt[tb] = rt.get(tb, DEFAULT_ELO) - delta
    return rt
