# World Cup 2026 Dashboard ⚽

A single-page-per-view dashboard for following the **2026 FIFA World Cup** (Canada · USA · Mexico — 48 teams, 12 groups, 104 matches). It pairs a live-updating bracket and group tables with interactive maps, match-day weather, a live score ticker, and an Elo-based prediction engine with an optional AI "pundit panel."

**Live:** https://droplet.josephborrello.com/worldcup/

Built with Flask + Jinja2 + vanilla JS (Leaflet for maps), served by gunicorn under pm2 behind an nginx subpath, backed by SQLite. No heavy front-end framework.

---

## Features at a glance

- **Bracket** — Round of 32 → Final in FIFA's two-pathway layout, with the feeder group tables rendered alongside each region. Slots resolve from placeholders (`Winner A`, `3rd A/B/C/D`) to real teams as results land.
- **Group tables** — all 12 groups with live points/GF/GD and FIFA tiebreakers, highlighting the top-2 and in-contention third places.
- **Three maps** (Leaflet + OpenStreetMap):
  - **Daily map** — a day slider (Jun 11 → Jul 19) lighting up that day's venues, with **country-flag pins** and click-through match detail.
  - **Schedule map** — the full fixture list plotted across the 16 host cities.
  - **Follow-a-team map** — pick one or more teams and trace their journey, with date-gradient pins, **gradient path lines**, and directional arrows.
