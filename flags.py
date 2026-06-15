"""Team-name -> flag graphic.

Renders an <img> from flagcdn.com (ISO 3166-1 alpha-2 codes, plus GB
subdivisions for England/Scotland). Image flags render identically on every OS,
unlike regional-indicator emoji which fall back to letter codes on Windows. The
emoji table is kept as a secondary fallback / data reference.
"""

from markupsafe import Markup

# team name -> flagcdn code
CODES = {
    "Algeria": "dz", "Argentina": "ar", "Australia": "au", "Austria": "at",
    "Belgium": "be", "Bosnia & Herzegovina": "ba", "Brazil": "br",
    "Canada": "ca", "Cape Verde": "cv", "Colombia": "co", "Croatia": "hr",
    "Curaçao": "cw", "Czech Republic": "cz", "DR Congo": "cd",
    "Ecuador": "ec", "Egypt": "eg", "England": "gb-eng", "France": "fr",
    "Germany": "de", "Ghana": "gh", "Haiti": "ht", "Iran": "ir",
    "Iraq": "iq", "Ivory Coast": "ci", "Japan": "jp", "Jordan": "jo",
    "Mexico": "mx", "Morocco": "ma", "Netherlands": "nl",
    "New Zealand": "nz", "Norway": "no", "Panama": "pa", "Paraguay": "py",
    "Portugal": "pt", "Qatar": "qa", "Saudi Arabia": "sa",
    "Scotland": "gb-sct", "Senegal": "sn", "South Africa": "za",
    "South Korea": "kr", "Spain": "es", "Sweden": "se",
    "Switzerland": "ch", "Tunisia": "tn", "Turkey": "tr", "USA": "us",
    "Uruguay": "uy", "Uzbekistan": "uz",
}

# secondary fallback (data reference; not used for rendering)
FLAGS = {
    "Algeria": "🇩🇿", "Argentina": "🇦🇷", "Australia": "🇦🇺", "Austria": "🇦🇹",
    "Belgium": "🇧🇪", "Bosnia & Herzegovina": "🇧🇦", "Brazil": "🇧🇷",
    "Canada": "🇨🇦", "Cape Verde": "🇨🇻", "Colombia": "🇨🇴", "Croatia": "🇭🇷",
    "Curaçao": "🇨🇼", "Czech Republic": "🇨🇿", "DR Congo": "🇨🇩",
    "Ecuador": "🇪🇨", "Egypt": "🇪🇬", "England": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "France": "🇫🇷",
    "Germany": "🇩🇪", "Ghana": "🇬🇭", "Haiti": "🇭🇹", "Iran": "🇮🇷",
    "Iraq": "🇮🇶", "Ivory Coast": "🇨🇮", "Japan": "🇯🇵", "Jordan": "🇯🇴",
    "Mexico": "🇲🇽", "Morocco": "🇲🇦", "Netherlands": "🇳🇱",
    "New Zealand": "🇳🇿", "Norway": "🇳🇴", "Panama": "🇵🇦", "Paraguay": "🇵🇾",
    "Portugal": "🇵🇹", "Qatar": "🇶🇦", "Saudi Arabia": "🇸🇦",
    "Scotland": "🏴󠁧󠁢󠁳󠁣󠁴󠁿", "Senegal": "🇸🇳", "South Africa": "🇿🇦",
    "South Korea": "🇰🇷", "Spain": "🇪🇸", "Sweden": "🇸🇪",
    "Switzerland": "🇨🇭", "Tunisia": "🇹🇳", "Turkey": "🇹🇷", "USA": "🇺🇸",
    "Uruguay": "🇺🇾", "Uzbekistan": "🇺🇿",
}


def flag_code(team):
    """ISO/subdivision code for a team, or None."""
    return CODES.get(team or "")


def flag(team):
    """Render a team's flag as an <img> (empty string for unknown/slot text)."""
    code = CODES.get(team or "")
    if not code:
        return Markup("")
    return Markup(
        '<img class="flag-img" src="https://flagcdn.com/{c}.svg" '
        'alt="{name}" title="{name}" loading="lazy" width="22" height="16">'
    ).format(c=code, name=team)
