#!/usr/bin/env python3
"""Build the static GitHub Pages copy of the dashboard (static-site generation).

GitHub Pages can't run Flask, hold the DB, or keep API secrets — so instead of
serving the app, the droplet *builds* it: each page is rendered through the local
Flask app, its URLs are rewritten for Pages (a <base> tag; /worldcup/static ->
appstatic; every /worldcup/api/* -> a committed JSON file), the static assets are
copied, and the JSON each page's JS reads is published. The result under docs/ is
a fully self-contained site — no backend, no secrets — that github.io serves on
its own. The droplet is now just a build-and-publish step (run by pm2 cron).

Two features can't be static and are handled gracefully:
  * the AI pundit panel needs a server + secret key -> shows a "use the live app"
    note (pundits.json: available=false);
  * the weather-heavy daily map -> its nav/links point at the live droplet app.
"""

import json
import os
import shutil
import subprocess
import urllib.request
from datetime import datetime, timezone

import config
import db
import flags
import predict
import publish_pages

BASE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(BASE, "docs")
DATA = os.path.join(DOCS, "data")
APPSTATIC = os.path.join(DOCS, "appstatic")
LOCAL = f"http://127.0.0.1:{config.PORT}"          # local Flask (root; nginx adds the subpath)
PAGES_BASE = os.environ.get("PAGES_BASE", "/world-cup-2026-dashboard/")
DROPLET = "https://droplet.josephborrello.com/worldcup"

# route -> output slug (each saved as docs/<slug>/index.html)
PAGES = {
    "/": "today",
    "/groups": "groups",
    "/bracket": "bracket",
    "/predictions": "predictions",
    "/schedule-map": "schedule-map",
    "/team-map": "team-map",
}

# /worldcup/api/* -> committed JSON file (longest paths first so prefixes don't
# clobber). Endpoints not published (weather/alerts) resolve to absent files; the
# page JS already .catch()es those.
API_MAP = [
    ("/worldcup/api/pundits/budget", "data/pundits_budget.json"),
    ("/worldcup/api/bracket/predicted", "data/bracket_predicted.json"),
    ("/worldcup/api/predictions", "data/predictions.json"),
    ("/worldcup/api/pundits", "data/pundits.json"),
    ("/worldcup/api/live", "data/live_matches.json"),
    ("/worldcup/api/matches", "data/matches.json"),
    ("/worldcup/api/venues", "data/venues.json"),
    ("/worldcup/api/teams", "data/teams.json"),
    ("/worldcup/api/days", "data/days.json"),
    ("/worldcup/api/weather", "data/weather.json"),
    ("/worldcup/api/alerts", "data/alerts.json"),
]

# nav / internal links -> static slug (or the live app for the deferred daily map)
NAV_MAP = [
    ("/worldcup/schedule-map", "schedule-map/"),
    ("/worldcup/team-map", "team-map/"),
    ("/worldcup/predictions", "predictions/"),
    ("/worldcup/bracket", "bracket/"),
    ("/worldcup/groups", "groups/"),
    ("/worldcup/map", f"{DROPLET}/map"),       # weather-heavy daily map: live app
]


def _get(path):
    with urllib.request.urlopen(LOCAL + path, timeout=30) as r:
        return r.read().decode("utf-8")


def _write(rel, text):
    path = os.path.join(DOCS, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _rewrite(html):
    for src, dst in API_MAP:
        html = html.replace(src, dst)
    html = html.replace("/worldcup/static/", "appstatic/")
    for src, dst in NAV_MAP:
        html = html.replace(src, dst)
    html = html.replace('href="/worldcup/"', 'href="today/"')      # home / brand
    html = html.replace(
        "<head>", f'<head>\n  <base href="{PAGES_BASE}">\n  '
        "<!-- static build: github.io serves this; data lives in /data -->", 1)
    return html


def _publish_data(conn):
    # deterministic DB-derived feeds (HTTP-fetch the app's own serializers)
    for ep, rel in (("/api/matches", "data/matches.json"),
                    ("/api/venues", "data/venues.json"),
                    ("/api/teams", "data/teams.json"),
                    ("/api/days", "data/days.json"),
                    ("/api/live", "data/live_matches.json")):
        _write(rel, _get(ep))

    # predictions + projected bracket — computed with a fixed seed so the files
    # are byte-stable between builds (no commit churn) until results change.
    pred = predict.predictions(conn, seed=2026)
    teams = {t: {**v, "code": flags.flag_code(t)} for t, v in pred["teams"].items()}
    _write("data/predictions.json", json.dumps(
        {"sims": pred["sims"], "n_finished": pred["n_finished"], "teams": teams},
        separators=(",", ":")))

    def _side(s):
        return None if not s else {"team": s["team"], "conf": s["conf"],
                                   "code": flags.flag_code(s["team"])}
    slots = {num: {"round": e["round"], "team1": _side(e["team1"]),
                   "team2": _side(e["team2"])} for num, e in pred["slots"].items()}
    _write("data/bracket_predicted.json", json.dumps(
        {"depth": "final", "n_finished": pred["n_finished"], "slots": slots},
        separators=(",", ":")))

    # pundits: can't run without a server + secret key -> graceful note
    _write("data/pundits.json", json.dumps({
        "available": False,
        "message": "AI pundit panels run on the live dashboard — they need a "
                   "server and an API key. Open the live app to generate fresh takes."}))
    _write("data/pundits_budget.json", json.dumps({"enabled": False}))

    # landing-page live strip (reuse the existing snapshot builder, unchanged)
    strip = publish_pages.build(conn)
    strip["generated"] = datetime.now(timezone.utc).isoformat()
    prev = None
    sp = os.path.join(DATA, "live.json")
    if os.path.exists(sp):
        try:
            prev = json.load(open(sp))
        except (ValueError, OSError):
            prev = None
    if prev is None or publish_pages._meaningful(prev) != publish_pages._meaningful(strip):
        _write("data/live.json", json.dumps(strip, separators=(",", ":"), ensure_ascii=False))


def _copy_assets():
    dst = APPSTATIC
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(os.path.join(BASE, "static"), dst)


def _git(*args):
    return subprocess.run(["git", "-C", BASE, *args], capture_output=True, text=True)


def main():
    conn = db.connect()
    _publish_data(conn)
    conn.close()
    _copy_assets()
    for route, slug in PAGES.items():
        _write(f"{slug}/index.html", _rewrite(_get(route)))

    _git("add", "docs")
    if _git("diff", "--cached", "--quiet").returncode == 0:
        print("static site unchanged — nothing to publish")
        return
    msg = "data: rebuild static site " + datetime.now(timezone.utc).isoformat()
    _git("commit", "-m", msg)
    push = _git("push", "origin", "main")
    if push.returncode:
        _git("-c", "rebase.autostash=true", "pull", "--rebase", "origin", "main")
        push = _git("push", "origin", "main")
    if push.returncode:
        raise SystemExit("git push failed:\n" + push.stderr)
    print("published:", msg)


if __name__ == "__main__":
    main()