- **Match-day weather** — live radar, isobars, and weather advisories for today's games; temperature + precipitation forecasts for upcoming dates; historical conditions for matches already played. F/C toggle, a national temperature heat map, and per-venue humidity/dewpoint.
- **Live score ticker** — when a match is in progress, the live score appears site-wide.
- **Predictions** — an Elo Monte-Carlo engine projects title odds, group-qualification odds, and a *likely* bracket, with an Actual/Projected toggle and a depth selector (R32 → Final).
- **AI pundit panel** *(experimental, optional)* — a [MiroFish](https://github.com/666ghj/MiroFish)-inspired panel of opinionated Claude personas debates the race, grounded in the model's numbers, with self-tracked cost controls.

---

## How it was built — feature stages

The dashboard grew in rounds. Each stage below was a self-contained feature addition on top of the live deployment.

### Stage 0 — Foundation
Greenfield Flask app following the server's convention (gunicorn + pm2, nginx subpath `/worldcup/`, port 5010, SQLite). Schedule, teams, groups, and venues seeded from the public-domain [openfootball](https://github.com/openfootball/worldcup.json) dataset plus a hand-curated 16-venue coordinate table; the fixed knockout pairing map seeded from FIFA's published R32 bracket. A 15-minute pm2 cron (`update_results.py`) pulls scores, recomputes standings with FIFA tiebreakers, and propagates resolved teams into knockout slots — with a graceful static fallback when no live source is available. Initial pages: bracket, group tables, daily map.

### Stage 1 — Live data
Wired in a free **football-data.org** API key to enrich the 15-minute updater with live scores, while keeping the no-key fallback intact.

### Stage 2 — Flag pins on the daily map
Daily-map pins now display the **flags of the two competing nations** (known for group-stage fixtures), with match time, stadium, and stage still available on click.

### Stage 3 — Follow-a-team map
A new, separate map: select one or more teams from a checklist and plot their matches, **color-coded by date** so you can read a team's path through the tournament at a glance.

### Stage 4 — Team-map polish
Refinements to the follow-a-team view: **path lines that follow the same date gradient**, **directional arrows** along each leg, and **whole-pin coloring** by date (replacing the small dot).

### Stage 5 — Weather
Match-day weather on the daily map: **live radar** (RainViewer), **isobars** (OpenWeatherMap pressure tiles), and **weather advisories** across all three host nations (US via NWS, Canada via MSC GeoMet). Keyless **Open-Meteo** supplies temperature and precipitation **forecasts** for future dates and **historical** conditions for matches already played.

### Stage 6 — Temperature controls
A **°F / °C toggle**, a **nationwide temperature heat map** overlay, a **numeric range legend** on the color bar, and per-venue **humidity + dewpoint**. (Includes a fix for West-Coast late kickoffs that fall into the next UTC day.)

### Stage 7 — Live score ticker
When one of today's matches is being played, the **live score appears on every page** via a shared ticker (estimated minute omitted — only authoritative state is shown).

### Stage 8 — Prediction engine
A pure-stdlib **Elo Monte-Carlo** simulator (`ratings.py` + `predict.py`) plays out the rest of the tournament thousands of times, holding finished results fixed: a Poisson goals model derives expected scorelines from the Elo gap, standings are rebuilt with the *same* FIFA tiebreaker code as the live path, third-place slots are allocated, and each knockout round is simulated to the Final. Surfaced as a **Predictions** page (title + qualification odds) and an **Actual / Projected** toggle plus **depth selector** on the bracket. Predictions are computed read-only — hypothetical teams are never written to the live `matches` table.

### Stage 9 — AI pundit panel
A [MiroFish](https://github.com/666ghj/MiroFish)-inspired layer: four opinionated AI personas (The Analyst, The Romantic, The Tactician, The Veteran) debate a group or the title race via the **Anthropic Claude API** (default model `claude-opus-4-8`), **grounded in the statistical model's odds** rather than replacing them. Generation is lazy (on request) and persisted, so results are re-paid only when the picture changes. Cost is managed with a self-tracked ledger: a **daily call cap**, a **monthly $ budget**, and a configurable **reserve** (default 20%) that always leaves headroom on the shared key. Degrades gracefully to stats-only when no key is set.

---

## Architecture

```
worldcup-2026/
  app.py              # Flask routes + SubpathMiddleware (nginx subpath)
  config.py           # ports, subpath, keys from .env (minimal loader)
  db.py / seed_data.py# SQLite schema + one-time seed from openfootball + venues
  update_results.py   # 15-min cron: scores -> standings -> bracket
  compute.py          # group standings, FIFA tiebreakers, bracket resolution
  ratings.py          # static Elo ratings for the 48 teams
  predict.py          # Elo Monte-Carlo simulation (pure stdlib)
  pundits.py          # MiroFish-inspired Claude pundit panel + cost controls
  weather.py / live.py / alerts.py
  venues.py / flags.py / data_source.py
  templates/          # base, bracket, groups, index, *map, predictions
  static/js|css/      # Leaflet maps, ticker, predictions UI, styles
  data/worldcup.db    # SQLite (generated; not committed)
  ecosystem.config.js # pm2 -> gunicorn + the updater cron
```

## Running locally

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python seed_data.py            # build data/worldcup.db
python update_results.py       # optional: pull current scores
python app.py                  # dev server
```

### Configuration (`.env`)

All keys are **optional** — the dashboard renders fully from seeded data without any of them.

| Variable | Enables |
| --- | --- |
| `FOOTBALL_DATA_API_KEY` | Live score enrichment (football-data.org) |
| `OPENWEATHER_API_KEY` | Isobar / pressure map overlays |
| `ANTHROPIC_API_KEY` | AI pundit panel |
| `PUNDIT_MODEL` | Pundit model (default `claude-opus-4-8`) |
| `PUNDIT_MAX_PER_DAY` / `PUNDIT_MONTHLY_BUDGET` / `PUNDIT_RESERVE_PCT` | Pundit cost caps + reserve |
| `PREDICT_SIMS` | Monte-Carlo sample count (default 4000) |

## Data & accuracy notes

- Live scores via football-data.org (free tier; slightly delayed) with a keyless openfootball fallback.
- Weather from Open-Meteo (keyless), RainViewer radar, NWS + MSC advisories, OpenWeatherMap map tiles.
- Elo ratings are an early-2026 snapshot; the simulation self-corrects as real results are fixed in place.
- Third-place slot allocation uses a valid (not FIFA's official fixed-table) matching.
- AI pundit takes are LLM-generated commentary grounded in the model — entertainment, not predictions of record.

## Credits

Schedule/structure data from [openfootball](https://github.com/openfootball/worldcup.json) (public domain). Maps © OpenStreetMap contributors. Flags via [flagcdn](https://flagcdn.com). Pundit concept inspired by [MiroFish](https://github.com/666ghj/MiroFish).
