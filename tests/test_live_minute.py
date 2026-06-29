"""Tests for the live ticker's minute-of-play snapshot (JOE-17).

The issue: show the *minute of play at the time of the most recent check*, and
tighten the score-refresh cadence to every 5 minutes. These tests pin:

  * the minute resolver (`live.live_minute`) — feed-supplied minute preferred,
    kickoff-elapsed estimate otherwise, "HT" while PAUSED;
  * `live.live_matches` carrying a `minute` per in-play match and recording the
    check timestamp (`live.last_checked`);
  * the 5-minute updater cron and the minute wiring in the ticker JS.
"""

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest import mock

import config
import db
import live

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)

KICKOFF = datetime(2026, 6, 18, 16, 0, tzinfo=timezone.utc)


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


# ── live_minute resolver ──────────────────────────────────────────────────────

def test_paused_is_half_time():
    assert live.live_minute({}, KICKOFF, KICKOFF + timedelta(minutes=50), "paused") == "HT"


def test_feed_minute_is_preferred():
    """When football-data carries a `minute`, trust it over any estimate."""
    fx = {"minute": 63}
    # even with an elapsed-time estimate that would say otherwise
    assert live.live_minute(fx, KICKOFF, KICKOFF + timedelta(minutes=10), "in_play") == "63'"


def test_estimate_first_half():
    got = live.live_minute({}, KICKOFF, KICKOFF + timedelta(minutes=20), "in_play")
    assert got == "21'"


def test_estimate_first_half_stoppage():
    got = live.live_minute({}, KICKOFF, KICKOFF + timedelta(minutes=47), "in_play")
    assert got == "45+'"


def test_estimate_second_half_subtracts_the_break():
    # 70 real minutes in: 15-min break removed -> 55th minute.
    got = live.live_minute({}, KICKOFF, KICKOFF + timedelta(minutes=70), "in_play")
    assert got == "55'"


def test_estimate_caps_at_ninety_plus():
    got = live.live_minute({}, KICKOFF, KICKOFF + timedelta(minutes=120), "in_play")
    assert got == "90+'"


def test_no_kickoff_or_not_started_yields_none():
    assert live.live_minute({}, None, KICKOFF, "in_play") is None
    # clock hasn't started (now is before kickoff)
    assert live.live_minute({}, KICKOFF, KICKOFF - timedelta(minutes=5), "in_play") is None


# ── live_matches integration ──────────────────────────────────────────────────

def _conn_with_match():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_schema(conn)
    conn.execute(
        "INSERT INTO matches (num, stage, round_label, group_letter, utc_datetime, "
        "team1, team2, score1, score2, status) VALUES "
        "(3, 'group', 'Group F', 'F', ?, 'Czech Republic', 'South Africa', "
        "NULL, NULL, 'scheduled')",
        (KICKOFF.isoformat(),),
    )
    conn.commit()
    return conn


def _fixture(status, home=0, away=1, minute=None):
    fx = {
        "status": status,
        "utcDate": "2026-06-18T16:00:00Z",
        "homeTeam": {"name": "Czech Republic"},
        "awayTeam": {"name": "South Africa"},
        "score": {"fullTime": {"home": home, "away": away}},
    }
    if minute is not None:
        fx["minute"] = minute
    return fx


def _run(conn, fixtures):
    live._CACHE.update(data=None, ts=0.0, checked_at=None)  # bypass the TTL cache
    with mock.patch.object(config, "FOOTBALL_DATA_API_KEY", "test-key"), \
            mock.patch.object(live.requests, "get") as g:
        g.return_value.json.return_value = {"matches": fixtures}
        g.return_value.raise_for_status.return_value = None
        return live.live_matches(conn)


def test_live_match_carries_minute_and_records_check_time():
    conn = _conn_with_match()
    out = _run(conn, [_fixture("IN_PLAY", minute=37)])
    assert len(out) == 1
    row = out[0]
    assert row["num"] == 3
    assert (row["score1"], row["score2"]) == (0, 1)
    assert row["minute"] == "37'"            # feed minute surfaced
    assert row["state"] == "in_play"
    # the check timestamp travels with the snapshot
    assert live.last_checked() is not None
    datetime.fromisoformat(live.last_checked())  # parseable ISO


def test_paused_live_match_reports_half_time():
    conn = _conn_with_match()
    out = _run(conn, [_fixture("PAUSED")])
    assert out[0]["minute"] == "HT"
    assert out[0]["state"] == "paused"


def test_finished_match_is_not_in_the_ticker():
    conn = _conn_with_match()
    assert _run(conn, [_fixture("FINISHED")]) == []


# ── deployment + frontend wiring ──────────────────────────────────────────────

def test_updater_cron_runs_every_five_minutes():
    eco = _read("ecosystem.config.js")
    assert "*/5 * * * *" in eco
    assert "*/7 * * * *" not in eco


def test_ticker_js_renders_minute_and_uses_check_time():
    js = _read("static", "js", "live.js")
    assert "minuteLabel" in js
    assert "checked_at" in js          # reads the check timestamp from the API
    assert "m.minute" in js            # renders the per-match minute


def test_api_live_returns_check_timestamp():
    import app as flask_app
    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as c:
        payload = c.get("/api/live").get_json()
    assert "matches" in payload
    assert "checked_at" in payload
