"""Tests for the MiroFish-style what-if scenario mapper (JOE-42).

The mapper (scenarios.py) turns a free-form question into a scenario tree via
the same claude-CLI plumbing as the pundit panel, sharing its cache and budget.
These tests pin down:

  * the async contract — POST /api/scenarios answers instantly (cached map or a
    pending envelope; the LLM call runs in the background because it can outlive
    the reverse proxy's 60s read timeout), GET /api/scenarios/status delivers;
  * validation and the budget/limit envelopes (checked before a job spawns);
  * question-normalized caching — repeating a question never re-calls the LLM;
  * error delivery — a failed job reports once via the poll, then a re-ask retries;
  * tree normalization — clamped probabilities, capped width/depth, junk dropped;
  * the page wiring (/what-if, nav link, JS hooks).

Every LLM call is stubbed at pundits._claude_cli and jobs run in-line
(scenarios._spawn is patched), so the suite is offline, deterministic, and free.
"""

import json
import os
from types import SimpleNamespace

import pytest

import app as flask_app
import pundits
import scenarios

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as fh:
        return fh.read()


@pytest.fixture
def client():
    flask_app.app.config['TESTING'] = True
    with flask_app.app.test_client() as c:
        yield c


REPLY = json.dumps({
    "reading": "Brazil are the model's favourite, so an early exit reshapes the draw.",
    "scenarios": [
        {"title": "Brazil crash out", "probability": 0.18,
         "summary": "Their half of the bracket opens wide up.",
         "impact": "Argentina benefit",
         "children": [
             {"title": "France stroll", "probability": 55,
              "summary": "The other favourites inherit the easier path."},
         ]},
        {"title": "Brazil survive", "probability": "82%",
         "summary": "Business as usual.", "impact": None},
    ],
    "bottom_line": "An upset is live but unlikely; France gain the most.",
})

USAGE = SimpleNamespace(input_tokens=1000, output_tokens=500)


@pytest.fixture
def stub_llm(monkeypatch):
    """Offline harness: canned CLI reply, no billing rows in the real DB, no
    Monte-Carlo run, CLI 'installed', background jobs run in-line so tests are
    deterministic. Yields a call-recording list."""
    calls = []

    def fake_cli(system, user_content):
        calls.append((system, user_content))
        return fake_cli.reply, USAGE
    fake_cli.reply = REPLY

    monkeypatch.setattr(pundits, "_claude_cli", fake_cli)
    monkeypatch.setattr(pundits, "available", lambda: True)
    monkeypatch.setattr(pundits, "_log_call", lambda conn, scope, usage: None)
    monkeypatch.setattr(scenarios, "_context", lambda conn, preds: "MODEL CONTEXT")
    monkeypatch.setattr(scenarios.predict, "predictions", lambda conn: {"teams": {}})
    monkeypatch.setattr(scenarios, "_spawn", lambda fn, *args: fn(*args))
    # scenario answers must not linger in the shared dev DB between test runs
    import db
    scenarios._jobs.clear()
    conn = db.connect()
    conn.execute("DROP TABLE IF EXISTS pundit_cache")
    conn.execute("DROP TABLE IF EXISTS pundit_calls")
    conn.commit()
    conn.close()
    yield calls
    scenarios._jobs.clear()
    conn = db.connect()
    conn.execute("DROP TABLE IF EXISTS pundit_cache")
    conn.execute("DROP TABLE IF EXISTS pundit_calls")
    conn.commit()
    conn.close()


QUESTION = "What if Brazil lose in the Round of 32?"


def _ask(client, question=QUESTION):
    """Drive the async handshake the way the page does: POST, and if the answer
    is pending, collect it from the status endpoint (jobs ran in-line)."""
    r = client.post('/api/scenarios', json={'question': question})
    d = r.get_json()
    if r.status_code == 200 and d.get('pending'):
        r = client.get('/api/scenarios/status', query_string={'question': question})
        d = r.get_json()
    return r, d


