"""MiroFish-style free-form scenario mapper.

Where the pundit panel (pundits.py) is a fixed round-table debating a preset
scope, this is the other half of the MiroFish idea: the user asks any "what if"
question about the tournament ("what if Brazil lose to Morocco?", "who benefits
if Group C ends in a three-way tie?") and gets back a *map* — a small tree of
branch scenarios with probabilities and knock-on consequences — grounded in the
statistical model's numbers.

It rides on the pundit plumbing on purpose: the same `claude` CLI call on Joe's
Max subscription (never the API), the same `pundit_cache`/`pundit_calls` tables,
and the same daily/monthly budget, so one set of cost controls governs all LLM
use. Answers are cached by (normalized question, results-state hash): repeating
a question is free until new results land.
"""

import hashlib
import json
import re
from datetime import datetime, timezone

import compute
import predict
import pundits

# Question sanity bounds — a couple of words up to a short paragraph.
MIN_QUESTION, MAX_QUESTION = 8, 300

# Tree shape caps: whatever the model returns is clipped to at most this many
# top-level branches / children per node / levels below the root question.
MAX_BRANCHES, MAX_CHILDREN, MAX_DEPTH = 6, 4, 2

SYSTEM = (
    "You are a football scenario analyst mapping out 2026 World Cup what-if "
    "questions on a whiteboard. You are given the current standings, remaining "
    "fixtures, and a statistical model's odds (Elo + Monte-Carlo). The user asks "
    "a free-form question about a potential scenario or alternative outcome. "
    "Map the plausible branches: 3-5 top-level scenarios, each optionally with "
    "up to 3 child scenarios exploring knock-on consequences (max 2 levels of "
    "children). Ground probabilities in the model's numbers where possible; when "
    "the branches of a level are mutually exclusive and exhaustive their "
    "probabilities should sum to roughly 1. Be concrete and vivid, not generic. "
    "If the question is not about football or this tournament, return a single "
    "scenario politely saying so. Respond ONLY with strict JSON of the form: "
    '{"reading":"1-2 sentences interpreting the question against the model\'s numbers",'
    '"scenarios":[{"title":"short branch name","probability":0.35,'
    '"summary":"1-2 sentences of consequences","impact":"who benefits/suffers, a few words",'
    '"children":[...same shape...]}],'
    '"bottom_line":"2-3 sentences: the headline answer"}. '
    "No markdown, no prose outside the JSON."
)


def normalize_question(question):
    """Canonical form used for the cache key: trimmed, single-spaced."""
    return re.sub(r"\s+", " ", (question or "").strip())


def _scope(question):
    """Cache scope for a question (keys pundit_cache rows and pundit_calls logs)."""
    digest = hashlib.sha1(question.lower().encode()).hexdigest()[:16]
    return f"whatif:{digest}"


def _context(conn, preds):
    """The model's current view of the tournament, compact enough for one prompt:
    title odds, every group table with advance odds, and the remaining fixtures."""
    lines = ["TITLE RACE — model odds (top contenders):"]
    top = sorted(preds["teams"].items(), key=lambda kv: -kv[1]["champion"])[:12]
    for t, v in top:
        lines.append(f"- {t}: champion {v['champion']*100:.1f}%, "
                     f"reach final {v['final']*100:.1f}%, Elo {v['elo']}")

    standings = compute.compute_standings(conn)
    for letter in sorted(standings):
        lines.append(f"\nGROUP {letter}:")
        for r in standings[letter]:
            v = preds["teams"].get(r["team"], {})
            lines.append(f"- {r['team']}: {r['points']}pts from {r['played']}, "
                         f"GD{r['gd']:+d}, Elo {v.get('elo')}; "
                         f"P(win group) {v.get('p_first', 0)*100:.0f}%, "
                         f"P(advance) {v.get('advance', 0)*100:.0f}%")

    fx = conn.execute(
        "SELECT num, stage, round_label, group_letter, date, team1, team2, "
        "team1_slot, team2_slot FROM matches WHERE status != 'finished' "
        "ORDER BY num").fetchall()
    if fx:
        lines.append("\nREMAINING FIXTURES:")
        for m in fx:
            t1 = m["team1"] or m["team1_slot"]
            t2 = m["team2"] or m["team2_slot"]
            where = f"Group {m['group_letter']}" if m["stage"] == "group" \
                else m["round_label"]
            lines.append(f"- #{m['num']} {t1} vs {t2} ({where}, {m['date']})")
    else:
        lines.append("\nThe tournament is complete — every match has been played.")
    return "\n".join(lines)


