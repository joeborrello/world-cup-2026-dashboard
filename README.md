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
  - **Follow-a-team map** — pick one or more teams and trace their journey, with date-gradient pins, **gradient path lines**, directional arrows, and **per-match kickoff weather** (actual conditions for matches played, forecast for those upcoming).
- **Match-day weather** — live radar, isobars, and weather advisories for today's games; temperature + precipitation forecasts for upcoming dates; historical conditions for matches already played, shown at each match's kickoff hour on **both the daily and follow-a-team maps**. F/C toggle, a national temperature heat map, and per-venue humidity/dewpoint.
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
Match-day weather on the daily map: **live radar** (RainViewer), **isobars** (OpenWeatherMap pressure tiles), and **weather advisories** across all three host nations (US via NWS, Canada via MSC GeoMet). Keyless **Open-Meteo** supplies temperature and precipitation **forecasts** for future dates and **historical** conditions for matches already played. The same kickoff-hour weather (shared `WCWx` formatter, `/api/weather?nums=`) is surfaced on the **follow-a-team map**, so each match on a team's run shows its actual or forecast conditions.

### Stage 6 — Temperature controls
A **°F / °C toggle**, a **nationwide temperature heat map** overlay, a **numeric range legend** on the color bar, and per-venue **humidity + dewpoint**. (Includes a fix for West-Coast late kickoffs that fall into the next UTC day.)

### Stage 7 — Live score ticker
When one of today's matches is being played, the **live score appears on every page** via a shared ticker, alongside the **minute of play as of the most recent check** (JOE-17) — football-data's own minute when the feed carries one, otherwise estimated from kickoff. It's a snapshot (labelled with the check time), not a browser-side ticking clock; half-time shows as "HT".

### Stage 8 — Prediction engine
A pure-stdlib **Elo Monte-Carlo** simulator (`ratings.py` + `predict.py`) plays out the rest of the tournament thousands of times, holding finished results fixed: a Poisson goals model derives expected scorelines from the Elo gap, standings are rebuilt with the *same* FIFA tiebreaker code as the live path, third-place slots are allocated, and each knockout round is simulated to the Final. Surfaced as a **Predictions** page (title + qualification odds) and an **Actual / Projected** toggle plus **depth selector** on the bracket. Predictions are computed read-only — hypothetical teams are never written to the live `matches` table.

