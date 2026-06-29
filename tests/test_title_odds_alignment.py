"""Title-odds alignment between the droplet app and the GitHub Pages snapshot (JOE-12).

The landing site (publish_pages.py) and the dashboard's predictions tab
(app.py /api/predictions) both surface the same Monte-Carlo "title odds". They
used to disagree slightly: the snapshot pinned a fixed RNG seed while the app
ran un-seeded, so the two agreed only to within sampling noise.

The droplet app is the single source of truth: its Monte-Carlo engine
(predict.predictions) holds finished results fixed and adjusts ratings by actual
in-tournament outcomes (ratings.dynamic_ratings), and the GitHub Pages landing
strip pulls those odds from the droplet (/api/landing), so the two surfaces show
the same numbers by construction.

The fix routes both through a single shared default seed (config.PREDICT_SEED,
exposed as predict.SEED), making the odds deterministic and therefore *identical*
in both places for a given result set. Both surfaces also render the odds to one
decimal place of precision. These tests pin that down so it can't silently regress.
"""

import config
import db
import predict
import publish_pages


def _fresh_conn():
    return db.connect()


def _clear_cache():
    """Force the heavy aggregate to recompute on the next call."""
    predict._cache["key"] = None
    predict._cache["agg"] = None


# ── wiring ──────────────────────────────────────────────────────────────────

def test_predict_seed_comes_from_config():
    """The engine default seed is the single shared config value."""
    assert predict.SEED == config.PREDICT_SEED


def test_default_seed_is_set():
    """A real seed must be configured by default, or the odds drift on noise."""
    assert config.PREDICT_SEED is not None


# ── determinism ─────────────────────────────────────────────────────────────

def test_unseeded_predictions_are_deterministic():
    """Two independent (cache-cleared) un-seeded runs give identical odds,
    because the shared default seed pins the Monte-Carlo sampling."""
    conn = _fresh_conn()
    try:
        _clear_cache()
        a = predict.predictions(conn)["teams"]
        _clear_cache()
        b = predict.predictions(conn)["teams"]
    finally:
        conn.close()
    champ_a = {t: v["champion"] for t, v in a.items()}
    champ_b = {t: v["champion"] for t, v in b.items()}
    assert champ_a == champ_b


# ── the actual bug: app vs published snapshot ───────────────────────────────

def test_app_and_snapshot_title_odds_match():
    """The champion odds the dashboard serves (predict.predictions, the same call
    /api/predictions makes) must exactly equal those published to the landing
    snapshot (publish_pages._title_odds) — not merely be close."""
    conn = _fresh_conn()
    try:
        _clear_cache()
        snapshot = publish_pages._title_odds(conn, n=5)
        app_teams = predict.predictions(conn)["teams"]
    finally:
        conn.close()

    assert snapshot, "snapshot should list title odds"
    for entry in snapshot:
        team, published = entry["team"], entry["champion"]
        # the snapshot rounds to 4dp; the app exposes the raw probability
        assert team in app_teams
        assert round(app_teams[team]["champion"], 4) == published


def _one_decimal_pct(champion):
    """How both web pages render a champion probability: a percentage to one
    decimal place (predictions.js `(x*100).toFixed(1)`, landing `(o.champion*100)
    .toFixed(1)`). Mirrored here so the data-precision contract is testable."""
    return f"{champion * 100:.1f}%"


def test_one_decimal_display_matches_between_surfaces():
    """Both pages show title odds to one decimal place. The landing snapshot
    rounds the probability to 4dp while the app exposes the raw float; verify
    that rounding never changes the one-decimal-percent string, so the two
    surfaces display *identical* odds (not merely close)."""
    conn = _fresh_conn()
    try:
        _clear_cache()
        snapshot = publish_pages._title_odds(conn, n=5)
        app_teams = predict.predictions(conn)["teams"]
    finally:
        conn.close()

    assert snapshot, "snapshot should list title odds"
    for entry in snapshot:
        team = entry["team"]
        # snapshot value (4dp) vs the app's raw value, both as shown to the user
        assert _one_decimal_pct(entry["champion"]) == _one_decimal_pct(
            app_teams[team]["champion"]), team


def test_snapshot_is_top_n_by_champion():
    """Sanity: the snapshot really is the strongest teams, in order, so an
    equality test above isn't comparing against an empty/garbage list."""
    conn = _fresh_conn()
    try:
        _clear_cache()
        snapshot = publish_pages._title_odds(conn, n=5)
        app_teams = predict.predictions(conn)["teams"]
    finally:
        conn.close()

    expected = sorted(app_teams.items(), key=lambda kv: -kv[1]["champion"])[:5]
    assert [t for t, _ in expected] == [e["team"] for e in snapshot]
