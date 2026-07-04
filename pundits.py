"""MiroFish-inspired "AI pundit panel".

A small swarm of opinionated personas (the idea borrowed from MiroFish's
multi-agent simulation) debates a group or the title race via Claude,
*grounded in the statistical model's numbers* so the narrative rides on the
odds rather than replacing them. Generation is lazy (on user request) and
persisted in `pundit_cache` so we don't re-pay the LLM until results change.

Claude access is the `claude` CLI in print mode, authenticated with Joe's Claude
Max (5x) subscription (~/.claude OAuth login) — NOT the pay-as-you-go API. We
strip ANTHROPIC_API_KEY from the subprocess env so a key in the environment can
never silently switch billing to the API account. The self-tracked $ budget is
therefore informational (API-equivalent cost), kept as a usage guardrail.

Degrades gracefully: with no claude CLI the panel is simply unavailable and the
statistical predictions are unaffected.
"""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace

import compute
import config
import predict

# Model pricing, USD per 1M tokens (input, output) — for the self-tracked budget.
PRICING = {
    "claude-opus-4-8": (5.0, 25.0), "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0), "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0), "claude-fable-5": (10.0, 50.0),
}
DEFAULT_PRICING = (5.0, 25.0)


def available() -> bool:
    """The panel needs the claude CLI (subscription login) on the server."""
    return shutil.which("claude") is not None


def _claude_cli(system, user_content):
    """One single-shot `claude -p` call on the Max subscription: all tools disabled,
    MCP pinned off, API key stripped so the ~/.claude login is used (never API billing).
    Returns (text, usage) — usage has .input_tokens/.output_tokens for _log_call."""
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)   # critical: subscription login, not the key
    sysfile = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                         encoding="utf-8") as f:
            f.write(system)
            sysfile = f.name
        proc = subprocess.run(
            [shutil.which("claude"), "-p", "--output-format", "json",
             "--model", config.PUNDIT_MODEL,
             "--system-prompt-file", sysfile, "--strict-mcp-config",
             "--disallowed-tools", "Bash", "Edit", "Write", "Read", "Glob", "Grep",
             "WebSearch", "WebFetch", "Task", "NotebookEdit", "TodoWrite"],
            input=user_content, capture_output=True, text=True, timeout=300, env=env)
    finally:
        if sysfile:
            try:
                os.unlink(sysfile)
            except OSError:
                pass
    try:
        data = json.loads(proc.stdout.strip())
    except json.JSONDecodeError:
        raise RuntimeError((proc.stderr or proc.stdout or "no CLI output")[:300])
    if data.get("is_error") or data.get("type") != "result":
        raise RuntimeError(str(data.get("result") or data.get("error")
                               or "claude CLI error")[:300])
    u = data.get("usage") or {}
    usage = SimpleNamespace(input_tokens=int(u.get("input_tokens") or 0),
                            output_tokens=int(u.get("output_tokens") or 0))
    return (data.get("result") or "").strip(), usage

PERSONAS = [
    ("The Analyst", "trusts the data and Elo/probability model above all"),
    ("The Romantic", "loves an underdog story and tournament fairy tales"),
    ("The Tactician", "focuses on styles, matchups and tactical fit"),
    ("The Veteran", "weighs experience, big-game temperament and form"),
]

SYSTEM = (
    "You are a panel of four football pundits previewing the 2026 World Cup. "
    "The members are: " + "; ".join(f"{n} ({d})" for n, d in PERSONAS) + ". "
    "You are given a statistical model's probabilities and the current standings. "
    "Ground your opinions in those numbers (you may push back on them with reasoning, "
    "but acknowledge them). Be vivid, concise and fun, not generic. "
    "Respond ONLY with strict JSON of the form: "
    '{"pundits":[{"name":"The Analyst","take":"...1-2 sentences...","lean":"Team"}],'
    '"consensus":"...2-3 sentences...","lean":"Team"}. No markdown, no prose outside the JSON.'
)