### Stage 9 — AI pundit panel
A [MiroFish](https://github.com/666ghj/MiroFish)-inspired layer: four opinionated AI personas (The Analyst, The Romantic, The Tactician, The Veteran) debate a group or the title race via the **Anthropic Claude API** (default model `claude-opus-4-8`), **grounded in the statistical model's odds** rather than replacing them. Generation is lazy (on request) and persisted, so results are re-paid only when the picture changes. Cost is managed with a self-tracked ledger: a **daily call cap**, a **monthly $ budget**, and a configurable **reserve** (default 20%) that always leaves headroom on the shared key. Degrades gracefully to stats-only when no key is set.

### Stage 10 — Golden Boot tracker
A **Golden Boot** page (`goldenboot.py`) tracks the race for the tournament's top scorer, built from the same keyless openfootball goals feed (`goals1`/`goals2` per match, persisted to a `scorers` table by the seeder and the 15-minute updater). Real Golden Boot rules apply: **penalties count, own goals don't**, and everyone within two goals of the lead is flagged **in contention**. Each contender also gets a **projection of additional goals**: their goals-per-match rate — regressed toward a modest prior so a hot opener doesn't extrapolate to a record-shattering tally — carried over the number of matches their team is *expected* to still play, derived from the Stage 8 Monte-Carlo round-reach odds. So a striker on a team projected deep into the knockouts is credited more upside than one likely to bow out early. A client-side toggle re-ranks the board by current goals or projected finish (works without JS).

---

## Prediction methodology

The bracket's **Projected** mode and the **Predictions** page are driven by a Monte-Carlo simulation that plays the rest of the tournament many thousands of times and tallies how often each outcome occurs. It's deliberately simple and transparent so the assumptions are easy to inspect — and to critique. Finished results are held fixed; only unplayed matches are simulated, so the numbers sharpen as the tournament unfolds. The implementation is pure-Python with no numerical dependencies: `predict.py` (simulation), `ratings.py` (priors), `compute.py` (standings + tiebreakers). *(This section mirrors the in-app "How the projections work" modal — keep the two in sync.)*

### 1. Team strength (dynamic Elo)

Each team **starts** from a prior rating on the [World-Football-Elo](https://en.wikipedia.org/wiki/World_Football_Elo_Ratings) scale (≈2100+ elite, ≈1900 strong, ≈1750 mid, ≈1600 weaker) — a hand-set, roughly early-2026 snapshot in `ratings.py`. Co-hosts (USA, Canada, Mexico) receive a flat **+60** home-advantage bonus; an unknown name falls back to 1700.

Those priors then **update from in-tournament results**. Finished matches are replayed in chronological order and each team's rating moves by the standard World-Football-Elo update, so an over- or under-performing side carries that form into the simulation of its remaining matches:

```
R' = R + K · G · (W − We)
```

`W` is the actual result (1 / 0.5 / 0), `We` the Elo win-expectancy, `G` a goal-margin multiplier (`1`, `1.5` for a 2-goal win, then `(11+d)/8` for margin `d ≥ 3`), and `K = 60` — the World-Cup tier, deliberately responsive. Early on ratings sit at the priors and diverge as results land; the biggest movers are shown on the Predictions page's **Form tracker**. Tune with `PREDICT_ELO_K` (0 freezes the static priors). The host bonus is treated as a positional edge — it enters `We` but the rating change accrues to the team's intrinsic rating.

### 2. Single-match model

**Group-stage goals.** A rating gap becomes an expected-goals *supremacy* `s`; each side's goals are then drawn from independent Poisson distributions:

```
s   = clamp( (R_A − R_B) / 200, −2.5, +2.5 )
λ_A = max( 0.18, (μ + s) / 2 )      λ_B = max( 0.18, (μ − s) / 2 )      μ = 2.7
G_A ~ Poisson(λ_A)                  G_B ~ Poisson(λ_B)
```

So ~200 Elo points ≈ one goal of expected supremacy, and evenly-matched teams average 1.35 goals each (2.7 combined, near the historical World-Cup norm). Modeling full scorelines — not just W/D/L — is what feeds the goal-difference and goals-scored tiebreakers.

**Draw correction (Dixon–Coles).** Plain independent Poisson under-produces draws, so the four lowest scorelines are reweighted by the Dixon–Coles factor `τ` (with `ρ = −0.12`, set via `PREDICT_DRAW_RHO`), which nudges 0–0 and 1–1 up and 1–0/0–1 down:

```
τ(0,0) = 1 − λ_A·λ_B·ρ        τ(1,1) = 1 − ρ
τ(1,0) = 1 + λ_B·ρ            τ(0,1) = 1 + λ_A·ρ        (τ = 1 otherwise)
P(x,y) ∝ τ(x,y) · Poisson(x; λ_A) · Poisson(y; λ_B)
```

This lifts the average group-stage draw rate from ~22% to a more historical ~24% with negligible effect on title odds — its job is draw realism, not reshuffling the favorites. `ρ` is calibrated to the long-run ~24% norm rather than any single tournament's rate (real WC group stages swing ~18–30% on small samples, so a drawy run is treated as variance, not chased). Set `PREDICT_DRAW_RHO=0` to recover plain independent Poisson.

**Knockout matches.** The same goal model decides the match; a level result goes to a single Elo-weighted coin flip standing in for a penalty shootout, using the standard Elo win-expectancy:

```
E_A = 1 / ( 1 + 10^( −(R_A − R_B) / 400 ) )
```

### 3. Simulating the whole tournament

Each of **N** iterations (default **4,000**, via `PREDICT_SIMS`) plays out end-to-end:

1. Simulate every unplayed **group** match; keep finished ones as-is.
2. Build the 12 group tables with FIFA's tiebreakers in order: **points → goal difference → goals scored → head-to-head** (points/GD/GF among the tied teams) → team name as a stable final fallback (in lieu of fair-play points / drawing of lots). This ordering is shared with the live standings path (`compute.order_group`) so both apply identical rules.
3. Rank the twelve 3rd-placed teams and take the **best 8**; assign them to the Round-of-32 `3X/Y` slots with a backtracking matcher that respects each slot's eligible groups.
4. Resolve the Round of 32 and play each knockout round through to the Final, carrying winners forward (and the two semi-final losers into the third-place match).

Across all iterations the model tallies how often each team finishes 1st/2nd/3rd in its group, reaches each round, and wins the title; dividing by N gives the probabilities. Results are cached and recomputed only when a new match result lands.

### 4. From frequencies to the projected bracket

The **odds tables** are these tallies directly (champion % = share of sims a team won the Final; advance % = share it reached the Round of 32, etc.).

The **projected bracket** needs more care. Choosing each slot's single most-likely team *independently* does **not** produce a valid bracket — a dominant team can become the favorite in two mutually-exclusive places (e.g. both the Final and the third-place match, or two different Round-of-32 slots). The marginal mode of each slot is not a joint sample. Instead the engine assembles **one internally consistent bracket**: it derives a coherent Round-of-32 field from the simulated standings (each group's expected finishing order, plus the best-8 thirds matched to their slots), then advances the **favored team of each projected matchup** forward, so every later slot is fed by a real earlier result. The confidence shown on each slot remains the honest marginal — the fraction of simulations in which that exact team reached that exact slot.

### 5. Uncertainty, assumptions & how to critique it

This is a teaching-grade model, not a betting market. Known limitations, roughly in order of impact:

- **Sampling noise.** A probability `p` from N sims has standard error ≈ `√(p(1−p)/N)` — about ±0.8 percentage points at p=0.5, N=4,000 — shrinking only as `1/√N`. Small gaps between teams may be noise; raise `PREDICT_SIMS` to tighten.
- **Ratings: subjective start, responsive update.** The pre-tournament snapshot is hand-set — challenge it freely. Ratings now adapt to results via a World-Cup-tier Elo update (`K=60`), which is deliberately responsive: over a handful of games it can overreact to a fluke result as readily as it captures real form. Lower `PREDICT_ELO_K` to damp it, or set 0 to freeze the priors.
- **Simplified goal model.** Goals use Poisson with a Dixon–Coles low-score correction (`ρ = −0.12`) — better than independent Poisson on draws, but the correction is modest and still doesn't capture full game-state dynamics (red cards, game-management when ahead, late pushes). The total-goals baseline (2.7) and the linear, clamped Elo→supremacy mapping are convenient choices, not estimated fits.
- **Thin knockout model.** A level match is decided by a single Elo coin — no separate extra-time phase, no penalty-specific skill.
- **Coarse home advantage.** A flat +60 for all three hosts; no travel, altitude, climate, rest-day, injury, or squad-news effects.
- **Third-place allocation is a *valid* matching, not FIFA's official fixed table.** The set of qualifying thirds is correct, but which slot each lands in may differ from FIFA's published lookup.
- **The projected bracket is the favored-path ("chalk") reconciliation.** Because the favorite advances each projected matchup, the single drawn bracket understates upsets — read the per-slot confidence and the odds tables for the true spread.
- **Not yet back-tested.** The model hasn't been calibrated against historical tournaments, so treat absolute numbers as indicative rather than validated.

Runs are deterministic given a fixed random seed, so any result above can be reproduced exactly for review.

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

### Deploying to the droplet

The live app at `/var/www/worldcup-2026` tracks `main` under pm2 (`ecosystem.config.js`). A deploy is just:

```bash
cd /var/www/worldcup-2026
git pull --ff-only origin main
./venv/bin/pip install -r requirements.txt   # only if deps changed
pm2 restart worldcup-2026
```

No manual migration step is needed: the schema (`db.init_schema`, all `CREATE TABLE IF NOT EXISTS`) is re-applied on app startup **and** at the top of every `update_results.py` cron run, so a new table — e.g. `scorers` for the Golden Boot tracker — is created on the existing production DB and back-filled by the next updater pass. Pull, restart, done.

**This deploy is now automatic.** A short pm2 cron (`worldcup-2026-deploy`, running `deploy.py` every 5 minutes) fast-forwards the live checkout to `origin/main` and restarts the web app whenever something new lands — so merging to `main` *is* deploying, within a few minutes, with no SSH step to forget. It is a no-op when already current and **only ever fast-forwards** (a dirty or diverged tree blocks the deploy loudly rather than discarding work). This safety net was added after the Golden Boot tracker reached `main` twice without reaching the live app. Register it once with `pm2 start ecosystem.config.js && pm2 save`; run `python deploy.py` by hand any time to deploy immediately.

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
| `PREDICT_DRAW_RHO` | Dixon–Coles draw correction (default −0.12; 0 disables) |
| `PREDICT_ELO_K` | Dynamic-Elo update weight (default 60; 0 freezes priors) |

## Data & accuracy notes

- Live scores via football-data.org (free tier; slightly delayed) with a keyless openfootball fallback.
- Weather from Open-Meteo (keyless), RainViewer radar, NWS + MSC advisories, OpenWeatherMap map tiles.
- Elo ratings are an early-2026 snapshot; the simulation self-corrects as real results are fixed in place.
- Third-place slot allocation uses a valid (not FIFA's official fixed-table) matching.
- AI pundit takes are LLM-generated commentary grounded in the model — entertainment, not predictions of record.

## Credits

Schedule/structure data from [openfootball](https://github.com/openfootball/worldcup.json) (public domain). Maps © OpenStreetMap contributors. Flags via [flagcdn](https://flagcdn.com). Pundit concept inspired by [MiroFish](https://github.com/666ghj/MiroFish).
