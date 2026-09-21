"""Billed numbers from the vendors' own APIs, next to what this machine recorded.

Everything else in this dashboard reads local files. This module is the one part that
talks to the internet, and only when you ask it to (the Refresh button / --cloud-sync).

Keys are never stored by the UI. Put them in the environment, or store one with
`claude-finops --set-key`, which writes ~/.claude-finops/secrets.local.json (0600,
outside the install tree, never packaged):

    {"anthropic_admin_key": "sk-ant-admin...", "cursor_api_key": "key_..."}

  Anthropic  Admin API key from Console → Settings → Admin keys. Org accounts only;
             individual accounts have no Admin API.
             /v1/organizations/usage_report/claude_code   per user per day, incl. Pro/Max
             /v1/organizations/cost_report                billed USD (API spend only)
  Cursor     Team/Org admin key from Cursor dashboard → Settings → Admin API.
             POST /teams/daily-usage-data, /teams/spend, GET /teams/members

Responses are cached in ~/.claude-finops/data/cloud_cache.json so the dashboard never calls out on its own.
"""
import base64
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
    "cursor": {
        "name": "Cursor", "agent": "cursor", "env": "CURSOR_API_KEY", "field": "cursor_api_key",
        "how": "Cursor dashboard → Settings → Admin API → create a key. Team/Business plans only; "
               "an individual Pro account has no admin API.",
        "covers": "Per-member daily activity and billed spend for the team.",
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


# --------------------------------------------------------------------- Cursor ----
def _cursor_headers(key):
    tok = base64.b64encode(f"{key}:".encode()).decode()      # basic auth, key as username
    return {"Authorization": f"Basic {tok}", "Content-Type": "application/json"}


def _cursor_post(key, path, body):
    try:
        return _get(f"https://api.cursor.com{path}", _cursor_headers(key),
                    data=json.dumps(body).encode(), method="POST")
    except RuntimeError as e:
        if "401" in str(e):                    # the key is fine; the account just isn't a team
            raise RuntimeError(
                "Cursor rejected the key for team endpoints (401 Invalid Team API Key). The key "
                "itself is valid, but /teams/* answers only for Team/Enterprise accounts — an "
                "individual account has no team data to report. Cursor stays local-only.") from None
        raise


def cursor_usage(key, days=30):
    """Daily per-member activity. The API allows at most 30 days per call."""
    end = int(time.time() * 1000)
    start = end - min(days, 30) * 86400000
    out, page = [], 1
    while True:
        d = _cursor_post(key, "/teams/daily-usage-data",
                         {"startDate": start, "endDate": end, "page": page, "pageSize": 1000})
        rows = d.get("data") or []
        for r in rows:
            ts = r.get("date")
            day = datetime.fromtimestamp(int(ts) / 1000, timezone.utc).date().isoformat() \
                if ts else None
            out.append({"day": day, "member": r.get("email") or r.get("userId") or "unknown",
                        "is_active": r.get("isActive"),
                        "lines_added": r.get("totalLinesAdded") or 0,
                        "lines_removed": r.get("totalLinesDeleted") or 0,
                        "accepted": r.get("acceptedLinesAdded") or 0,
                        "requests": r.get("composerRequests") or r.get("totalApplies") or 0,
                        "model": r.get("mostUsedModel")})
        if len(rows) < 1000 or page > 20:
            break
        page += 1
    return out


def cursor_spend(key):
    d = _cursor_post(key, "/teams/spend", {"page": 1, "pageSize": 500})
    out = []
    for r in d.get("teamMemberSpend") or d.get("data") or []:
        cents = r.get("spendCents")
        out.append({"member": r.get("email") or r.get("name") or "unknown",
                    "spend_usd": (float(cents) / 100.0) if cents is not None else None,
                    "requests": r.get("fastPremiumRequests") or r.get("requests"),
                    "role": r.get("role")})
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
    k = key_for("cursor")
    if k:
        log("Cursor: daily usage…")
        try:
            cache["cursor_usage"] = cursor_usage(k, days)
        except RuntimeError as e:
            errors["cursor_usage"] = str(e)
        log("Cursor: spend…")
        try:
            cache["cursor_spend"] = cursor_spend(k)
        except RuntimeError as e:
            errors["cursor_spend"] = str(e)
    cache["errors"] = errors
    cache["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w") as fh:
        json.dump(cache, fh)
    return cache


def report(analytics, days=30):
    """Billed (vendor API) next to local (this machine), per agent and per day."""
    c = load_cache()
    cc = c.get("anthropic_claude_code") or []
    cost = c.get("anthropic_cost") or []
    cu = c.get("cursor_usage") or []
    cs = c.get("cursor_spend") or []
    since = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
    local = {r["agent"]: r for r in analytics.q(
        "SELECT agent, SUM(est_cost_usd) cost, SUM(billable_tokens) tokens,"
        " COUNT(DISTINCT session_id) sessions, COUNT(*) requests"
        " FROM requests WHERE day >= ? GROUP BY agent", (since,))}

    by_day = {}
    for r in cc:
        d = by_day.setdefault(r["day"], {"day": r["day"], "billed_cost": 0.0, "billed_tokens": 0,
                                         "billed_sessions": 0})
        d["billed_cost"] += r["est_cost_usd"]
        d["billed_tokens"] += r["tokens"]
        d["billed_sessions"] += r["sessions"]
    for r in analytics.q("SELECT day, SUM(est_cost_usd) c, SUM(billable_tokens) t,"
                         " COUNT(DISTINCT session_id) s FROM requests"
                         " WHERE agent='claude' AND day >= ? GROUP BY day", (since,)):
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

    cur = {}
    for r in cu:
        m = cur.setdefault(r["member"], {"member": r["member"], "days": 0, "lines_added": 0,
                                         "lines_removed": 0, "accepted": 0, "requests": 0})
        m["days"] += 1 if r.get("is_active") is not False else 0
        for k2 in ("lines_added", "lines_removed", "accepted", "requests"):
            m[k2] += r.get(k2) or 0
    spend = {s["member"]: s for s in cs}
    for m in cur.values():
        m["spend_usd"] = (spend.get(m["member"]) or {}).get("spend_usd")

    claude_local = local.get("claude") or {}
    cursor_local = local.get("cursor") or {}
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
            "cursor_spend": sum(s["spend_usd"] or 0 for s in cs) if cs else None,
            "local_cursor_requests": cursor_local.get("requests") or 0,
            "org_users": len(users), "cursor_members": len(cur),
        },
        "by_day": sorted(by_day.values(), key=lambda x: x["day"]),
        "users": sorted(users.values(), key=lambda x: -x["cost"]),
        "cursor_members": sorted(cur.values(), key=lambda x: -(x["spend_usd"] or 0)),
        "api_cost_by_description": sorted(
            [{"description": k2, "cost_usd": v} for k2, v in
             {r["description"]: sum(x["cost_usd"] for x in cost if x["description"] == r["description"])
              for r in cost}.items()], key=lambda x: -x["cost_usd"])[:20],
        "note": "Billed figures come from the vendor APIs (org-wide, every machine and member). "
                "Local figures are what this machine's transcripts recorded. A gap usually means "
                "other machines, other members, or work outside this machine.",
        "basis": "billed",
    }