def _ensure_cache(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pundit_cache (
            scope      TEXT,
            state_hash TEXT,
            payload    TEXT,
            created_at TEXT,
            PRIMARY KEY (scope, state_hash)
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pundit_calls (
            ts         TEXT,      -- ISO-8601 UTC of the LLM call
            scope      TEXT,
            model      TEXT,
            in_tokens  INTEGER,
            out_tokens INTEGER,
            est_cost   REAL       -- USD, estimated from token usage
        )""")
    conn.commit()


def budget_status(conn):
    """Daily call count + month-to-date estimated spend against the configured caps."""
    _ensure_cache(conn)
    now = datetime.now(timezone.utc)
    day = conn.execute(
        "SELECT COUNT(*) c FROM pundit_calls WHERE substr(ts,1,10)=?",
        (now.strftime("%Y-%m-%d"),)).fetchone()["c"]
    month = conn.execute(
        "SELECT COALESCE(SUM(est_cost),0) s FROM pundit_calls WHERE substr(ts,1,7)=?",
        (now.strftime("%Y-%m"),)).fetchone()["s"]
    budget = config.PUNDIT_MONTHLY_BUDGET
    reserve = max(0, min(90, config.PUNDIT_RESERVE_PCT))
    usable = (100 - reserve) / 100.0
    day_cap = int(config.PUNDIT_MAX_PER_DAY * usable)     # floor — pundits stop here
    month_cap = round(budget * usable, 4)
    return {
        "day_used": day, "day_max": config.PUNDIT_MAX_PER_DAY, "day_cap": day_cap,
        "month_spent": round(month, 4), "month_budget": budget, "month_cap": month_cap,
        "reserve_pct": reserve,
        "month_pct": round(100 * month / budget, 1) if budget else 0,
        "model": config.PUNDIT_MODEL,
    }


def limit_message(bs):
    """If a fresh LLM call is blocked by the caps, say why; None when allowed.

    Shared by the pundit panel and the what-if scenario mapper — both bill the
    same daily/monthly budget, so one gate serves both.
    """
    if bs["day_used"] >= bs["day_cap"]:
        return (f"Daily pundit cap reached ({bs['day_cap']} of {bs['day_max']} — "
                f"{bs['reserve_pct']}% held in reserve). Resets at 00:00 UTC.")
    if bs["month_spent"] >= bs["month_cap"]:
        return (f"Monthly pundit cap reached (${bs['month_cap']:.2f} of "
                f"${bs['month_budget']:.2f} — {bs['reserve_pct']}% reserved). "
                f"${bs['month_spent']:.2f} used; resets next month.")
    return None


def _log_call(conn, scope, usage):
    pin, pout = PRICING.get(config.PUNDIT_MODEL, DEFAULT_PRICING)
    cost = usage.input_tokens / 1e6 * pin + usage.output_tokens / 1e6 * pout
    conn.execute(
        "INSERT INTO pundit_calls (ts, scope, model, in_tokens, out_tokens, est_cost) "
        "VALUES (?,?,?,?,?,?)",
        (datetime.now(timezone.utc).isoformat(), scope, config.PUNDIT_MODEL,
         usage.input_tokens, usage.output_tokens, cost))
    conn.commit()


def _state_hash(conn, scope):
    n = conn.execute("SELECT COUNT(*) FROM matches WHERE status='finished'").fetchone()[0]
    raw = f"{scope}|{n}|{config.PUNDIT_MODEL}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _group_context(conn, preds, letter):
    standings = compute.compute_standings(conn).get(letter, [])
    lines = []
    for r in standings:
        v = preds["teams"].get(r["team"], {})
        lines.append(
            f"- {r['team']}: Elo {v.get('elo')}, played {r['played']}, {r['points']}pts "
            f"GD{r['gd']:+d}; model P(win group) {v.get('p_first',0)*100:.0f}%, "
            f"P(advance) {v.get('advance',0)*100:.0f}%")
    fx = conn.execute(
        "SELECT team1, team2, status, date FROM matches "
        "WHERE group_letter=? ORDER BY num", (letter,)).fetchall()
    remaining = [f"{m['team1']} vs {m['team2']} ({m['date']})"
                 for m in fx if m["status"] != "finished"]
    body = (f"GROUP {letter} — current table and model odds:\n" + "\n".join(lines))
    if remaining:
        body += "\n\nRemaining group fixtures:\n- " + "\n- ".join(remaining)
    else:
        body += "\n\nAll group matches are complete."
    return body


def _knockout_context(conn, preds):
    top = sorted(preds["teams"].items(), key=lambda kv: -kv[1]["champion"])[:10]
    lines = [f"- {t}: champion {v['champion']*100:.1f}%, reach final {v['final']*100:.1f}%, "
             f"Elo {v['elo']}" for t, v in top]
    darkhorses = sorted(
        ((t, v) for t, v in preds["teams"].items() if v["elo"] < 1850),
        key=lambda kv: -kv[1]["sf"])[:4]
    dh = [f"- {t}: reach semis {v['sf']*100:.1f}%" for t, v in darkhorses]
    return ("TITLE RACE — model odds (top contenders):\n" + "\n".join(lines) +
            "\n\nDark horses to watch:\n" + "\n".join(dh))


def _context(conn, preds, scope):
    if scope.startswith("group:"):
        return _group_context(conn, preds, scope.split(":", 1)[1])
    return _knockout_context(conn, preds)


def _scope_title(scope):
    if scope.startswith("group:"):
        return f"Group {scope.split(':', 1)[1]}"
    return "Title race"


def panel(conn, scope):
    """Return the pundit panel for a scope ('group:A' | 'knockout'), cached."""
    if not available():
        return {"available": False,
                "message": "The claude CLI is not on the server — the AI pundit panel is unavailable."}

    _ensure_cache(conn)
    sh = _state_hash(conn, scope)
    row = conn.execute(
        "SELECT payload FROM pundit_cache WHERE scope=? AND state_hash=?",
        (scope, sh)).fetchone()
    if row:  # cache hits are free — never blocked by the budget
        return {"available": True, "cached": True, "scope": scope,
                "title": _scope_title(scope), "budget": budget_status(conn),
                **json.loads(row["payload"])}

    # cost controls — gated by the reserved daily cap AND the reserved $ budget,
    # so the configured headroom (reserve_pct) is always left open.
    bs = budget_status(conn)
    blocked = limit_message(bs)
    if blocked:
        return {"available": False, "limited": True, "budget": bs, "message": blocked}

    preds = predict.predictions(conn)
    context = _context(conn, preds, scope)

    try:
        text, usage = _claude_cli(SYSTEM, f"Preview: {_scope_title(scope)}.\n\n{context}")
        _log_call(conn, scope, usage)   # bill it the moment the call succeeds
        if text.startswith("```"):
            text = text.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0]
        data = json.loads(text)
        # drop any empty/placeholder pundits (e.g. a truncated trailing entry)
        data["pundits"] = [p for p in data.get("pundits", [])
                           if (p.get("take") or "").strip()]
    except json.JSONDecodeError:
        data = {"pundits": [], "consensus": text, "lean": None}
    except Exception as exc:
        return {"available": False, "message": f"Pundit panel error: {exc}"}

    conn.execute(
        "INSERT OR REPLACE INTO pundit_cache (scope, state_hash, payload, created_at) "
        "VALUES (?,?,?,?)",
        (scope, sh, json.dumps(data), datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return {"available": True, "cached": False, "scope": scope,
            "title": _scope_title(scope), "budget": budget_status(conn), **data}
