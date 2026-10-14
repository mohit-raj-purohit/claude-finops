"""Billed numbers from Anthropic's Admin API, next to what this machine recorded.

Everything else in this dashboard reads local files. This module is the one part that
talks to the internet, and only when you ask it to (the Refresh button / --cloud-sync).

Keys come from the environment, or are stored with `claude-finops --set-key` or the
API keys section on the Budgets page. Both write ~/.claude-finops/secrets.local.json
(0600, outside the install tree, never packaged); the dashboard never sends a stored
key back to the browser, only whether it is set and its last four characters:

    {"anthropic_admin_key": "sk-ant-admin..."}

  Anthropic  Admin API key from Console → Settings → Admin keys. Org accounts only;
             individual accounts have no Admin API.
             /v1/organizations/usage_report/claude_code   per user per day, incl. Pro/Max
             /v1/organizations/cost_report                billed USD (API spend only)

Responses are cached in ~/.claude-finops/data/cloud_cache.json so the dashboard never calls out on its own.
"""
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from .paths import ROOT, SECRETS_PATH, CACHE_PATH
UA = "claude-finops/1.0 (local dashboard)"
TIMEOUT = 30

PROVIDERS = {
    "anthropic": {
        "name": "Anthropic (Claude)", "agent": "claude", "env": "ANTHROPIC_ADMIN_KEY",
        "field": "anthropic_admin_key",
        "how": "Console → Settings → Admin keys → Create Admin key (sk-ant-admin…). "
               "Requires an organization; individual accounts have no Admin API.",
        "covers": "Per-user Claude Code sessions, tokens and estimated cost (including Pro/Max "
                  "subscription users), plus billed API cost for the org.",
    },
}


def _secrets():
    try:
        with open(SECRETS_PATH) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def key_for(provider):
    p = PROVIDERS[provider]
    return os.environ.get(p["env"]) or _secrets().get(p["field"]) or None


def configured():
    return {k: bool(key_for(k)) for k in PROVIDERS}


# A light shape check, to catch pasting the wrong thing: not a validity test.
KEY_SHAPES = {
    "anthropic": (lambda v: v.startswith("sk-ant-admin"),
                  "An Anthropic Admin key starts with sk-ant-admin (a regular API key will not work)."),
}


def key_status():
    """Per provider: where the key comes from and its last four characters. Never the key."""
    stored = _secrets()
    out = {}
    for pid, p in PROVIDERS.items():
        env, val = os.environ.get(p["env"]), stored.get(p["field"])
        src = "env" if env else "stored" if val else None
        key = env or val or ""
        out[pid] = {"name": p["name"], "env": p["env"], "how": p["how"], "covers": p["covers"],
                    "source": src, "last4": key[-4:] if len(key) >= 12 else None}
    return out


def save_key(provider, value):
    """Store (value) or remove (empty value) one provider key in the 0600 secrets file.

    Raises ValueError for an unknown provider or a value that fails the shape check.
    """
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}")
    value = (value or "").strip()
    if value:
        if any(ch.isspace() for ch in value):
            raise ValueError("A key has no spaces or line breaks in it.")
        ok, msg = KEY_SHAPES.get(provider, (lambda v: True, ""))
        if not ok(value):
            raise ValueError(msg)
    data = _secrets()
    if value:
        data[PROVIDERS[provider]["field"]] = value
    else:
        data.pop(PROVIDERS[provider]["field"], None)
    os.makedirs(os.path.dirname(SECRETS_PATH), exist_ok=True)
    fd = os.open(SECRETS_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2)
    os.chmod(SECRETS_PATH, 0o600)      # O_CREAT's mode only applies to a new file


def _get(url, headers, data=None, method="GET"):
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"User-Agent": UA, **headers})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        body = (e.read() or b"")[:400].decode("utf-8", "replace")
        raise RuntimeError(f"{e.code} from {url.split('?')[0]}: {body}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach {url.split('?')[0]}: {e.reason}") from None


