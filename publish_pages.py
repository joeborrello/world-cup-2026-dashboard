#!/usr/bin/env python3
"""Publish a small live-data snapshot for the GitHub Pages landing site.

Builds `docs/data/live.json` from the same SQLite DB the dashboard serves
(current phase, today's matches with live scores, and top title odds), then
commits & pushes it — but only when the meaningful content actually changed,
so we don't churn the repo with no-op commits. Run on a schedule by pm2
(see ecosystem.config.js), a couple of minutes behind the results updater.

Safe by design: it stages only docs/data/live.json, and on a rejected push it
rebases with autostash so it never disturbs unrelated local changes.
"""

import json
import os
import subprocess
from datetime import date, datetime, timezone

import config
import db
import live
import predict
from flags import flag_code

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "docs", "data", "live.json")
REL = "docs/data/live.json"          # path as git sees it


def _phase(conn):
    """Label the tournament's current frontier (first not-yet-finished match)."""
    row = conn.execute(
        "SELECT stage, round_label FROM matches WHERE status!='finished' "
        "ORDER BY date, utc_datetime LIMIT 1").fetchone()
    if not row:
        return "Tournament complete"
    return "Group stage" if row["stage"] == "group" else row["round_label"]


def _today(conn):
    today = config.tournament_today().isoformat()
    rows = conn.execute(
        "SELECT * FROM matches WHERE date=? ORDER BY utc_datetime", (today,)).fetchall()
    # overlay live (in-play / paused) scores keyed by match number
    live_by_num = {m["num"]: m for m in live.live_matches(conn)}
    out = []
    for m in rows:
        t1 = m["team1"] or m["team1_slot"]
        t2 = m["team2"] or m["team2_slot"]
        lv = live_by_num.get(m["num"])
        entry = {
            "num": m["num"],
            "team1": t1, "team2": t2,
            "code1": flag_code(m["team1"]), "code2": flag_code(m["team2"]),
            "score1": m["score1"], "score2": m["score2"],
            "status": m["status"],          # scheduled | finished
            "state": None,                  # in_play | paused (live only)
            "utc_datetime": m["utc_datetime"],
            "tag": f"Group {m['group_letter']}" if m["group_letter"] else m["round_label"],
        }
        if lv:
            entry.update(score1=lv["score1"], score2=lv["score2"], state=lv["state"])
        out.append(entry)
    return out


def _title_odds(conn, n=5):
    try:
        # fixed seed -> odds are stable until real results change, so the
        # snapshot doesn't churn on Monte-Carlo noise between runs.
        data = predict.predictions(conn, seed=2026)
    except Exception:
        return []
    top = sorted(data["teams"].items(), key=lambda kv: -kv[1]["champion"])[:n]
    return [{"team": t, "code": flag_code(t),
             "champion": round(v["champion"], 4)} for t, v in top]


def build(conn):
    return {
        "phase": _phase(conn),
        "today": _today(conn),
        "title_odds": _title_odds(conn),
        "live_url": "https://droplet.josephborrello.com/worldcup/",
    }


def _meaningful(payload):
    """Everything except the timestamp — used to decide whether to republish."""
    return {k: v for k, v in payload.items() if k != "generated"}


def _git(*args):
    return subprocess.run(["git", "-C", BASE, *args],
                          capture_output=True, text=True)


def main():
    conn = db.connect()
    payload = build(conn)
    conn.close()

    prev = None
    if os.path.exists(OUT):
        try:
            with open(OUT) as fh:
                prev = json.load(fh)
        except (ValueError, OSError):
            prev = None

    if prev is not None and _meaningful(prev) == _meaningful(payload):
        print("live snapshot unchanged — nothing to publish")
        return

    payload["generated"] = datetime.now(timezone.utc).isoformat()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        json.dump(payload, fh, separators=(",", ":"), ensure_ascii=False)

    # commit only the snapshot, push, and recover from a stale remote once
    _git("add", REL)
    if not _git("diff", "--cached", "--quiet").returncode:
        print("snapshot identical after add — skipping commit")
        return
    msg = "data: refresh live snapshot " + payload["generated"]
    _git("commit", "-m", msg)
    push = _git("push", "origin", "main")
    if push.returncode:
        print("push rejected, rebasing:", push.stderr.strip())
        _git("-c", "rebase.autostash=true", "pull", "--rebase", "origin", "main")
        push = _git("push", "origin", "main")
    if push.returncode:
        raise SystemExit("git push failed:\n" + push.stderr)
    print("published:", msg)


if __name__ == "__main__":
    main()
