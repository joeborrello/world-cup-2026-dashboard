# worldcup-2026 — Agent Context

> You are the dedicated agent for this app. Read SESSIONS.md in this directory before
> starting work; append a dated entry there after any meaningful change.

## What this app is
World Cup 2026 tracker + statistical prediction engine (Flask + Jinja2). Predictions come
from a dynamic-Elo + bivariate-Poisson model (tunable via env: PREDICT_ELO_K,
PREDICT_DRAW_RHO — see config.py comments). On top sits the MiroFish-inspired **AI pundit
panel**: four personas (Analyst, Romantic, Tactician, Veteran) debate a group or the title
race via Claude, grounded in the model's numbers.

## Live deployment
- Public: https://droplet.josephborrello.com/worldcup/
- Port 5010 (localhost) · pm2 name `worldcup-2026` · restart: `pm2 restart worldcup-2026`
- Companion pm2 process `worldcup-2026-updater` (data updater; often stopped between runs)
- Python: `venv/` · secrets in `.env` (mode 600): FOOTBALL_DATA_API_KEY, PUNDIT_* vars

## Architecture
- `app.py` — Flask routes (incl. `/api/pundits/*`, `/predictions`)
- `predict.py` / `ratings.py` / `compute.py` — the statistical model (Elo + Poisson)
- `pundits.py` — AI pundit panel: **Claude via the `claude` CLI on Joe's Max (5x)
  subscription** (`_claude_cli`: single-shot `-p`, all tools disallowed, ANTHROPIC_API_KEY
  stripped from subprocess env). Model from PUNDIT_MODEL (currently `claude-fable-5`).
  Lazy generation, cached in `pundit_cache` keyed by (scope, state_hash) — re-generates
  only when results change; cache hits are free.
- `config.py` — all knobs from env with commented defaults

## Data
- SQLite (matches, standings, pundit_cache, pundit_calls) — see db module for path
- Pundit cost controls: daily call cap + self-tracked monthly $ budget with a reserve pct
  (PUNDIT_MAX_PER_DAY=50, PUNDIT_MONTHLY_BUDGET=5.0, 20% reserve → effective 40/day, $4/mo).
  Since the CLI switch these $ figures are informational (API-equivalent), kept as guardrails.

## Conventions & gotchas
- **Billing rule (Joe's standing order): all Claude calls bill the Max subscription via
  the CLI — never the API.** The `.env` API key is commented out on purpose; `_claude_cli`
  strips the env key as a safeguard. Don't reintroduce SDK calls.
- Pundit output must be strict JSON (system prompt enforces); parser tolerates fenced
  blocks and drops empty/truncated pundit entries.
- `_log_call` prices token usage from the PRICING table by PUNDIT_MODEL — keep that table
  current when changing models.
- Panel availability gates on `shutil.which("claude")` (pundits.available()), also used by
  `/api/pundits/budget` → `enabled`.
- The daily-map **weather layer needs OPENWEATHER_API_KEY** in `.env` (free OWM key), and
  the **updater needs FOOTBALL_DATA_API_KEY** — the updater once sat dead purely for lack
  of that key (2026-06 health-digest incident).
- "Today" uses a **2am US-Eastern cutoff**, not midnight, so midnight-Eastern matches
  still show as today. Intentional (Austria–Jordan incident) — don't "fix" it.

## Journal protocol
After meaningful work append to SESSIONS.md: date, what changed (files), why, follow-ups.
