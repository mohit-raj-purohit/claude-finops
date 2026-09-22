"""Live model advice: what to switch to, while the session is still running.

The back-test in analytics.model_evidence() answers "what should I have used?",
which is the wrong tense once a session is under way. This module answers it in
the present: given the prompts a session has actually sent and the model it is
on, say whether your own history already shows a cheaper model doing this kind
of work without taking more turns.

It is built to run three ways, so the advice is the same wherever you meet it:

  * the dashboard's Running sessions view (per live session)
  * a UserPromptSubmit hook (per prompt, before the turn runs)
  * a statusline command (continuously, in a few characters)

The hook and statusline run on every prompt, so the evidence is computed once
and cached; a warehouse query per keystroke would be felt. Everything here fails
soft and silent — advice that breaks your terminal is worse than no advice.
"""
import json
import os
import time

from .classify import classify
from .paths import DATA_DIR, DB_PATH, ensure_dirs

CACHE = os.path.join(DATA_DIR, "advice_cache.json")
TTL_S = 3600
RECENT_PROMPTS = 12      # how much of the session counts as "what it is doing now"
MIN_SAVING_PCT = 25      # below this, interrupting someone is not worth it

# `/model <name>` takes a family name, not the pricing table's id.
ALIASES = ("opus", "sonnet", "haiku", "fable")


def model_alias(model_id):
    """'claude-fable-5-1' -> 'fable'. Falls back to the full id we were given."""
    low = str(model_id or "").lower()
    for a in ALIASES:
        if a in low:
            return a
    return model_id


# ---------------------------------------------------------------- evidence ----

def _build_evidence():
    """Pull the back-test into the small shape the live surfaces need."""
    from .analytics import Analytics
    a = Analytics(DB_PATH)
    ev = a.model_evidence({})
    out = {}
    for c in ev["categories"]:
        cands = [{"model": x["model"], "name": x["name"], "verdict": x["verdict"],
                  "cost_per_prompt": round(x["cost_per_prompt"], 3),
                  "savings_pct": x["savings_pct"], "turn_ratio": x["turn_ratio"],
                  "prompts": x["prompts"], "why": x["why"]}
                 for x in c["candidates"]]
        out[c["category"]] = {"current": c["current"]["model"],
                              "current_name": c["current"]["name"],
                              "current_cost_per_prompt": round(c["current"]["cost_per_prompt"], 3),
                              "agent": c.get("agent", "claude"),
                              "candidates": cands}
    return out


def evidence(force=False):
    """Cached evidence table, keyed by category. Never raises."""
    try:
        with open(CACHE) as fh:
            c = json.load(fh)
        if not force and time.time() - c.get("built_at", 0) < TTL_S:
            return c.get("categories") or {}
    except (OSError, ValueError):
        pass
    try:
        cats = _build_evidence()
    except Exception:
        return {}
    try:
        ensure_dirs()
        tmp = CACHE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump({"built_at": int(time.time()), "categories": cats}, fh)
        os.replace(tmp, CACHE)
    except OSError:
        pass
    return cats


# ---------------------------------------------------------------- session ----

def recent_prompts(transcript, n=RECENT_PROMPTS):
    """The last n human prompts in a live transcript, newest last.

    Reads the tail only: an active session's JSONL runs to tens of megabytes and
    this is on the path of every prompt you type.
    """
    try:
        size = os.path.getsize(transcript)
        with open(transcript, "rb") as fh:
            fh.seek(max(0, size - 400_000))
            lines = fh.read().decode("utf-8", "replace").splitlines()[1:]
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        if '"type":"user"' not in line and '"type": "user"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("isMeta") or d.get("isSidechain"):
            continue
        msg = d.get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):
            content = " ".join(b.get("text", "") for b in content
                               if isinstance(b, dict) and b.get("type") == "text")
        if not isinstance(content, str) or not content.strip():
            continue
        if content.lstrip().startswith(("<", "[Request interrupted")):
            continue          # tool results and interrupt markers are not prompts
        out.append(content)
        if len(out) >= n:
            break
    return list(reversed(out))


def category_of(texts):
    """The category this stretch of work is in, by weight of confidence."""
    scores = {}
    for t in texts:
        cat, conf, _ = classify(t)
        scores[cat] = scores.get(cat, 0) + max(conf, 0.1)
    if not scores:
        return "other", 0.0
    cat = max(scores, key=scores.get)
    return cat, round(scores[cat] / sum(scores.values()), 2)


def advise(model=None, transcript=None, prompt=None, ev=None):
    """What to say about a session that is running right now.

    model      the model the session is on (pricing-table id)
    transcript path to the live JSONL, for reading what it has been doing
    prompt     the prompt about to be sent, when we are called from a hook
    Returns None when there is nothing worth saying.
    """
    ev = evidence() if ev is None else ev
    if not ev:
        return None
    texts = list(recent_prompts(transcript)) if transcript else []
    if prompt:
        # The prompt in hand is what the next turn will cost, so it leads.
        texts = texts[-4:] + [prompt] * 3
    if not texts:
        return None
    cat, conf = category_of(texts)
    row = ev.get(cat)
    if not row:
        return None
    # Only speak when they are on the model the evidence is about. Advising a
    # switch away from a model you already left would be noise.
    if model and row["current"] and model_alias(model) != model_alias(row["current"]):
        return None
    usable = [c for c in row["candidates"]
              if c["verdict"] in ("supported", "caution") and c["savings_pct"] >= MIN_SAVING_PCT]
    if not usable:
        return None
    best = usable[0]
    trial = best["verdict"] == "caution"
    return {
        "category": cat, "confidence": conf,
        "current_model": row["current"], "current_name": row["current_name"],
        "current_cost_per_prompt": row["current_cost_per_prompt"],
        "model": best["model"], "name": best["name"], "alias": model_alias(best["model"]),
        "cost_per_prompt": best["cost_per_prompt"], "savings_pct": best["savings_pct"],
        "turn_ratio": best["turn_ratio"], "prompts": best["prompts"],
        "verdict": best["verdict"], "trial": trial, "why": best["why"],
        "command": f"/model {model_alias(best['model'])}",
        "line": (f"{cat.replace('_', ' ')} on {row['current_name']} "
                 f"(${row['current_cost_per_prompt']:.2f}/prompt). Your last {best['prompts']} "
                 f"ran on {best['name']} at ${best['cost_per_prompt']:.2f} in "
                 f"{best['turn_ratio']}x the turns"
                 + (" — worth a trial here." if trial else ".")),
        "short": f"try {model_alias(best['model'])} (-{best['savings_pct']:.0f}%)",
    }
