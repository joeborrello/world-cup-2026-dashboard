"""Tests for the auto-deploy decision logic (deploy.py).

The git/pm2 side effects in `main()` are integration-level and exercised on the
droplet, but the *decision* — "should this checkout fast-forward?" — is pure and
must never guess. These pin its behavior so a future edit can't accidentally turn
a no-op into an unwanted deploy (or vice-versa).
"""
import deploy


def test_deploys_when_remote_is_ahead():
    assert deploy.needs_deploy("aaaaaaa", "bbbbbbb") is True


def test_noop_when_already_current():
    assert deploy.needs_deploy("aaaaaaa", "aaaaaaa") is False


def test_noop_when_a_sha_is_missing():
    # A failed rev-parse yields an empty string; never deploy on missing info.
    assert deploy.needs_deploy("", "bbbbbbb") is False
    assert deploy.needs_deploy("aaaaaaa", "") is False
    assert deploy.needs_deploy("", "") is False
