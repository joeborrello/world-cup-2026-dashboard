"""Pytest bootstrap: make sure the SQLite DB exists before any test runs.

The dashboard's `data/worldcup.db` is a generated artifact (it's gitignored and
rebuilt by `seed_data.py`), and `db.connect()` opens the file without creating
the schema. On a developer machine the DB already exists, so the tests pass — but
on a clean checkout (CI, a fresh agent run) every DB-backed test errors with
`sqlite3.OperationalError: no such table: ...`. That is what kept failing the
JOE-10 job five times until it was marked blocked.

Seeding here, once per session, fixes it for every test without each test needing
to know about the DB. Seeding is offline (`prefer_remote=False` reads the
committed `data/openfootball-2026.json`, no network) and idempotent.
"""
import os
import sqlite3

import config as app_config


def _needs_seed():
    """True if the DB file is missing or has no match data yet."""
    if not os.path.exists(app_config.DB_PATH):
        return True
    try:
        conn = sqlite3.connect(app_config.DB_PATH)
        try:
            row = conn.execute("SELECT COUNT(*) FROM matches").fetchone()
        finally:
            conn.close()
        return not row or row[0] == 0
    except sqlite3.Error:
        # table missing / corrupt file → reseed from scratch
        return True


def pytest_configure(config):
    if _needs_seed():
        os.makedirs(app_config.DATA_DIR, exist_ok=True)
        import seed_data
        seed_data.seed(prefer_remote=False)