# ── the async API contract ───────────────────────────────────────────────────
def test_post_answers_instantly_with_pending(client, stub_llm):
    """The POST must never wait on the LLM (that's what 504'd behind nginx):
    a fresh question gets a pending envelope, the status endpoint delivers."""
    r = client.post('/api/scenarios', json={'question': QUESTION})
    assert r.status_code == 200
    d = r.get_json()
    assert d['available'] is True and d['pending'] is True
    assert d['question'] == QUESTION
    assert 'budget' in d
    assert 'scenarios' not in d               # the map arrives via the poll


def test_status_poll_returns_scenario_map(client, stub_llm):
    r, d = _ask(client)
    assert r.status_code == 200
    assert d['available'] is True and d['cached'] is False
    assert d['question'] == QUESTION
    assert d['reading'].startswith("Brazil are")
    assert d['bottom_line'].startswith("An upset")
    assert 'budget' in d
    # the model context and question both reached the LLM
    system, user = stub_llm[0]
    assert QUESTION in user and "MODEL CONTEXT" in user
    assert "strict JSON" in system
    # tree: two branches, one with a child
    assert [s['title'] for s in d['scenarios']] == ["Brazil crash out", "Brazil survive"]
    assert d['scenarios'][0]['children'][0]['title'] == "France stroll"


def test_poll_while_job_running_stays_pending(client, stub_llm):
    scope = scenarios._scope(scenarios.normalize_question(QUESTION))
    scenarios._jobs[scope] = {"status": "running"}     # a CLI call is in flight
    d = client.get('/api/scenarios/status',
                   query_string={'question': QUESTION}).get_json()
    assert d['available'] is True and d['pending'] is True
    # re-POSTing the same question must join the running job, not double-bill
    d = client.post('/api/scenarios', json={'question': QUESTION}).get_json()
    assert d['pending'] is True
    assert stub_llm == []


def test_poll_with_nothing_in_flight_says_so(client, stub_llm):
    d = client.get('/api/scenarios/status',
                   query_string={'question': QUESTION}).get_json()
    assert d['available'] is False
    assert 'ask it again' in d['message']


def test_done_job_survives_a_state_hash_change(client, stub_llm):
    """If a result lands while a map is being drawn, the cache key moves on —
    the finished job's stashed payload must still answer the poll."""
    client.post('/api/scenarios', json={'question': QUESTION})   # job ran in-line
    import db
    conn = db.connect()
    conn.execute("DELETE FROM pundit_cache")   # simulate the state hash moving on
    conn.commit()
    conn.close()
    d = client.get('/api/scenarios/status',
                   query_string={'question': QUESTION}).get_json()
    assert d['available'] is True and d['cached'] is False
    assert d['scenarios'][0]['title'] == "Brazil crash out"


def test_probabilities_normalized_to_unit_interval(client, stub_llm):
    _, d = _ask(client)
    assert d['scenarios'][0]['probability'] == 0.18
    assert d['scenarios'][1]['probability'] == 0.82      # "82%" string form
    assert d['scenarios'][0]['children'][0]['probability'] == 0.55  # bare 55 = percent


def test_repeat_question_is_cached_and_free(client, stub_llm):
    _ask(client)
    # same question modulo case/whitespace → same cache row, answered by the
    # POST itself (no pending round-trip), no second LLM call
    d = client.post('/api/scenarios',
                    json={'question': '  what IF Brazil  lose in the round of 32?  '}).get_json()
    assert d['cached'] is True
    assert 'pending' not in d
    assert d['scenarios'][0]['title'] == "Brazil crash out"
    assert len(stub_llm) == 1


def test_different_question_misses_cache(client, stub_llm):
    _ask(client)
    _ask(client, 'What if Spain win every match 5-0?')
    assert len(stub_llm) == 2


def test_question_validation(client, stub_llm):
    for bad in ('', 'hi', 'x' * 301):
        for r in (client.post('/api/scenarios', json={'question': bad}),
                  client.get('/api/scenarios/status', query_string={'question': bad})):
            assert r.status_code == 400
            d = r.get_json()
            assert d['available'] is False and d['error'] == 'bad_question'
    assert stub_llm == []                     # nothing hit the LLM
    r = client.post('/api/scenarios')         # no JSON body at all
    assert r.status_code == 400