# ------------------------------------------------------------------ Anthropic ----
def _anthropic_headers(key):
    return {"x-api-key": key, "anthropic-version": "2023-06-01"}


def anthropic_claude_code(key, days=30, log=print):
    """One request per day (the endpoint reports a single UTC day), paged."""
    out, today = [], datetime.now(timezone.utc).date()
    for i in range(days, 0, -1):
        day = (today - timedelta(days=i)).isoformat()
        page = None
        while True:
            q = f"starting_at={day}&limit=1000" + (f"&page={page}" if page else "")
            d = _get(f"https://api.anthropic.com/v1/organizations/usage_report/claude_code?{q}",
                     _anthropic_headers(key))
            for rec in d.get("data") or []:
                actor = rec.get("actor") or {}
                cm = rec.get("core_metrics") or {}
                loc = cm.get("lines_of_code") or {}
                models = []
                cost = tokens = 0.0
                for m in rec.get("model_breakdown") or []:
                    t = m.get("tokens") or {}
                    n = sum(int(t.get(k) or 0) for k in
                            ("input", "output", "cache_read", "cache_creation"))
                    c = (m.get("estimated_cost") or {}).get("amount") or 0
                    cost += float(c) / 100.0            # cents -> USD
                    tokens += n
                    models.append({"model": m.get("model"), "tokens": n,
                                   "est_cost_usd": float(c) / 100.0})
                out.append({
                    "day": day,
                    "actor": actor.get("email_address") or actor.get("api_key_name") or "unknown",
                    "customer_type": rec.get("customer_type"), "terminal": rec.get("terminal_type"),
                    "sessions": cm.get("num_sessions") or 0,
                    "lines_added": loc.get("added") or 0, "lines_removed": loc.get("removed") or 0,
                    "commits": cm.get("commits_by_claude_code") or 0,
                    "prs": cm.get("pull_requests_by_claude_code") or 0,
                    "tokens": tokens, "est_cost_usd": cost, "models": models})
            if not d.get("has_more"):
                break
            page = d.get("next_page")
        log(f"  Anthropic Claude Code {day}: {len(out)} records so far")
    return out


def anthropic_cost(key, days=30):
    """Billed USD per day. Covers API spend only — subscription plans bill separately."""
    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days)
    iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
    out, page = [], None
    while True:
        q = f"starting_at={iso(start)}&ending_at={iso(end)}&group_by[]=description"
        d = _get(f"https://api.anthropic.com/v1/organizations/cost_report?{q}"
                 + (f"&page={page}" if page else ""), _anthropic_headers(key))
        for b in d.get("data") or []:
            for r in b.get("results") or []:
                amt = r.get("amount")
                try:
                    usd = float(amt) / 100.0            # decimal string, in cents
                except (TypeError, ValueError):
                    usd = 0.0
                out.append({"day": (b.get("starting_at") or "")[:10],
                            "description": r.get("description") or r.get("model") or "usage",
                            "model": r.get("model"), "cost_usd": usd})
        if not d.get("has_more"):
            break
        page = d.get("next_page")
    return out


# ---------------------------------------------------------------------- cache ----
def load_cache():
    try:
        with open(CACHE_PATH) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def sync(days=30, log=print):
    """Fetch what the configured keys allow, write the cache, return it."""
    cache = load_cache()
    cache["days"] = days
    errors = {}
    k = key_for("anthropic")
    if k:
        log("Anthropic: Claude Code analytics…")
        try:
            cache["anthropic_claude_code"] = anthropic_claude_code(k, days, log)
        except RuntimeError as e:
            errors["anthropic_claude_code"] = str(e)
        log("Anthropic: cost report…")
        try:
            cache["anthropic_cost"] = anthropic_cost(k, days)
        except RuntimeError as e:
            errors["anthropic_cost"] = str(e)
    cache["errors"] = errors
    cache["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w") as fh:
        json.dump(cache, fh)
    return cache


