#!/usr/bin/env python3
"""Auto-deploy: fast-forward the live droplet checkout to origin/main and restart.

Why this exists: the Golden Boot tracker (JOE-13) shipped to `main` twice without
ever reaching the live app — merging is not deploying, and the manual
`git pull && pm2 restart` step was forgotten both times. This closes that gap.

Run on a short pm2 cron (see `ecosystem.config.js`). Each tick it fetches
`origin/main` and, if the live checkout is behind, **fast-forwards only** and
restarts the gunicorn app so the new code (and the idempotent schema applied on
startup) takes effect. The 7-minute updater cron relaunches its script fresh
every run, so it picks up new code on its own next tick — only the long-running
web process needs an explicit restart.

Safety:
  * no-op when already current (cheap, quiet);
  * fast-forward only — never rebases, resets, or discards local state, so an
    accidentally-dirty or diverged working tree blocks the deploy loudly instead
    of silently throwing work away;
  * a failed restart is reported but never crashes the cron tick.
"""
import os
import subprocess
import sys

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
REMOTE = "origin"
BRANCH = "main"
APP_PROCESS = "worldcup-2026"  # the long-running gunicorn process to restart


def needs_deploy(local_sha, remote_sha):
    """True when the checkout should fast-forward.

    Pure decision logic (no side effects) so it is unit-testable: deploy iff both
    SHAs resolved and they differ. Equal SHAs — or a missing one (a failed
    rev-parse) — mean "do nothing", never "guess".
    """
    return bool(local_sha) and bool(remote_sha) and local_sha != remote_sha


def _git(*args):
    return subprocess.run(
        ["git", *args], cwd=REPO_DIR, capture_output=True, text=True
    )


def main():
    fetch = _git("fetch", REMOTE, BRANCH)
    if fetch.returncode != 0:
        print(f"git fetch failed; skipping:\n{fetch.stderr}", file=sys.stderr)
        return 1

    local = _git("rev-parse", "HEAD").stdout.strip()
    remote = _git("rev-parse", f"{REMOTE}/{BRANCH}").stdout.strip()

    if not needs_deploy(local, remote):
        print(f"up to date at {local[:8]}")
        return 0

    ff = _git("merge", "--ff-only", f"{REMOTE}/{BRANCH}")
    if ff.returncode != 0:
        print(
            "fast-forward failed (dirty tree or diverged from "
            f"{REMOTE}/{BRANCH}); skipping deploy:\n{ff.stderr}",
            file=sys.stderr,
        )
        return 1

    print(f"deployed {local[:8]} -> {remote[:8]}; restarting {APP_PROCESS}")
    restart = subprocess.run(
        ["pm2", "restart", APP_PROCESS], capture_output=True, text=True
    )
    if restart.returncode != 0:
        print(f"pm2 restart failed:\n{restart.stderr}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
