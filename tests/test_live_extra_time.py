"""Live display says when a knockout match is formally in extra time (JOE-37).

Before this, a match deep into extra time just read "90+'" (estimate) or a bare
feed minute like "105'" — indistinguishable from second-half stoppage. These
tests pin the new labels:

  * a feed minute past 90 on a knockout match reads "ET 105'" (capped at
    "ET 120+'"), while group matches — which cannot have extra time — keep the
    plain feed minute;
  * a PAUSED knockout past regulation is the extra-time interval ("ET break"),
    not "HT";
  * the kickoff-elapsed estimate stays a cautious "90+'" while second-half
    stoppage could still explain the clock, then degrades to a minute-less
    "ET" once it can't;
  * `live_matches` flags such matches `extra_time` for the frontend, and the
    ticker JS renders the server label instead of hardcoding paused -> "HT".
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

KICKOFF = datetime(2026, 7, 4, 16, 0, tzinfo=timezone.utc)


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


# ── feed-minute path ──────────────────────────────────────────────────────────

def test_feed_minute_past_ninety_is_extra_time_on_a_knockout():
    got = live.live_minute({"minute": 105}, KICKOFF,
                           KICKOFF + timedelta(minutes=125), "in_play", knockout=True)
    assert got == "ET 105'"


def test_feed_minute_ninety_is_still_regulation():
    # 90' is the last regulation minute (stoppage): not yet formally ET.
    got = live.live_minute({"minute": 90}, KICKOFF,
                           KICKOFF + timedelta(minutes=108), "in_play", knockout=True)
    assert got == "90'"


def test_feed_minute_caps_at_one_twenty_plus():
    got = live.live_minute({"minute": 123}, KICKOFF,
                           KICKOFF + timedelta(minutes=150), "in_play", knockout=True)
    assert got == "ET 120+'"


def test_group_match_never_reads_extra_time():
    # Group matches can't go to ET — a >90 feed minute is stoppage, shown as-is.
    got = live.live_minute({"minute": 93}, KICKOFF,
                           KICKOFF + timedelta(minutes=110), "in_play", knockout=False)
    assert got == "93'"


def test_unparseable_feed_minute_falls_back_to_verbatim():
    # football-data occasionally styles stoppage as "90+3" — shown untouched.
    got = live.live_minute({"minute": "90+3"}, KICKOFF,
                           KICKOFF + timedelta(minutes=110), "in_play", knockout=True)
    assert got == "90+3'"


# ── paused (interval) path ────────────────────────────────────────────────────

def test_paused_knockout_past_regulation_is_the_et_break():
    # PAUSED with the feed clock at/after 90 on a knockout = the ET interval.
    got = live.live_minute({"minute": 90}, KICKOFF,
                           KICKOFF + timedelta(minutes=110), "paused", knockout=True)
    assert got == "ET break"


def test_paused_knockout_without_feed_minute_uses_elapsed_time():
    # No feed minute, but 110 real minutes in a pause can't be half-time.
    got = live.live_minute({}, KICKOFF,
                           KICKOFF + timedelta(minutes=110), "paused", knockout=True)
    assert got == "ET break"


def test_paused_at_the_half_is_still_half_time():
    got = live.live_minute({"minute": 45}, KICKOFF,
                           KICKOFF + timedelta(minutes=50), "paused", knockout=True)
    assert got == "HT"
    got = live.live_minute({}, KICKOFF,
                           KICKOFF + timedelta(minutes=50), "paused", knockout=True)
    assert got == "HT"


def test_paused_group_match_is_always_half_time():
    got = live.live_minute({"minute": 90}, KICKOFF,
                           KICKOFF + timedelta(minutes=110), "paused", knockout=False)
    assert got == "HT"


# ── kickoff-elapsed estimate path ─────────────────────────────────────────────

def test_estimate_stays_ninety_plus_while_stoppage_could_explain_it():
    # 108 elapsed -> estimated 93': plausibly second-half stoppage, stay "90+'".
    got = live.live_minute({}, KICKOFF,
                           KICKOFF + timedelta(minutes=108), "in_play", knockout=True)
    assert got == "90+'"


def test_estimate_presumes_extra_time_once_stoppage_cannot():
    # 118 elapsed -> estimated 103': regulation + stoppage must be over. The
    # estimate can't place the ET minute (unknown stoppage), so a bare "ET".
    got = live.live_minute({}, KICKOFF,
                           KICKOFF + timedelta(minutes=118), "in_play", knockout=True)
    assert got == "ET"


def test_estimate_for_group_match_still_caps_at_ninety_plus():
    got = live.live_minute({}, KICKOFF,
                           KICKOFF + timedelta(minutes=130), "in_play", knockout=False)
    assert got == "90+'"


# ── live_matches integration ──────────────────────────────────────────────────

def _conn_with_match(stage, round_label, group_letter):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_schema(conn)
    conn.execute(
        "INSERT INTO matches (num, stage, round_label, group_letter, utc_datetime, "
        "team1, team2, score1, score2, status) VALUES (74, ?, ?, ?, ?, "
        "'Germany', 'Paraguay', NULL, NULL, 'scheduled')",
        (stage, round_label, group_letter, KICKOFF.isoformat()),
    )
    conn.commit()
    return conn


def _fixture(minute):
    return {
        "status": "IN_PLAY",
        "utcDate": "2026-07-04T16:00:00Z",
        "minute": minute,
        "homeTeam": {"name": "Germany"},
        "awayTeam": {"name": "Paraguay"},
        "score": {"fullTime": {"home": 1, "away": 1}},
    }


def _run(conn, fixtures):
    live._CACHE.clear()  # bypass the TTL cache (now keyed per edition)
    with mock.patch.object(config, "FOOTBALL_DATA_API_KEY", "test-key"), \
            mock.patch.object(live.requests, "get") as g:
        g.return_value.json.return_value = {"matches": fixtures}
        g.return_value.raise_for_status.return_value = None
        return live.live_matches(conn)


def test_knockout_in_extra_time_is_flagged_and_labelled():
    conn = _conn_with_match("knockout", "Round of 16", None)
    out = _run(conn, [_fixture(minute=98)])
    assert len(out) == 1
    assert out[0]["minute"] == "ET 98'"
    assert out[0]["extra_time"] is True


def test_knockout_in_regulation_is_not_flagged():
    conn = _conn_with_match("knockout", "Round of 16", None)
    out = _run(conn, [_fixture(minute=63)])
    assert out[0]["minute"] == "63'"
    assert out[0]["extra_time"] is False


def test_group_match_is_never_flagged_extra_time():
    conn = _conn_with_match("group", "Group F", "F")
    out = _run(conn, [_fixture(minute=93)])
    assert out[0]["minute"] == "93'"
    assert out[0]["extra_time"] is False


# ── frontend wiring ───────────────────────────────────────────────────────────

def test_ticker_js_renders_server_label_and_extra_time_class():
    js = _read("static", "js", "live.js")
    # paused shows the server's label ("HT" or "ET break"), no hardcoded 'HT'-only
    assert "return m.minute || 'HT'" in js
    # extra-time matches get the `et` style hook in the ticker and on cards
    assert js.count("m.extra_time") >= 2


def test_css_styles_the_extra_time_badge():
    css = _read("static", "css", "style.css")
    assert ".lt-min.et" in css and ".mc-live.et" in css