def report(analytics, days=30):
    """Billed (Anthropic API) next to local (this machine), per day and per user."""
    c = load_cache()
    cc = c.get("anthropic_claude_code") or []
    cost = c.get("anthropic_cost") or []
    since = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
    # The Anthropic API reports UTC days, so this page compares on UTC days too (every
    # other page uses your local calendar days); otherwise a late-evening session would
    # sit on different dates on the two sides.
    claude_local = analytics.one(
        "SELECT SUM(est_cost_usd) cost, SUM(billable_tokens) tokens,"
        " COUNT(DISTINCT session_id) sessions, COUNT(*) requests"
        " FROM requests WHERE agent='claude' AND substr(ts,1,10) >= ?", (since,))

    by_day = {}
    for r in cc:
        d = by_day.setdefault(r["day"], {"day": r["day"], "billed_cost": 0.0, "billed_tokens": 0,
                                         "billed_sessions": 0})
        d["billed_cost"] += r["est_cost_usd"]
        d["billed_tokens"] += r["tokens"]
        d["billed_sessions"] += r["sessions"]
    for r in analytics.q("SELECT substr(ts,1,10) day, SUM(est_cost_usd) c, SUM(billable_tokens) t,"
                         " COUNT(DISTINCT session_id) s FROM requests"
                         " WHERE agent='claude' AND substr(ts,1,10) >= ? GROUP BY 1", (since,)):
        d = by_day.setdefault(r["day"], {"day": r["day"], "billed_cost": 0.0, "billed_tokens": 0,
                                         "billed_sessions": 0})
        d.update(local_cost=r["c"] or 0.0, local_tokens=r["t"] or 0, local_sessions=r["s"] or 0)

    users = {}
    for r in cc:
        u = users.setdefault(r["actor"], {"actor": r["actor"], "cost": 0.0, "tokens": 0,
                                          "sessions": 0, "lines_added": 0, "lines_removed": 0,
                                          "commits": 0, "prs": 0, "customer_type": r["customer_type"],
                                          "terminals": set()})
        for k2, v in (("cost", "est_cost_usd"), ("tokens", "tokens"), ("sessions", "sessions"),
                      ("lines_added", "lines_added"), ("lines_removed", "lines_removed"),
                      ("commits", "commits"), ("prs", "prs")):
            u[k2] += r[v] or 0
        if r["terminal"]:
            u["terminals"].add(r["terminal"])
    for u in users.values():
        u["terminals"] = sorted(u["terminals"])

    return {
        "configured": configured(), "providers": PROVIDERS,
        "fetched_at": c.get("fetched_at"), "errors": c.get("errors") or {},
        "days": days,
        "totals": {
            "billed_claude_cost": sum(r["est_cost_usd"] for r in cc),
            "billed_claude_tokens": sum(r["tokens"] for r in cc),
            "local_claude_cost": claude_local.get("cost") or 0.0,
            "local_claude_tokens": claude_local.get("tokens") or 0,
            "billed_api_cost": sum(r["cost_usd"] for r in cost),
            "org_users": len(users),
        },
        "by_day": sorted(by_day.values(), key=lambda x: x["day"]),
        "users": sorted(users.values(), key=lambda x: -x["cost"]),
        "api_cost_by_description": sorted(
            [{"description": k2, "cost_usd": v} for k2, v in
             {r["description"]: sum(x["cost_usd"] for x in cost if x["description"] == r["description"])
              for r in cost}.items()], key=lambda x: -x["cost_usd"])[:20],
        "note": "Billed figures come from the Anthropic Admin API (org-wide, every machine and member). "
                "Local figures are what this machine's transcripts recorded. A gap usually means "
                "other machines, other members, or work outside this machine. Days on this "
                "page are UTC days, as Anthropic reports them.",
        "basis": "billed",
    }