def _prob(value):
    """Coerce a model-supplied probability to [0,1] or None. Tolerates percent
    forms ('35%', 35) since LLMs mix the two conventions freely."""
    if isinstance(value, str):
        value = value.strip().rstrip("%")
    try:
        p = float(value)
    except (TypeError, ValueError):
        return None
    if 1.0 < p <= 100.0:
        p /= 100.0
    return round(min(max(p, 0.0), 1.0), 3)


def _clean_node(node, depth=1):
    """One validated tree node, or None if it's unusable (no title)."""
    if not isinstance(node, dict):
        return None
    title = str(node.get("title") or "").strip()
    if not title:
        return None
    out = {
        "title": title[:120],
        "summary": str(node.get("summary") or "").strip()[:500],
        "impact": str(node.get("impact") or "").strip()[:80] or None,
        "probability": _prob(node.get("probability")),
        "children": [],
    }
    kids = node.get("children")
    if depth < MAX_DEPTH and isinstance(kids, list):
        out["children"] = [c for c in (_clean_node(k, depth + 1) for k in kids)
                           if c][:MAX_CHILDREN]
    return out


def _normalize_tree(data, raw_text):
    """Clamp whatever JSON came back into the shape the UI renders. A reply that
    parsed but isn't a scenario tree degrades to a prose-only answer."""
    if not isinstance(data, dict):
        return {"reading": raw_text.strip()[:1000], "scenarios": [],
                "bottom_line": ""}
    scenarios = data.get("scenarios")
    nodes = []
    if isinstance(scenarios, list):
        nodes = [c for c in (_clean_node(n) for n in scenarios) if c][:MAX_BRANCHES]
    return {
        "reading": str(data.get("reading") or "").strip()[:1000],
        "scenarios": nodes,
        "bottom_line": str(data.get("bottom_line") or "").strip()[:1000],
    }


def ask(conn, question):
    """Answer a free-form what-if question with a scenario map, cached.

    Returns a dict the API returns verbatim: either an error/limit envelope or
    {"available": True, "cached": ..., "question": ..., "budget": ...,
     "reading": ..., "scenarios": [tree...], "bottom_line": ...}.
    """
    q = normalize_question(question)
    if len(q) < MIN_QUESTION or len(q) > MAX_QUESTION:
        return {"available": False, "error": "bad_question",
                "message": f"Ask a question between {MIN_QUESTION} and "
                           f"{MAX_QUESTION} characters."}
    if not pundits.available():
        return {"available": False,
                "message": "The claude CLI is not on the server — "
                           "the scenario mapper is unavailable."}

    pundits._ensure_cache(conn)
    scope = _scope(q)
    sh = pundits._state_hash(conn, scope)
    row = conn.execute(
        "SELECT payload FROM pundit_cache WHERE scope=? AND state_hash=?",
        (scope, sh)).fetchone()
    if row:  # cache hits are free — never blocked by the budget
        return {"available": True, "cached": True, "question": q,
                "budget": pundits.budget_status(conn), **json.loads(row["payload"])}

    bs = pundits.budget_status(conn)
    blocked = pundits.limit_message(bs)
    if blocked:
        return {"available": False, "limited": True, "budget": bs,
                "message": blocked}

    preds = predict.predictions(conn)
    context = _context(conn, preds)

    try:
        text, usage = pundits._claude_cli(
            SYSTEM, f"Question: {q}\n\n{context}")
        pundits._log_call(conn, scope, usage)  # bill it the moment the call succeeds
        if text.startswith("```"):
            text = text.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0]
        try:
            data = _normalize_tree(json.loads(text), text)
        except json.JSONDecodeError:
            data = _normalize_tree(None, text)
    except Exception as exc:
        return {"available": False, "message": f"Scenario mapper error: {exc}"}

    conn.execute(
        "INSERT OR REPLACE INTO pundit_cache (scope, state_hash, payload, created_at) "
        "VALUES (?,?,?,?)",
        (scope, sh, json.dumps(data), datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return {"available": True, "cached": False, "question": q,
            "budget": pundits.budget_status(conn), **data}
