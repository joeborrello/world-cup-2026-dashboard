"""Tests for the kicked-off presumption in the live ticker (JOE-35).

The bug: football-data can leave a fixture's status at TIMED well after the real
kickoff (Spain–Austria read TIMED 18+ minutes into the match), so the site showed
no sign the game had started. Now a fixture whose scheduled kickoff has passed is
presumed live until the feed catches up: state ``in_play``, 0–0 placeholder score,
kickoff-estimated minute, and a ``presumed`` flag so the frontend can label the
score unconfirmed. The presumption lapses after a regulation match's real length.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest import mock

import config
import db
import live

KICKOFF = datetime(2026, 6, 18, 16, 0, tzinfo=timezone.utc)


def _conn_with_match():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_schema(conn)
    conn.execute(
        "INSERT INTO matches (num, stage, round_label, group_letter, utc_datetime, "
        "team1, team2, score1, score2, status) VALUES "
        "(7, 'group', 'Group E', 'E', ?, 'Spain', 'Austria', NULL, NULL, 'scheduled')",
        (KICKOFF.isoformat(),),
    )
    conn.commit()
    return conn


def _fixture(status, home=None, away=None):
    return {
        "status": status,
        "utcDate": "2026-06-18T16:00:00Z",
        "homeTeam": {"name": "Spain"},
        "awayTeam": {"name": "Austria"},
        "score": {"fullTime": {"home": home, "away": away}},
    }


def _run(conn, fixtures, now):
    live._CACHE.clear()  # bypass the TTL cache (now keyed per edition)
    with mock.patch.object(config, "FOOTBALL_DATA_API_KEY", "test-key"), \
            mock.patch.object(live, "_utcnow", return_value=now), \
            mock.patch.object(live.requests, "get") as g:
        g.return_value.json.return_value = {"matches": fixtures}
        g.return_value.raise_for_status.return_value = None
        return live.live_matches(conn)


# ── presumed_live window ──────────────────────────────────────────────────────

def test_window_opens_at_kickoff():
    assert not live.presumed_live(KICKOFF, KICKOFF - timedelta(minutes=1))
    assert live.presumed_live(KICKOFF, KICKOFF)
    assert live.presumed_live(KICKOFF, KICKOFF + timedelta(minutes=18))


def test_window_closes_after_a_regulation_match():
    limit = timedelta(minutes=live._PRESUMED_MAX_MINUTES)
    assert live.presumed_live(KICKOFF, KICKOFF + limit)
    assert not live.presumed_live(KICKOFF, KICKOFF + limit + timedelta(minutes=1))


def test_missing_kickoff_is_never_presumed():
    assert not live.presumed_live(None, KICKOFF)


# ── live_matches integration ──────────────────────────────────────────────────

def test_timed_fixture_past_kickoff_is_presumed_live():
    """18 minutes in, feed still TIMED (the JOE-35 report): show the match live."""
    conn = _conn_with_match()
    out = _run(conn, [_fixture("TIMED")], now=KICKOFF + timedelta(minutes=18))
    assert len(out) == 1
    row = out[0]
    assert row["state"] == "in_play"
    assert row["presumed"] is True
    assert (row["score1"], row["score2"]) == (0, 0)   # placeholder until confirmed
    assert row["minute"] == "19'"                      # estimated from kickoff


def test_timed_fixture_before_kickoff_stays_hidden():
    conn = _conn_with_match()
    assert _run(conn, [_fixture("TIMED")], now=KICKOFF - timedelta(minutes=5)) == []


def test_presumption_lapses_when_the_match_must_be_over():
    conn = _conn_with_match()
    out = _run(conn, [_fixture("TIMED")],
               now=KICKOFF + timedelta(minutes=live._PRESUMED_MAX_MINUTES + 1))
    assert out == []


def test_confirmed_in_play_is_not_flagged_presumed():
    conn = _conn_with_match()
    out = _run(conn, [_fixture("IN_PLAY", home=1, away=0)],
               now=KICKOFF + timedelta(minutes=30))
    assert out[0]["presumed"] is False
    assert (out[0]["score1"], out[0]["score2"]) == (1, 0)


def test_other_statuses_are_still_excluded():
    """POSTPONED/CANCELLED past kickoff must not be presumed live."""
    conn = _conn_with_match()
    for status in ("POSTPONED", "CANCELLED", "SUSPENDED", "FINISHED"):
        assert _run(conn, [_fixture(status)],
                    now=KICKOFF + timedelta(minutes=18)) == [], status


# ── frontend wiring ───────────────────────────────────────────────────────────

def test_ticker_js_labels_presumed_scores():
    import os
    root = os.path.dirname(os.path.dirname(__file__))
    with open(os.path.join(root, "static", "js", "live.js"), encoding="utf-8") as fh:
        js = fh.read()
    assert "m.presumed" in js
    assert "not yet confirmed" in js
