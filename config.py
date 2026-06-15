"""Configuration for the 2026 World Cup dashboard."""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')


def _load_dotenv(path):
    """Minimal .env loader (no external dependency). Reads KEY=VALUE lines;
    blank lines and # comments are skipped. Real env vars take precedence."""
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, val = line.partition('=')
                os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


_load_dotenv(os.path.join(BASE_DIR, '.env'))

# SQLite database (built by seed_data.py, refreshed by update_results.py)
DB_PATH = os.path.join(DATA_DIR, 'worldcup.db')

# Local cached copy of the openfootball dataset (offline fallback for seeding)
OPENFOOTBALL_LOCAL = os.path.join(DATA_DIR, 'openfootball-2026.json')

# Primary live source: openfootball public-domain JSON (no API key required).
OPENFOOTBALL_URL = (
    'https://raw.githubusercontent.com/openfootball/worldcup.json/'
    'master/2026/worldcup.json'
)

# Optional enrichment: football-data.org (free tier). Set FOOTBALL_DATA_API_KEY
# in the environment to enable. The dashboard works fully without it.
FOOTBALL_DATA_API_KEY = os.environ.get('FOOTBALL_DATA_API_KEY', '')
FOOTBALL_DATA_URL = 'https://api.football-data.org/v4/competitions/WC/matches'

# Optional: OpenWeatherMap key (free tier) enables the isobar/pressure + other
# live map overlays on the daily map. Everything else works without it.
OPENWEATHER_API_KEY = os.environ.get('OPENWEATHER_API_KEY', '')

# Predictions: Monte-Carlo sample count (accuracy vs. compute time; cached).
PREDICT_SIMS = int(os.environ.get('PREDICT_SIMS', '4000'))

# Dixon-Coles low-score correction (draw realism). rho<0 lifts the draw rate;
# 0 disables it (plain independent Poisson). See predict.DRAW_RHO.
PREDICT_DRAW_RHO = float(os.environ.get('PREDICT_DRAW_RHO', '-0.12'))

# Optional: Anthropic key enables the MiroFish-inspired "AI pundit panel".
# Statistical predictions work fully without it.
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
PUNDIT_MODEL = os.environ.get('PUNDIT_MODEL', 'claude-haiku-4-5')

# Pundit cost controls (cache hits are free and don't count). A fresh generation
# is blocked once EITHER the daily call cap OR the self-tracked monthly $ budget
# is reached. Tune both in the environment.
PUNDIT_MAX_PER_DAY = int(os.environ.get('PUNDIT_MAX_PER_DAY', '50'))
PUNDIT_MONTHLY_BUDGET = float(os.environ.get('PUNDIT_MONTHLY_BUDGET', '5.0'))  # USD
# Headroom reserve: pundits may use at most (100 - reserve)% of the caps above,
# always leaving this share of the cycle's daily calls and monthly $ untouched
# for other use of the shared key.
PUNDIT_RESERVE_PCT = int(os.environ.get('PUNDIT_RESERVE_PCT', '20'))

# Deployment
PORT = 5010
SUBPATH = '/worldcup'

# Tournament span (for the map day slider)
TOURNAMENT_START = '2026-06-11'
TOURNAMENT_END = '2026-07-19'