def test_unavailable_without_cli(client, stub_llm, monkeypatch):
    monkeypatch.setattr(pundits, "available", lambda: False)
    d = client.post('/api/scenarios', json={'question': QUESTION}).get_json()
    assert d['available'] is False and 'claude CLI' in d['message']
    assert stub_llm == []


def test_budget_cap_blocks_fresh_calls(client, stub_llm, monkeypatch):
    capped = dict(day_used=40, day_max=50, day_cap=40, month_spent=1.0,
                  month_budget=5.0, month_cap=4.0, reserve_pct=20,
                  month_pct=20.0, model='test')
    monkeypatch.setattr(pundits, "budget_status", lambda conn: capped)
    d = client.post('/api/scenarios', json={'question': QUESTION}).get_json()
    assert d['available'] is False and d['limited'] is True
    assert 'Daily pundit cap' in d['message']
    assert stub_llm == []
    assert scenarios._jobs == {}              # nothing spawned


# ── tolerant parsing / tree normalization ────────────────────────────────────
def test_fenced_json_tolerated(client, stub_llm):
    pundits._claude_cli.reply = "```json\n" + REPLY + "\n```"
    _, d = _ask(client)
    assert d['scenarios'][0]['title'] == "Brazil crash out"


def test_non_json_reply_degrades_to_prose(client, stub_llm):
    pundits._claude_cli.reply = "Honestly, Brazil will be fine."
    _, d = _ask(client)
    assert d['available'] is True
    assert d['scenarios'] == []
    assert d['reading'] == "Honestly, Brazil will be fine."


def test_cli_failure_is_reported_once_then_retryable(client, stub_llm, monkeypatch):
    good = pundits._claude_cli

    def boom(system, user_content):
        raise RuntimeError("CLI exploded")
    monkeypatch.setattr(pundits, "_claude_cli", boom)
    _, d = _ask(client)
    assert d['available'] is False and 'CLI exploded' in d['message']
    # the failed job was cleared with the report — asking again retries fresh
    monkeypatch.setattr(pundits, "_claude_cli", good)
    _, d = _ask(client)
    assert d['available'] is True
    assert d['scenarios'][0]['title'] == "Brazil crash out"
    assert len(stub_llm) == 1                 # only the retry reached the LLM


def test_tree_is_clamped():
    deep = {"title": "d0", "children": [{"title": "d1", "children": [
        {"title": "d2", "children": [{"title": "d3"}]}]}]}
    data = {
        "reading": "r",
        "scenarios": [deep] + [{"title": f"s{i}"} for i in range(9)] +
                     [{"summary": "no title, dropped"}, "not a dict"],
        "bottom_line": "b",
    }
    out = scenarios._normalize_tree(data, "raw")
    assert len(out['scenarios']) == scenarios.MAX_BRANCHES
    # depth capped at MAX_DEPTH levels below the root question
    node, depth = out['scenarios'][0], 1
    while node['children']:
        node, depth = node['children'][0], depth + 1
    assert depth == scenarios.MAX_DEPTH
    # probability junk → None, out-of-range clamped
    assert scenarios._prob("not a number") is None
    assert scenarios._prob(-3) == 0.0
    assert scenarios._prob(150) == 1.0
    assert scenarios._prob(0.5) == 0.5


# ── page wiring ──────────────────────────────────────────────────────────────
def test_what_if_page_serves(client):
    html = client.get('/what-if').get_data(as_text=True)
    assert 'wiQuestion' in html and 'wiGo' in html and 'wiOut' in html
    assert 'js/whatif.js' in html
    assert 'api_scenarios' not in html        # url_for resolved, not leaked


def test_nav_links_to_what_if(client):
    html = client.get('/predictions').get_data(as_text=True)
    assert '/what-if' in html                 # topbar nav + the panel cross-link


def test_whatif_js_escapes_llm_output():
    js = _read('static', 'js', 'whatif.js')
    assert 'esc' in js and '&lt;' in js       # LLM/user text is HTML-escaped
    assert 'askUrl' in js and 'wi-tree' in js
    # the page must poll for the answer, not hang on the POST
    assert 'statusUrl' in js and 'pending' in js


def test_whatif_page_exposes_status_url(client):
    html = client.get('/what-if').get_data(as_text=True)
    assert 'statusUrl' in html
    assert '/api/scenarios/status' in html
