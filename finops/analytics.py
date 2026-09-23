"""FinOps analytics over the normalized warehouse.

Every number returned is tagged with a `basis`:
  actual      - read directly from the transcripts
  estimated   - derived from token counts x configurable pricing
  forecast    - projected from historical usage
  recommendation - suggested action, never a booked saving
"""
import json
import math
import os
import sqlite3
import statistics
import threading
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from .pricing import Pricing

from .paths import ROOT, DB_PATH, SETTINGS_PATH, LOCAL_SETTINGS_PATH

UNAVAILABLE = "Unavailable from connected Claude data"


def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


# claude_max -> "Max", claude_pro -> "Pro": the tier string carries a 5x/20x
# suffix we keep, because which Max you are on changes every limit in the app.
_PLANS = {"claude_max": "Max", "claude_pro": "Pro", "claude_team": "Team",
          "claude_enterprise": "Enterprise"}


def _plan(acct):
    base = _PLANS.get(acct.get("organizationType") or "")
    mult = ""
    tier = str(acct.get("organizationRateLimitTier") or "")
    if base == "Max":
        for m in ("5x", "20x"):
            if tier.endswith(m):
                mult = " " + m
    return (base + mult) if base else ""


def _version():
    """The version of the copy that is actually serving this page.

    Worth surfacing: a global npm install and a checkout look identical in the
    browser, and "why is my new feature missing" is almost always this.
    """
    try:
        from .update import _installed
        return _installed()
    except Exception:
        return ""


def detect_account():
    """Who Claude Code is signed in as, read from ~/.claude.json (actual, not guessed).

    Everything here is already on this machine, written by Claude Code itself at
    login. We only surface it, so a shared screenshot says whose numbers these
    are — a dashboard with no name on it is the one people misread.
    """
    try:
        with open(os.path.expanduser("~/.claude.json")) as fh:
            acct = json.load(fh).get("oauthAccount") or {}
    except (OSError, ValueError):
        return {}
    email = acct.get("emailAddress") or ""
    name = acct.get("fullName") or acct.get("displayName") or ""
    org = acct.get("organizationName") or ""
    out = {"name": name, "email": email,
           # A personal plan names the org after the person; repeating it is noise.
           "org": "" if org == name else org,
           "plan": _plan(acct)}
    if email:
        out["label"] = email
    return {k: v for k, v in out.items() if v}


def load_settings():
    """Shared defaults (settings.json) + this machine's overrides (settings.local.json)."""
    with open(SETTINGS_PATH) as fh:
        cur = json.load(fh)
    # Detected identity first, so a configured settings.json still wins below.
    detected = detect_account()
    acct = cur.setdefault("account", {})
    for k, v in detected.items():
        if not acct.get(k):
            acct[k] = v
    if os.path.exists(LOCAL_SETTINGS_PATH):
        with open(LOCAL_SETTINGS_PATH) as fh:
            _merge(cur, json.load(fh))
    return cur


def _d(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def _cumsum(values):
    total = 0.0
    for v in values:
        total += v or 0.0
        yield total


class Analytics:
    def __init__(self, db_path=DB_PATH):
        # One connection per thread. The HTTP server is threaded, and a single sqlite
        # connection shared across threads fails under concurrent use with "bad
        # parameter or other API misuse" — which is exactly what a page firing several
        # requests at once produces. Every connection is tracked so close() can release
        # them all before the warehouse file is swapped on sync.
        self._db_path = db_path
        self._local = threading.local()
        self._conns = []
        self._conns_lock = threading.Lock()
        self.pricing = Pricing()
        self.settings = load_settings()
        self.meta = {r["key"]: r["value"] for r in self.db.execute("SELECT * FROM meta")}
        row = self.db.execute("SELECT MIN(day) a, MAX(day) b FROM requests WHERE day<>''").fetchone()
        self.first_day, self.last_day = row["a"], row["b"]

    _today = None                      # tests set this; production uses the clock

    def today(self):
        return self._today or date.today()

    @property
    def db(self):
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self._db_path)
            c.row_factory = sqlite3.Row
            self._local.conn = c
            with self._conns_lock:
                self._conns.append(c)
        return c

    def close(self):
        """Close every thread's connection, so the warehouse file can be replaced."""
        with self._conns_lock:
            conns, self._conns = self._conns, []
        for c in conns:
            try:
                c.close()
            except Exception:
                pass
        self._local = threading.local()

    def q(self, sql, params=()):
        return [dict(r) for r in self.db.execute(sql, params)]

    def one(self, sql, params=()):
        r = self.db.execute(sql, params).fetchone()
        return dict(r) if r else {}

    # ---------------- filters ----------------
    def where(self, f):
        """Build a SQL WHERE fragment from the global filter object."""
        cl, p = [], []
        f = f or {}
        if f.get("start"):
            cl.append("r.day >= ?"); p.append(f["start"])
        if f.get("end"):
            cl.append("r.day <= ?"); p.append(f["end"])
        if f.get("agents"):
            cl.append("r.agent IN (%s)" % ",".join("?" * len(f["agents"]))); p += f["agents"]
        if f.get("models"):
            cl.append("r.model IN (%s)" % ",".join("?" * len(f["models"]))); p += f["models"]
        if f.get("projects"):
            cl.append("r.project_id IN (%s)" % ",".join("?" * len(f["projects"])))
            p += [int(x) for x in f["projects"]]
        if f.get("sessions"):
            cl.append("r.session_id IN (%s)" % ",".join("?" * len(f["sessions"]))); p += f["sessions"]
        if f.get("categories"):
            cl.append("r.prompt_id IN (SELECT id FROM prompts WHERE category IN (%s))"
                      % ",".join("?" * len(f["categories"]))); p += f["categories"]
        if not f.get("include_sandbox", True):
            cl.append("r.project_id IN (SELECT id FROM projects WHERE is_sandbox=0)")
        if f.get("min_cost") not in (None, ""):
            cl.append("r.est_cost_usd >= ?"); p.append(float(f["min_cost"]))
        if f.get("max_cost") not in (None, ""):
            cl.append("r.est_cost_usd <= ?"); p.append(float(f["max_cost"]))
        if f.get("min_tokens") not in (None, ""):
            cl.append("r.billable_tokens >= ?"); p.append(int(f["min_tokens"]))
        return (" AND ".join(cl) if cl else "1=1"), p

    def daily_series(self, f, days=None, end=None):
        """Per-day cost/token series with idle days present as zeros.

        A plain GROUP BY day only returns days you actually worked, so a mean taken
        over it is a per-ACTIVE-day rate. Every projection here multiplies that rate
        by calendar days remaining, so the zero days have to be filled in or the
        forecast is inflated by exactly the share of days you were idle.
        """
        w, p = self.where(f)
        rows = self.q(f"""SELECT r.day, SUM(r.est_cost_usd) cost, SUM(r.billable_tokens) tokens,
                          COUNT(*) requests FROM requests r WHERE {w} AND r.day <> ''
                          GROUP BY 1 ORDER BY 1""", p)
        if not rows:
            return []
        by_day = {r["day"]: r for r in rows}
        last = _d(end) if end else max(_d(rows[-1]["day"]), self.today())
        first = _d(rows[0]["day"])
        if days:
            first = max(first, last - timedelta(days=days - 1))
        out, cur = [], first
        while cur <= last:
            k = cur.isoformat()
            out.append(by_day.get(k) or {"day": k, "cost": 0.0, "tokens": 0, "requests": 0})
            cur += timedelta(days=1)
        return out

    # ---------------- billing period ----------------
    def billing_period(self, today=None):
        bp = self.settings["billing_period"]
        today = today or self.today()
        anchor = int(bp.get("anchor_day", 1))
        if today.day >= anchor:
            start = today.replace(day=min(anchor, 28))
        else:
            prev = today.replace(day=1) - timedelta(days=1)
            start = prev.replace(day=min(anchor, 28))
        nxt = (start.replace(day=28) + timedelta(days=8)).replace(day=min(anchor, 28))
        end = nxt - timedelta(days=1)
        total = (end - start).days + 1
        elapsed = (today - start).days + 1
        return {
            "start": start.isoformat(), "end": end.isoformat(), "today": today.isoformat(),
            "total_days": total, "elapsed_days": elapsed,
            "remaining_days": max(total - elapsed, 0),
            "pct_elapsed": round(100.0 * elapsed / total, 1),
            "basis": "actual",
        }

    # ---------------- overview ----------------
    def overview(self, f=None):
        w, p = self.where(f)
        tot = self.one(f"""
          SELECT COUNT(*) requests, COUNT(DISTINCT r.session_id) sessions,
                 COUNT(DISTINCT r.project_id) projects, COUNT(DISTINCT r.day) active_days,
                 COALESCE(SUM(r.input_tokens),0) input_tokens,
                 COALESCE(SUM(r.output_tokens),0) output_tokens,
                 COALESCE(SUM(r.thinking_tokens),0) thinking_tokens,
                 COALESCE(SUM(r.cache_read_tokens),0) cache_read_tokens,
                 COALESCE(SUM(r.cache_write_tokens),0) cache_write_tokens,
                 COALESCE(SUM(r.billable_tokens),0) billable_tokens,
                 COALESCE(SUM(r.est_cost_usd),0) est_cost_usd,
                 COALESCE(SUM(r.est_cost_no_cache_usd),0) est_cost_no_cache_usd,
                 AVG(r.latency_ms) avg_latency_ms,
                 COALESCE(MAX(r.context_tokens),0) max_context,
                 AVG(r.context_tokens) avg_context
          FROM requests r WHERE {w}""", p)
        tot["prompts"] = self.one(
            f"SELECT COUNT(DISTINCT r.prompt_id) n FROM requests r WHERE {w} AND r.prompt_id IS NOT NULL",
            p).get("n", 0)
        tot["tool_calls"] = self.one(
            f"SELECT COALESCE(SUM(r.tool_call_count),0) n FROM requests r WHERE {w}", p).get("n", 0)

        bp = self.billing_period()
        today = bp["today"]
        wk = (_d(today) - timedelta(days=6)).isoformat()

        def spend(extra, ep):
            return self.one(f"SELECT COALESCE(SUM(r.est_cost_usd),0) c,"
                            f" COALESCE(SUM(r.billable_tokens),0) t, COUNT(*) n"
                            f" FROM requests r WHERE {w} AND {extra}", p + ep)

        tot["cost_today"] = spend("r.day = ?", [today])
        tot["cost_week"] = spend("r.day >= ?", [wk])
        tot["cost_period"] = spend("r.day >= ? AND r.day <= ?", [bp["start"], bp["end"]])
        tot["billing_period"] = bp

        days = max(tot["active_days"] or 1, 1)
        tot["avg_cost_per_active_day"] = tot["est_cost_usd"] / days
        tot["avg_tokens_per_request"] = (tot["billable_tokens"] / tot["requests"]) if tot["requests"] else 0
        tot["cost_basis"] = "estimated"
        tot["date_range"] = {"first": self.first_day, "last": self.last_day}
        return tot

    # ---------------- burn rate & limits ----------------
    def burn(self, f=None):
        w, p = self.where(f)
        bp = self.billing_period()
        rows = self.q(f"""SELECT r.day, SUM(r.est_cost_usd) cost, SUM(r.billable_tokens) tokens,
                          COUNT(*) requests FROM requests r
                          WHERE {w} AND r.day >= ? AND r.day <= ? GROUP BY 1 ORDER BY 1""",
                      p + [bp["start"], bp["end"]])
        used_cost = sum(r["cost"] for r in rows)
        used_tokens = sum(r["tokens"] for r in rows)
        used_req = sum(r["requests"] for r in rows)
        elapsed = max(bp["elapsed_days"], 1)
        daily_avg = used_cost / elapsed

        # Trailing rates are measured over the last N CALENDAR days, not only the
        # slice inside the billing period — early in a period that slice is too short
        # to be a rate. Idle days count as zero, because these rates get multiplied by
        # calendar days remaining. This keeps burn and forecast on one methodology.
        yesterday = (self.today() - timedelta(days=1)).isoformat()
        last7 = self.daily_series(f, days=7, end=yesterday)
        last14 = self.daily_series(f, days=14, end=yesterday)
        avg7 = (sum(r["cost"] for r in last7) / len(last7)) if last7 else 0.0
        tok_avg7 = (sum(r["tokens"] for r in last7) / len(last7)) if last7 else 0.0
        # the projection rate matches Analytics.forecast()'s "expected" scenario
        burn = (sum(r["cost"] for r in last14) / len(last14)) if last14 else daily_avg
        tok_burn = (sum(r["tokens"] for r in last14) / len(last14)) if last14 else 0.0
        projected = used_cost + burn * bp["remaining_days"]
        tok_daily = used_tokens / elapsed

        lim = self.settings["limits"]
        out = {
            "period": bp,
            "used": {"cost_usd": used_cost, "tokens": used_tokens, "requests": used_req,
                     "basis": "estimated"},
            "daily_avg_cost": daily_avg, "avg7_cost": avg7,
            "daily_avg_tokens": tok_daily, "avg7_tokens": tok_avg7,
            "burn_rate_cost_per_day": burn,
            "burn_rate_window_days": len(last14),
            "projected_period_cost": projected,
            "projected_period_tokens": used_tokens + tok_burn * bp["remaining_days"],
            "forecast_basis": "forecast",
            "forecast_note": ("Projection uses the %d-calendar-day mean daily spend, idle days "
                              "included as zero — the same rate as the "
                              "Forecast view's expected scenario." % len(last14)),
            "series": rows,
            "allowances": {},
        }
        for key, used_val, rate, label in (
            ("monthly_cost_allowance_usd", used_cost, burn, "cost"),
            ("monthly_token_allowance", used_tokens, (tok_burn or tok_daily), "tokens"),
            ("monthly_request_allowance", used_req, used_req / elapsed, "requests"),
        ):
            allowance = lim.get(key)
            if not allowance:
                out["allowances"][label] = {"configured": False, "message": UNAVAILABLE}
                continue
            remaining = allowance - used_val
            pct = 100.0 * used_val / allowance
            days_left = (remaining / rate) if rate > 0 and remaining > 0 else None
            proj = used_val + rate * bp["remaining_days"]
            out["allowances"][label] = {
                "configured": True, "allowance": allowance, "used": used_val,
                "remaining": remaining, "used_pct": round(pct, 1),
                "remaining_pct": round(max(100 - pct, 0), 1),
                "days_until_limit": (round(days_left, 1) if days_left is not None else None),
                "limit_date": ((_d(bp["today"]) + timedelta(days=days_left)).isoformat()
                               if days_left is not None and days_left < 3650 else None),
                "exceeded": remaining <= 0,
                "projected_end_of_period": proj,
                "projected_overage_pct": round(100.0 * (proj - allowance) / allowance, 1),
                "status": self._status(pct),
                "basis": "estimated+forecast",
            }
        out["remaining_credits_usd"] = lim.get("remaining_credits_usd") or UNAVAILABLE
        return out

    @staticmethod
    def _status(pct):
        if pct >= 100: return "critical"
        if pct >= 90: return "approaching"
        if pct >= 75: return "high"
        return "healthy"

    # ---------------- timeline ----------------
    def timeline(self, f=None, grain="day"):
        w, p = self.where(f)
        col = "r.day" if grain == "day" else "substr(r.ts,1,13)"
        return self.q(f"""
          SELECT {col} bucket, COUNT(*) requests,
                 COUNT(DISTINCT r.session_id) sessions,
                 COUNT(DISTINCT r.prompt_id) prompts,
                 SUM(r.input_tokens) input_tokens, SUM(r.output_tokens) output_tokens,
                 SUM(r.cache_read_tokens) cache_read_tokens,
                 SUM(r.cache_write_tokens) cache_write_tokens,
                 SUM(r.billable_tokens) tokens, SUM(r.est_cost_usd) cost,
                 AVG(r.context_tokens) avg_context
          FROM requests r WHERE {w} AND r.day <> '' GROUP BY 1 ORDER BY 1""", p)

    # ---------------- models ----------------
    def models(self, f=None):
        w, p = self.where(f)
        rows = self.q(f"""
          SELECT r.model, r.model_known, COUNT(*) requests,
                 COUNT(DISTINCT r.session_id) sessions,
                 SUM(r.input_tokens) input_tokens, SUM(r.output_tokens) output_tokens,
                 SUM(r.thinking_tokens) thinking_tokens,
                 SUM(r.cache_read_tokens) cache_read_tokens,
                 SUM(r.cache_write_tokens) cache_write_tokens,
                 SUM(r.billable_tokens) tokens, SUM(r.est_cost_usd) cost,
                 AVG(r.latency_ms) avg_latency_ms, AVG(r.context_tokens) avg_context,
                 MAX(r.context_tokens) max_context
          FROM requests r WHERE {w} GROUP BY r.model ORDER BY cost DESC""", p)
        tc = sum(r["cost"] for r in rows) or 1
        tt = sum(r["tokens"] for r in rows) or 1
        # Utilisation is measured against the window that actually served each request —
        # a long-context variant has a bigger one — and requests that ran over a window
        # this price table cannot explain are counted, not averaged into a figure above
        # 100%, which is what dividing by the base model's window used to produce.
        util = {}
        for v in self.q(f"""SELECT r.model, r.priced_as, r.unpriced_long_context u,
                              COUNT(*) n, AVG(r.context_tokens) avg_ctx
                            FROM requests r WHERE {w}
                            GROUP BY r.model, r.priced_as, r.unpriced_long_context""", p):
            u = util.setdefault(v["model"], {"num": 0.0, "den": 0, "over": 0, "long": 0})
            win = self.pricing.context_window(v["priced_as"] or v["model"])
            if v["u"] or not win:
                u["over"] += v["n"]
                continue
            u["num"] += v["n"] * 100.0 * (v["avg_ctx"] or 0) / win
            u["den"] += v["n"]
            if v["priced_as"] and v["priced_as"] != v["model"]:
                u["long"] += v["n"]
        for r in rows:
            r["display_name"] = self.pricing.display_name(r["model"])
            r["tier"] = self.pricing.tier(r["model"])
            r["context_window"] = self.pricing.context_window(r["model"])
            r["pricing_known"] = bool(r["model_known"])
            r["cost_pct"] = round(100.0 * r["cost"] / tc, 1)
            r["token_pct"] = round(100.0 * r["tokens"] / tt, 1)
            r["cost_per_1k_output"] = (1000.0 * r["cost"] / r["output_tokens"]) if r["output_tokens"] else None
            r["output_per_input"] = (r["output_tokens"] / (r["input_tokens"] + r["cache_read_tokens"]
                                     + r["cache_write_tokens"])) if r["tokens"] else 0
            r["tokens_per_request"] = r["tokens"] / r["requests"] if r["requests"] else 0
            u = util.get(r["model"], {})
            r["utilization_pct"] = round(u["num"] / u["den"], 1) if u.get("den") else None
            r["over_window_requests"] = u.get("over", 0)
            r["long_context_requests"] = u.get("long", 0)
        priced = [r for r in rows if r["tokens"] and r["tier"] != "none"]
        superlatives = {}
        if priced:
            superlatives = {
                "most_expensive": max(priced, key=lambda r: r["cost"])["model"],
                "most_used": max(priced, key=lambda r: r["requests"])["model"],
                "most_token_efficient": max(priced, key=lambda r: r["output_per_input"])["model"],
                "best_cost_per_output": min(
                    [r for r in priced if r["cost_per_1k_output"]],
                    key=lambda r: r["cost_per_1k_output"], default={}).get("model"),
            }
        return {"rows": rows, "superlatives": superlatives, "basis": "estimated"}

    # ---------------- projects / sessions / prompts ----------------
    def projects(self, f=None):
        w, p = self.where(f)
        rows = self.q(f"""
          SELECT pr.id project_id, pr.name, pr.path, pr.slug, pr.is_sandbox,
                 COUNT(DISTINCT r.session_id) sessions,
                 COUNT(DISTINCT r.prompt_id) prompts, COUNT(*) requests,
                 SUM(r.billable_tokens) tokens, SUM(r.output_tokens) output_tokens,
                 SUM(r.cache_read_tokens) cache_read_tokens,
                 SUM(r.est_cost_usd) cost, GROUP_CONCAT(DISTINCT r.model) models
          FROM requests r JOIN projects pr ON pr.id = r.project_id
          WHERE {w} GROUP BY pr.id ORDER BY cost DESC""", p)
        for r in rows:
            r["avg_cost_per_session"] = r["cost"] / r["sessions"] if r["sessions"] else 0
            r["avg_cost_per_prompt"] = r["cost"] / r["prompts"] if r["prompts"] else None
            r["files_touched"] = self.one(
                "SELECT COUNT(DISTINCT path) n FROM files_touched WHERE project_id=?",
                (r["project_id"],))["n"]
            b = self.settings["budgets"].get("per_project_usd", {}).get(r["name"])
            r["budget_usd"] = b
            r["budget_used_pct"] = round(100.0 * r["cost"] / b, 1) if b else None
        return rows

    def sessions(self, f=None, limit=500, order="cost"):
        w, p = self.where(f)
        ob = {"cost": "cost DESC", "tokens": "tokens DESC", "duration": "s.duration_s DESC",
              "recent": "s.started_at DESC", "prompts": "prompts DESC"}.get(order, "cost DESC")
        rows = self.q(f"""
          SELECT s.id session_id, s.title, s.git_branch, s.cli_version, s.started_at,
                 s.ended_at, s.duration_s, s.files_touched, pr.name project, pr.id project_id,
                 COUNT(DISTINCT r.prompt_id) prompts, COUNT(*) requests,
                 SUM(r.tool_call_count) tool_calls,
                 SUM(r.input_tokens) input_tokens, SUM(r.output_tokens) output_tokens,
                 SUM(r.cache_read_tokens) cache_read_tokens,
                 SUM(r.cache_write_tokens) cache_write_tokens,
                 SUM(r.billable_tokens) tokens, SUM(r.est_cost_usd) cost,
                 MAX(r.context_tokens) max_context, AVG(r.context_tokens) avg_context,
                 GROUP_CONCAT(DISTINCT r.model) models
          FROM requests r JOIN sessions s ON s.id = r.session_id
          JOIN projects pr ON pr.id = s.project_id
          WHERE {w} GROUP BY s.id ORDER BY {ob} LIMIT ?""", p + [limit])
        for r in rows:
            r["cost_per_prompt"] = r["cost"] / r["prompts"] if r["prompts"] else None
            r["tokens_per_prompt"] = r["tokens"] / r["prompts"] if r["prompts"] else None
            r["tokens_per_request"] = r["tokens"] / r["requests"] if r["requests"] else 0
            r["output_ratio"] = (r["output_tokens"] or 0) / r["tokens"] if r["tokens"] else 0
            cr, cw = r["cache_read_tokens"] or 0, r["cache_write_tokens"] or 0
            r["cache_hit_ratio"] = (cr / (cr + cw)) if (cr + cw) else None
        return rows

    def prompts(self, f=None, limit=300, offset=0, order="cost", search=None):
        w, p = self.where(f)
        ob = {"cost": "pcost DESC", "tokens": "ptokens DESC", "recent": "pr.ts DESC",
              "cheapest": "pcost ASC", "efficiency": "efficiency DESC",
              "length": "pr.char_len DESC"}.get(order, "pcost DESC")
        extra, ep = "", []
        if search:
            extra = (" AND r.prompt_id IN (SELECT id FROM prompts pr WHERE pr.text LIKE ? "
                     "OR pr.session_id LIKE ? OR pr.category LIKE ?)")
            ep = [f"%{search}%"] * 3
        # Aggregate requests per prompt first, rank, and only then join the (large) prompt
        # text for the page being returned. Grouping with the text attached was ~2s a call.
        if order in ("recent", "length"):
            inner_ob, inner_lim, inner_p = "", "", []
        else:
            inner_ob, inner_lim, inner_p = f"ORDER BY {ob}", "LIMIT ? OFFSET ?", [limit, offset]
        rows = self.q(f"""
          WITH agg AS (
            SELECT r.prompt_id,
                   COUNT(r.id) requests, SUM(r.input_tokens) input_tokens,
                   SUM(r.output_tokens) output_tokens, SUM(r.cache_read_tokens) cache_read_tokens,
                   SUM(r.cache_write_tokens) cache_write_tokens,
                   SUM(r.billable_tokens) ptokens, SUM(r.est_cost_usd) pcost,
                   SUM(r.tool_call_count) tool_calls, AVG(r.latency_ms) latency_ms,
                   MAX(r.context_tokens) max_context,
                   GROUP_CONCAT(DISTINCT r.model) models,
                   (CAST(SUM(r.output_tokens) AS REAL) / MAX(SUM(r.billable_tokens),1)) efficiency
            FROM requests r WHERE {w}{extra} AND r.prompt_id IS NOT NULL
            GROUP BY r.prompt_id {inner_ob} {inner_lim})
          SELECT pr.id prompt_id, pr.uuid, pr.ts, pr.day, pr.text, pr.char_len, pr.word_len,
                 pr.category, pr.category_confidence, pr.category_evidence, pr.source,
                 pr.session_id, proj.name project, proj.id project_id, s.title session_title,
                 agg.requests, agg.input_tokens, agg.output_tokens, agg.cache_read_tokens,
                 agg.cache_write_tokens, agg.ptokens, agg.pcost, agg.tool_calls, agg.latency_ms,
                 agg.max_context, agg.models, agg.efficiency
          FROM agg JOIN prompts pr ON pr.id = agg.prompt_id
          JOIN projects proj ON proj.id = pr.project_id
          LEFT JOIN sessions s ON s.id = pr.session_id
          ORDER BY {ob} {"" if inner_lim else "LIMIT ? OFFSET ?"}""",
                      p + ep + inner_p + ([] if inner_lim else [limit, offset]))
        keep_text = bool((f or {}).get("_full_text"))
        for r in rows:
            r["preview"] = (r["text"] or "")[:220]
            if not keep_text:
                # the list endpoint ships previews only; /api/prompt/<id> carries full text
                del r["text"]
            r["cost_per_1k_output"] = (1000.0 * r["pcost"] / r["output_tokens"]
                                       if r["output_tokens"] else None)
            r["files_touched"] = self.one(
                "SELECT COUNT(DISTINCT path) n FROM files_touched WHERE prompt_id=?",
                (r["prompt_id"],))["n"]
            try:
                r["category_evidence"] = json.loads(r["category_evidence"] or "[]")
            except Exception:
                r["category_evidence"] = []
        return rows

    def prompt_detail(self, pid):
        p = self.one("""SELECT pr.*, proj.name project, s.title session_title, s.git_branch
                        FROM prompts pr JOIN projects proj ON proj.id=pr.project_id
                        LEFT JOIN sessions s ON s.id=pr.session_id WHERE pr.id=?""", (pid,))
        if not p:
            return {"error": "not found"}
        p["requests"] = self.q(
            "SELECT ts, model, effort, input_tokens, output_tokens, thinking_tokens,"
            " cache_read_tokens, cache_write_tokens, billable_tokens, context_tokens,"
            " est_cost_usd, latency_ms, stop_reason, tool_call_count"
            " FROM requests WHERE prompt_id=? ORDER BY ts", (pid,))
        # NB: keep the scalar prompts.tool_calls count intact — the log goes under its own key
        p["tool_call_log"] = self.q(
            "SELECT name, target, ts FROM tool_calls WHERE prompt_id=? ORDER BY ts", (pid,))
        p["tool_summary"] = self.q(
            "SELECT name, COUNT(*) n FROM tool_calls WHERE prompt_id=? GROUP BY 1 ORDER BY 2 DESC",
            (pid,))
        p["files"] = self.q(
            "SELECT path, op, COUNT(*) n FROM files_touched WHERE prompt_id=? GROUP BY path, op",
            (pid,))
        try:
            p["category_evidence"] = json.loads(p.get("category_evidence") or "[]")
        except Exception:
            p["category_evidence"] = []
        return p

    def session_detail(self, sid):
        s = self.one("""SELECT s.*, pr.name project FROM sessions s
                        JOIN projects pr ON pr.id=s.project_id WHERE s.id=?""", (sid,))
        if not s:
            return {"error": "not found"}
        s["prompts"] = self.q("""
          SELECT pr.id prompt_id, pr.ts, pr.category, substr(pr.text,1,220) preview,
                 pr.char_len, pr.billable_tokens ptokens, pr.est_cost_usd pcost,
                 pr.tool_calls, pr.models, pr.max_context_tokens max_context
          FROM prompts pr WHERE pr.session_id=? ORDER BY pr.ts""", (sid,))
        s["timeline"] = self.q(
            "SELECT ts, model, billable_tokens, context_tokens, output_tokens, est_cost_usd"
            " FROM requests WHERE session_id=? ORDER BY ts", (sid,))
        s["tools"] = self.q(
            "SELECT name, COUNT(*) n FROM tool_calls WHERE session_id=? GROUP BY 1 ORDER BY 2 DESC",
            (sid,))
        s["files"] = self.q(
            "SELECT path, GROUP_CONCAT(DISTINCT op) ops, COUNT(*) n FROM files_touched"
            " WHERE session_id=? GROUP BY path ORDER BY n DESC LIMIT 100", (sid,))
        return s

    # ---------------- categories ----------------
    def categories(self, f=None):
        w, p = self.where(f)
        rows = self.q(f"""
          SELECT pr.category, COUNT(DISTINCT pr.id) prompts, COUNT(r.id) requests,
                 SUM(r.billable_tokens) tokens, SUM(r.output_tokens) output_tokens,
                 SUM(r.est_cost_usd) cost, AVG(pr.category_confidence) confidence,
                 AVG(pr.char_len) avg_prompt_chars
          FROM prompts pr JOIN requests r ON r.prompt_id = pr.id
          WHERE {w} GROUP BY pr.category ORDER BY cost DESC""", p)
        tc = sum(r["cost"] for r in rows) or 1
        for r in rows:
            r["cost_pct"] = round(100.0 * r["cost"] / tc, 1)
            r["cost_per_prompt"] = r["cost"] / r["prompts"] if r["prompts"] else 0
        return {"rows": rows, "basis": "estimated",
                "note": "Categories are heuristic keyword classifications of your prompt text."}

    # ---------------- leaderboards ----------------
    def leaderboards(self, f=None, n=20):
        return {
            "most_expensive": self.prompts(f, limit=n, order="cost"),
            "most_token_heavy": self.prompts(f, limit=n, order="tokens"),
            "cheapest": self.prompts(f, limit=n, order="cheapest"),
            "most_efficient": self.prompts(f, limit=n, order="efficiency"),
            "longest_sessions": self.sessions(f, limit=n, order="duration"),
            "basis": "estimated",
        }

    # ---------------- efficiency ----------------
    def hygiene(self, f=None, top=12, trajectory_points=80):
        """Context and session hygiene: what it cost to keep re-sending a large prefix.

        Everything here is observed. For each session: the context size of every
        request in order, the request at which it first crossed each configured
        threshold, and what was spent from that point on. Across the range: the share
        of spend in requests above each threshold. No compaction is simulated and no
        saving is estimated — how much a fresh session would have saved depends on what
        the work still needed, which the transcript does not say.

        Subagent (sidechain) turns are excluded: they run against their own prefix, so
        mixing them into the parent session's trajectory would misstate both.
        """
        cfg = self.settings.get("hygiene", {})
        thresholds = sorted(int(x) for x in cfg.get("context_thresholds", [100000, 150000]))
        w, p = self.where(f)
        rows = self.q(f"""SELECT r.session_id, r.ts, r.context_tokens ctx, r.est_cost_usd cost
                          FROM requests r WHERE {w} AND r.is_sidechain = 0 AND r.ts <> ''
                          ORDER BY r.session_id, r.ts""", p)
        total = sum(r["cost"] or 0 for r in rows)
        above = {t: {"requests": 0, "cost_usd": 0.0, "sessions": 0, "cost_after_first_cross_usd": 0.0}
                 for t in thresholds}
        sessions = {}
        for r in rows:
            cost, ctx = (r["cost"] or 0.0), (r["ctx"] or 0)
            s = sessions.setdefault(r["session_id"], {
                "session_id": r["session_id"], "requests": 0, "cost_usd": 0.0,
                "max_context": 0, "first_cross": {t: None for t in thresholds},
                "cost_after": {t: 0.0 for t in thresholds}, "traj": []})
            idx = s["requests"]
            s["requests"] += 1
            s["cost_usd"] += cost
            s["max_context"] = max(s["max_context"], ctx)
            s["traj"].append((ctx, cost))
            for t in thresholds:
                if ctx >= t:
                    above[t]["requests"] += 1
                    above[t]["cost_usd"] += cost
                    if s["first_cross"][t] is None:
                        s["first_cross"][t] = idx
                if s["first_cross"][t] is not None:
                    s["cost_after"][t] += cost
        for s in sessions.values():
            for t in thresholds:
                if s["first_cross"][t] is not None:
                    above[t]["sessions"] += 1
                    above[t]["cost_after_first_cross_usd"] += s["cost_after"][t]

        rank_t = thresholds[-1]
        ranked = sorted(sessions.values(), key=lambda s: -s["cost_after"][rank_t])[:top]
        ids = [s["session_id"] for s in ranked]
        meta = {}
        if ids:
            ph = ",".join("?" * len(ids))
            meta = {m["id"]: m for m in self.q(f"""SELECT s.id, s.title, pj.name project
                                                FROM sessions s JOIN projects pj ON pj.id=s.project_id
                                                WHERE s.id IN ({ph})""", ids)}

        def downsample(traj):
            n = len(traj)
            if n <= trajectory_points:
                return traj
            step = n / trajectory_points
            return [traj[int(i * step)] for i in range(trajectory_points)]

        out_sessions = []
        for s in ranked:
            m = meta.get(s["session_id"], {})
            traj = downsample(s["traj"])
            out_sessions.append({
                "session_id": s["session_id"], "title": m.get("title"), "project": m.get("project"),
                "requests": s["requests"], "cost_usd": s["cost_usd"], "max_context": s["max_context"],
                "first_cross": {str(t): s["first_cross"][t] for t in thresholds},
                "cost_after": {str(t): s["cost_after"][t] for t in thresholds},
                "cost_after_pct": {str(t): (round(100.0 * s["cost_after"][t] / s["cost_usd"], 1)
                                            if s["cost_usd"] else 0.0) for t in thresholds},
                "context_trajectory": [c for c, _ in traj],
                "cumulative_cost": [round(x, 4) for x in _cumsum(cost for _, cost in traj)],
                "basis": "actual",
            })
        return {
            "thresholds": thresholds,
            "rank_threshold": rank_t,
            "requests": len(rows), "sessions": len(sessions), "cost_usd": total,
            "above": {str(t): {
                **v,
                "share_pct": round(100.0 * v["cost_usd"] / total, 1) if total else 0.0,
                "share_after_first_cross_pct": (round(100.0 * v["cost_after_first_cross_usd"] / total, 1)
                                                if total else 0.0),
            } for t, v in above.items()},
            "sessions_ranked": out_sessions,
            "excluded": "subagent turns (own prefix)",
            "undetectable": ["/clear", "/compact"],
            "note": ("Observed shares of spend. Nothing here estimates what compaction or a "
                     "fresh session would have saved. /clear and /compact are not recorded in "
                     "transcripts, so a compaction shows up only as the context dropping."),
            "basis": "actual",
        }

    def ttl_replay(self, f=None):
        """5m vs 1h cache TTL over real segments — arithmetic, no behavioural assumption.

        Gated on reconciliation: if replaying the TTL you actually used cannot reproduce
        the cost that was logged, the counterfactual is not trustworthy either and no
        number is returned.
        """
        from .segments import replay, split_segments
        w, p = self.where(f)
        turns = self.q(f"""SELECT r.session_id, r.ts, r.model, r.priced_as, r.is_sidechain, r.agent_id,
                             r.input_tokens, r.output_tokens, r.cache_read_tokens,
                             r.cache_write_5m, r.cache_write_1h, r.est_cost_usd
                           FROM requests r WHERE {w} AND r.agent='claude' AND r.ts <> ''
                           ORDER BY r.session_id, r.ts""", p)
        segs = split_segments(turns)
        out = replay(segs, self.pricing)
        out["turns"] = len(turns)
        out["undetectable_boundaries"] = ["/clear", "/compact"]
        out["note"] = ("Segments break at session start, subagent start and model change. "
                       "Claude Code does not record /clear or /compact, so a compaction "
                       "sits inside a segment and is not modelled.")
        return out

    def long_context_pricing(self, f=None):
        """Requests whose context exceeded the model's standard window.

        These could only have been served by the long-context variant, which bills at a
        premium. Where a `[1m]` price list exists they are already repriced; where it
        does not, they are billed at the standard rate and the estimate is LOW — that is
        a gap in config/pricing.json, not in the data, so it is reported rather than
        guessed at.
        """
        w, p = self.where(f)
        rows = self.q(f"""SELECT r.model, r.priced_as, r.unpriced_long_context u,
                            COUNT(*) n, SUM(r.est_cost_usd) cost, MAX(r.context_tokens) mx
                          FROM requests r WHERE {w} AND r.context_tokens > 0
                            AND (r.priced_as <> r.model OR r.unpriced_long_context = 1)
                          GROUP BY r.model, r.priced_as, r.unpriced_long_context""", p)
        repriced = [r for r in rows if not r["u"]]
        unpriced = [r for r in rows if r["u"]]
        return {
            "repriced": repriced,
            "unpriced": unpriced,
            "repriced_requests": sum(r["n"] for r in repriced),
            "unpriced_requests": sum(r["n"] for r in unpriced),
            "unpriced_cost_usd": sum(r["cost"] or 0 for r in unpriced),
            "unpriced_models": sorted({r["model"] for r in unpriced}),
            "message": (
                "%d requests exceeded their model's standard context window with no "
                "long-context price configured, so their cost is understated. Add a "
                "\"<model>[1m]\" entry to config/pricing.json for: %s."
                % (sum(r["n"] for r in unpriced), ", ".join(sorted({r["model"] for r in unpriced})))
                if unpriced else ""),
            "basis": "estimated",
        }

    def cache_cost_split(self, f=None):
        """Cache read vs write split by estimated cost, not just by token count.

        A read is billed at a fraction of the input rate and a write at a premium, so
        the token split and the dollar split are different numbers — writes are a small
        share of cache tokens and a much larger share of cache spend. Reporting only the
        token ratio overstates how healthy caching is, which is why the scorecard grades
        this dimension on cost. Prices are re-derived per model here rather than read off
        requests.est_cost_usd, which is a single blended figure per request.
        """
        w, p = self.where(f)
        rows = self.q(f"""SELECT model,
                            SUM(cache_read_tokens) cr,
                            SUM(cache_write_5m) w5, SUM(cache_write_1h) w1
                          FROM requests r WHERE {w} GROUP BY model""", p)
        read_tok = w5_tok = w1_tok = 0
        read_cost = w5_cost = w1_cost = 0.0
        for r in rows:
            cr, w5, w1 = (r["cr"] or 0), (r["w5"] or 0), (r["w1"] or 0)
            read_tok += cr
            w5_tok += w5
            w1_tok += w1
            read_cost += self.pricing.estimate(r["model"], cache_read=cr)
            w5_cost += self.pricing.estimate(r["model"], cache_write_5m=w5)
            w1_cost += self.pricing.estimate(r["model"], cache_write_1h=w1)

        write_tok = w5_tok + w1_tok
        write_cost = w5_cost + w1_cost
        tok_total = read_tok + write_tok
        cost_total = read_cost + write_cost
        per_read = (read_cost / read_tok) if read_tok else 0
        per_write = (write_cost / write_tok) if write_tok else 0
        return {
            "read_tokens": read_tok, "write_tokens": write_tok,
            "write_5m_tokens": w5_tok, "write_1h_tokens": w1_tok,
            "read_cost_usd": read_cost, "write_cost_usd": write_cost,
            "write_5m_cost_usd": w5_cost, "write_1h_cost_usd": w1_cost,
            "read_token_share": (read_tok / tok_total) if tok_total else None,
            "read_cost_share": (read_cost / cost_total) if cost_total else None,
            "write_cost_share": (write_cost / cost_total) if cost_total else None,
            # how much more a write token costs than a read token, same workload
            "write_vs_read_multiple": (per_write / per_read) if per_read else None,
            # 1h writes cost more per token than 5m writes; whether that premium is worth
            # paying depends on the real inter-turn gaps, which ttl_replay() prices
            "write_1h_token_share": (w1_tok / write_tok) if write_tok else None,
            "basis": "estimated",
        }

    def efficiency(self, f=None):
        w, p = self.where(f)
        t = self.one(f"""SELECT SUM(input_tokens) i, SUM(output_tokens) o,
                         SUM(cache_read_tokens) cr, SUM(cache_write_tokens) cw,
                         SUM(billable_tokens) tot, SUM(thinking_tokens) think,
                         COUNT(*) n, SUM(est_cost_usd) cost,
                         SUM(est_cost_no_cache_usd) cost_nc, AVG(context_tokens) avgctx
                         FROM requests r WHERE {w}""", p)
        tot = t["tot"] or 1
        prompt_side = (t["i"] or 0) + (t["cr"] or 0) + (t["cw"] or 0)
        cache_total = (t["cr"] or 0) + (t["cw"] or 0)
        cache_cost = self.cache_cost_split(f)
        sess = self.sessions(f, limit=100000, order="cost")
        scored = [s for s in sess if s["tokens"] and s["prompts"]]
        for s in scored:
            s["efficiency_score"] = round(100.0 * s["output_ratio"] * 10, 1)
        scored.sort(key=lambda s: s["output_ratio"])
        return {
            "output_ratio": (t["o"] or 0) / tot,
            "output_per_input": ((t["o"] or 0) / prompt_side) if prompt_side else 0,
            "thinking_share_of_output": ((t["think"] or 0) / (t["o"] or 1)),
            "cache_hit_ratio": ((t["cr"] or 0) / cache_total) if cache_total else None,
            "cache_read_cost_share": cache_cost["read_cost_share"],
            "tokens_per_request": tot / (t["n"] or 1),
            "avg_context_tokens": t["avgctx"],
            "cost_per_1k_output": (1000.0 * (t["cost"] or 0) / (t["o"] or 1)),
            "cost_per_prompt": ((t["cost"] or 0) /
                                max(self.one(f"SELECT COUNT(DISTINCT r.prompt_id) n FROM requests r WHERE {w}", p)["n"], 1)),
            "cache": {
                "reads": t["cr"], "writes": t["cw"],
                "cost_split": cache_cost,
                "cost_with_cache": t["cost"], "cost_without_cache": t["cost_nc"],
                # Named for what it is. This used to be "estimated_savings_usd", and the
                # UI called it a saving; it is the gap to a run that never happened.
                "uncached_counterfactual_delta_usd": (t["cost_nc"] or 0) - (t["cost"] or 0),
                "uncached_counterfactual_pct": (round(100.0 * ((t["cost_nc"] or 0) - (t["cost"] or 0))
                                                      / (t["cost_nc"] or 1), 1)),
                "basis": "estimated",
            },
            "low_efficiency_sessions": scored[:10],
            "high_efficiency_sessions": scored[-10:][::-1],
            "basis": "estimated",
        }

    def context_analysis(self, f=None):
        w, p = self.where(f)
        buckets = self.q(f"""
          SELECT CASE
            WHEN r.context_tokens < 25000 THEN '0-25K'
            WHEN r.context_tokens < 50000 THEN '25-50K'
            WHEN r.context_tokens < 100000 THEN '50-100K'
            WHEN r.context_tokens < 150000 THEN '100-150K'
            WHEN r.context_tokens < 200000 THEN '150-200K'
            ELSE '200K+' END bucket,
            COUNT(*) requests, SUM(r.billable_tokens) tokens, SUM(r.est_cost_usd) cost
          FROM requests r WHERE {w} GROUP BY 1""", p)
        order = ['0-25K', '25-50K', '50-100K', '100-150K', '150-200K', '200K+']
        buckets.sort(key=lambda b: order.index(b["bucket"]) if b["bucket"] in order else 99)
        total_cost = sum(b["cost"] for b in buckets) or 1
        for b in buckets:
            b["cost_pct"] = round(100.0 * b["cost"] / total_cost, 1)
        thr = self.settings["waste_rules"]["large_context_tokens"]
        big = self.one(f"SELECT COUNT(*) n, COALESCE(SUM(r.est_cost_usd),0) c FROM requests r"
                       f" WHERE {w} AND r.context_tokens >= ?", p + [thr])
        heavy_sessions = self.q(f"""
          SELECT s.id session_id, s.title, pr.name project, MAX(r.context_tokens) max_context,
                 AVG(r.context_tokens) avg_context, COUNT(*) requests, SUM(r.est_cost_usd) cost
          FROM requests r JOIN sessions s ON s.id=r.session_id
          JOIN projects pr ON pr.id=s.project_id
          WHERE {w} GROUP BY s.id HAVING MAX(r.context_tokens) >= ?
          ORDER BY cost DESC LIMIT 20""", p + [thr])
        agg = self.one(f"SELECT AVG(r.context_tokens) a, MAX(r.context_tokens) m FROM requests r WHERE {w}", p)
        windows = [v.get("context_window") for v in self.pricing.models.values() if v.get("context_window")]
        return {
            "buckets": buckets, "avg_context": agg["a"], "max_context": agg["m"],
            "threshold": thr,
            "large_context_requests": big["n"], "large_context_cost": big["c"],
            "large_context_cost_pct": round(100.0 * big["c"] / total_cost, 1),
            "heavy_sessions": heavy_sessions,
            "typical_context_window": max(windows) if windows else None,
            "basis": "actual token counts, estimated cost",
        }

    # ---------------- waste ----------------
    def waste(self, f=None):
        """Waste rules over the pre-rolled prompt/session tables.

        Two different numbers per finding, and the distinction matters:

        * ``est_cost_usd`` — the *exposed* spend: what the flagged items cost in
          total. It is the money worth reviewing, not the money wasted.
        * ``est_excess_usd`` — the *estimated excess*: how much more the flagged
          items cost than a reasonable baseline for the same work. This is
          bounded per rule and is the honest "waste" figure.

        Attributing a flagged session's whole cost to waste would be wrong — those
        sessions did real work — and on a skewed spend distribution it saturates at
        ~100%, which makes it useless for grading. The excess estimate does not.
        """
        w, p = self.where(f)
        rules = self.settings["waste_rules"]
        pfilter = f"pr.id IN (SELECT DISTINCT r.prompt_id FROM requests r WHERE {w})"
        sfilter = f"s.id IN (SELECT DISTINCT r.session_id FROM requests r WHERE {w})"
        findings = []

        def add(sev, kind, title, detail, evidence, action, key, excess_basis):
            cost = sum(e.get("cost") or 0 for e in evidence)
            excess = sum(min(max(e.get("excess") or 0, 0), e.get("cost") or 0) for e in evidence)
            findings.append({
                "severity": sev, "kind": kind, "title": title, "detail": detail,
                "est_cost_usd": cost, "est_excess_usd": excess,
                "excess_basis": excess_basis, "evidence": evidence,
                "affected": {key: [e[key] for e in evidence if e.get(key)]},
                "recommended_action": action, "basis": "estimated"})

        # 1. very long prompts — excess is the share of spend attributable to
        #    re-sending the oversized prompt text on every turn of the same request.
        rows = self.q(f"""SELECT pr.id prompt_id, pr.char_len, substr(pr.text,1,160) preview,
                          pr.session_id, pr.est_cost_usd cost, pr.billable_tokens tokens,
                          pr.request_count requests
                          FROM prompts pr WHERE {pfilter} AND pr.char_len >= ?
                          ORDER BY cost DESC LIMIT 15""", p + [rules["long_prompt_chars"]])
        budget_chars = rules["long_prompt_chars"]
        for r in rows:
            r["excess"] = 0.0
        if rows:
            add("high", "long_prompts",
                f"{len(rows)} very long prompts (>{budget_chars:,} chars)",
                "Long pasted prompts inflate the cached prefix re-sent on every following turn.",
                rows, "Move large pasted context into a file and reference it, or summarize first.",
                "prompt_id",
                "none claimed — flagged for review only")

        # 2. duplicate prompts — excess is the cost of the repeats, not the first ask,
        #    counted only within a single session so cross-session coincidences don't count.
        dups = self.q(f"""SELECT pr.norm_hash, pr.session_id, COUNT(*) n, substr(MIN(pr.text),1,160) preview,
                          SUM(pr.est_cost_usd) cost, SUM(pr.billable_tokens) tokens,
                          MIN(pr.est_cost_usd) first_cost, GROUP_CONCAT(pr.id) prompt_ids
                          FROM prompts pr WHERE {pfilter} AND pr.char_len > 25
                          GROUP BY pr.norm_hash, pr.session_id HAVING n > 1
                          ORDER BY cost DESC LIMIT 15""", p)
        for d in dups:
            d["prompt_id"] = int(d["prompt_ids"].split(",")[0])
            d["affected_ids"] = [int(x) for x in d["prompt_ids"].split(",")]
            d["excess"] = max(d["cost"] - d["first_cost"], 0)   # repeats only
        if dups:
            add("high", "duplicate_prompts", f"{len(dups)} prompts repeated more than once",
                "The same request was sent again, re-paying for context each time.",
                dups, "Reuse the earlier answer, or capture the recurring request as a slash command.",
                "prompt_id", "cost of the repeat occurrences, excluding the first ask")

        # 3. low-yield sessions — excess is what the session cost ABOVE what the same
        #    output would have cost at your own median session efficiency.
        #    The baseline is drawn from the SAME population that is eligible to be
        #    flagged — sessions above huge_session_tokens, inside the current filter.
        #    Grading big sessions against the median of all sessions punishes them for
        #    something inherent to long agentic work: output ratio falls as a session
        #    grows, so a small-session median flags most large sessions by construction.
        ratios = [r["x"] for r in self.q(
            f"""SELECT CAST(s.output_tokens AS REAL)/s.billable_tokens x FROM sessions s
                WHERE {sfilter} AND s.billable_tokens > ?""",
            p + [rules["huge_session_tokens"]])]
        median_ratio = statistics.median(ratios) if ratios else 0.0
        cutoff = median_ratio * rules["low_output_ratio_vs_median"]
        baseline_n = len(ratios)
        low = self.q(f"""SELECT s.id session_id, s.title, s.billable_tokens tokens,
                         s.output_tokens out_tokens, s.est_cost_usd cost, s.request_count requests,
                         (CAST(s.output_tokens AS REAL)/MAX(s.billable_tokens,1)) output_ratio,
                         proj.name project FROM sessions s JOIN projects proj ON proj.id=s.project_id
                         WHERE {sfilter} AND s.billable_tokens > ?
                           AND (CAST(s.output_tokens AS REAL)/MAX(s.billable_tokens,1)) < ?
                         ORDER BY cost DESC LIMIT 15""",
                     p + [rules["huge_session_tokens"], cutoff])
        for r in low:
            r["excess"] = 0.0
        if low:
            add("medium", "low_yield_sessions",
                f"{len(low)} large sessions yielded under {cutoff*100:.2f}% output tokens",
                f"Among your {baseline_n} comparably large sessions the median turns "
                f"{median_ratio*100:.2f}% of billable tokens into output. These ran well below "
                f"that while consuming heavy context.",
                low, "Start a fresh session or /compact once a thread stops producing new output.",
                "session_id", "none claimed — the same output at another ratio is a counterfactual")

        # 4. frontier model on small tasks — excess is computed against the cheaper tier.
        frontier = [m for m, v in self.pricing.models.items()
                    if v.get("tier") in rules["frontier_tiers"]]
        tiers = defaultdict(list)
        for m, v in self.pricing.models.items():
            tiers[v.get("tier")].append(m)
        cheaper = None
        for t in ("balanced", "economy"):
            if tiers.get(t):
                cheaper = min(tiers[t], key=lambda m: self.pricing.rates(m).get("output", 1e9))
                break
        if frontier:
            ph = ",".join("?" * len(frontier))
            small = self.q(f"""SELECT pr.id prompt_id, substr(pr.text,1,160) preview, pr.category,
                               pr.session_id, pr.est_cost_usd cost, pr.output_tokens out_tokens,
                               pr.billable_tokens tokens, pr.models, pr.input_tokens,
                               pr.cache_read_tokens, pr.cache_write_tokens,
                               (SELECT COALESCE(SUM(rq.cache_write_5m),0) FROM requests rq
                                  WHERE rq.prompt_id = pr.id) c5,
                               (SELECT COALESCE(SUM(rq.cache_write_1h),0) FROM requests rq
                                  WHERE rq.prompt_id = pr.id) c1
                               FROM prompts pr WHERE {pfilter}
                                 AND pr.output_tokens < ? AND pr.tool_calls = 0 AND pr.est_cost_usd > 0
                                 AND EXISTS (SELECT 1 FROM requests r2 WHERE r2.prompt_id=pr.id
                                             AND r2.model IN ({ph}))
                               ORDER BY cost DESC LIMIT 15""",
                           p + [rules["simple_task_output_tokens"]] + frontier)
            for r in small:
                r["excess"] = 0.0
            if small:
                add("medium", "frontier_on_small_tasks",
                    f"{len(small)} frontier-model prompts produced under "
                    f"{rules['simple_task_output_tokens']} output tokens",
                    "Short, simple turns running on the most expensive model tier.",
                    small, "Route short lookups and confirmations to a cheaper model tier.",
                    "prompt_id", "none claimed")

        # 5. tool loops — excess is the share of the loop beyond the threshold.
        loop_calls = rules.get("tool_loop_calls", 40)
        loops = self.q(f"""SELECT pr.id prompt_id, substr(pr.text,1,160) preview, pr.session_id,
                           pr.tool_calls tools, pr.est_cost_usd cost, pr.billable_tokens tokens
                           FROM prompts pr WHERE {pfilter} AND pr.tool_calls > ?
                           ORDER BY cost DESC LIMIT 15""", p + [loop_calls])
        for r in loops:
            r["excess"] = 0.0
        if loops:
            add("medium", "tool_loops", f"{len(loops)} prompts triggered {loop_calls}+ tool calls",
                "Long agentic loops re-send the whole conversation each step, so cost grows super-linearly.",
                loops, "Split the task, or give more precise instructions up front.", "prompt_id",
                "none claimed")

        # 6. poor cache reuse — excess is the break-even: the cache-write premium over
        #    plain input pricing, minus the discount actually earned on the reads.
        poor = self.q(f"""SELECT s.id session_id, s.title, proj.name project,
                          s.cache_read_tokens reads, s.cache_write_tokens writes,
                          (SELECT COALESCE(SUM(r.cache_write_5m),0) FROM requests r WHERE r.session_id=s.id) w5,
                          (SELECT COALESCE(SUM(r.cache_write_1h),0) FROM requests r WHERE r.session_id=s.id) w1,
                          s.est_cost_usd cost, s.request_count requests, s.models
                          FROM sessions s JOIN projects proj ON proj.id=s.project_id
                          WHERE {sfilter} AND s.cache_write_tokens > ?
                          ORDER BY cost DESC""", p + [rules.get("poor_cache_min_writes", 500_000)])
        flagged = []
        for r in poor:
            m = (r["models"] or "").split(",")[0]
            rt = self.pricing.rates(m)
            g = lambda k: float(rt.get(k, 0.0))
            # break-even: premium paid on writes minus discount earned on reads
            net = (r["w5"] * (g("cache_write_5m") - g("input"))
                   + r["w1"] * (g("cache_write_1h") - g("input"))
                   - r["reads"] * (g("input") - g("cache_read"))) / 1_000_000.0
            if net > 0:
                r["excess"] = net
                flagged.append(r)
        poor = flagged[:15]
        if poor:
            add("medium", "poor_cache_reuse", f"{len(poor)} sessions wrote cache they barely reused",
                "Cache writes cost more than plain input; they only pay off when read back repeatedly.",
                poor, "Keep related work in one continuous session so the cached prefix is reused.",
                "session_id", "cache-write premium minus the read discount actually earned, "
                              "at this model's rates")

        # 7. long-lived sparse sessions — informational, no excess claimed.
        idle = self.q(f"""SELECT s.id session_id, s.title, proj.name project, s.duration_s,
                          s.request_count requests, s.est_cost_usd cost
                          FROM sessions s JOIN projects proj ON proj.id=s.project_id
                          WHERE {sfilter} AND s.duration_s > ? AND s.request_count < 30
                          ORDER BY s.duration_s DESC LIMIT 10""",
                     p + [rules["idle_gap_minutes"] * 60 * 4])
        for r in idle:
            r["excess"] = 0.0
        if idle:
            add("low", "stale_sessions", f"{len(idle)} long-lived sessions with sparse activity",
                "Resuming a stale session re-sends aged context that is often no longer relevant.",
                idle, "Start a fresh session for a new task rather than resuming an old one.",
                "session_id", "none claimed — flagged for review only")

        findings.sort(key=lambda x: ({"high": 0, "medium": 1, "low": 2}[x["severity"]],
                                     -x["est_excess_usd"]))

        pids, sids = set(), set()
        for fnd in findings:
            pids.update(fnd["affected"].get("prompt_id", []))
            sids.update(fnd["affected"].get("session_id", []))
            for e in fnd["evidence"]:
                pids.update(e.get("affected_ids", []))
        exposed = 0.0
        if pids:
            exposed += self.one("SELECT COALESCE(SUM(est_cost_usd),0) c FROM prompts WHERE id IN (%s)"
                                % ",".join("?" * len(pids)), tuple(pids))["c"]
        if sids:
            exposed += self.one(
                "SELECT COALESCE(SUM(est_cost_usd),0) c FROM requests WHERE session_id IN (%s)"
                % ",".join("?" * len(sids)) +
                (" AND (prompt_id IS NULL OR prompt_id NOT IN (%s))" % ",".join("?" * len(pids))
                 if pids else ""),
                tuple(sids) + tuple(pids))["c"]

        total = self.one(f"SELECT COALESCE(SUM(r.est_cost_usd),0) c FROM requests r WHERE {w}", p)["c"]
        exposed = min(exposed, total)

        # De-duplicate excess by entity rather than summing across rules: an item caught
        # by two rules is one item. Per entity take the largest excess any rule claimed,
        # then drop prompt-level excess for prompts that sit inside an already-counted
        # session, since the session estimate already covers them.
        p_excess, s_excess = {}, {}
        for fnd in findings:
            for e in fnd["evidence"]:
                ex = min(max(e.get("excess") or 0, 0), e.get("cost") or 0)
                if e.get("prompt_id"):
                    for pid_ in (e.get("affected_ids") or [e["prompt_id"]]):
                        p_excess[pid_] = max(p_excess.get(pid_, 0.0), ex / max(
                            len(e.get("affected_ids") or [1]), 1))
                elif e.get("session_id"):
                    s_excess[e["session_id"]] = max(s_excess.get(e["session_id"], 0.0), ex)
        counted_sessions = set(s_excess)
        outside = 0.0
        if p_excess:
            owner = {r["id"]: r["session_id"] for r in self.q(
                "SELECT id, session_id FROM prompts WHERE id IN (%s)"
                % ",".join("?" * len(p_excess)), tuple(p_excess))}
            outside = sum(v for pid_, v in p_excess.items()
                          if owner.get(pid_) not in counted_sessions)
        excess = min(sum(s_excess.values()) + outside, exposed)
        return {"findings": findings,
                "estimated_excess_usd": excess,
                "excess_pct": round(100.0 * excess / total, 1) if total else 0,
                "exposed_cost_usd": exposed,
                "exposed_pct": round(100.0 * exposed / total, 1) if total else 0,
                # kept for compatibility with older callers
                "flagged_cost_usd": exposed,
                "flagged_pct": round(100.0 * exposed / total, 1) if total else 0,
                "total_cost_usd": total,
                "affected_prompts": len(pids), "affected_sessions": len(sids),
                "note": "Exposed spend is the de-duplicated total cost of everything a rule "
                        "touched — money worth reviewing. Estimated excess is how much more that "
                        "work cost than a reasonable baseline, and is the actual waste figure. "
                        "Both are estimates.",
                "excess_note": "Estimated excess is claimed only where the baseline is measured: the cost of "
                               "repeating an identical prompt in the same session, and cache writes that were "
                               "never read back enough to pay for themselves. Everything else is exposed spend "
                               "to review, not waste.",
                "basis": "estimated"}

    # ---------------- recommendations ----------------
    # How close to the ceiling counts as "near". 90% was the cut the old reprice used
    # to decide a request could not move to a smaller window; it is kept as the
    # observation threshold because that is where re-read cost visibly concentrates.
    NEAR_WINDOW_PCT = 0.9

    def context_window_fit(self, f=None):
        """How much spend ran near the ceiling of the context window actually in use.

        This is the one piece of the old model-switch reprice worth keeping — the
        window check — turned from a what-if into an observation. Nothing is
        repriced and no alternative model is assumed. A request is "near" when its
        prompt side is at least NEAR_WINDOW_PCT of its model's window, and "over"
        when it exceeds it, which can only mean the long-context variant served it.
        """
        w, p = self.where(f)
        rows = self.q(f"""SELECT r.model, r.priced_as, r.context_tokens ctx, r.est_cost_usd cost
                          FROM requests r WHERE {w}""", p)
        per = {}
        unknown = {"requests": 0, "cost_usd": 0.0}
        total_n = total_cost = near_cost = over_cost = 0.0
        near_n = over_n = 0
        for r in rows:
            cost = r["cost"] or 0.0
            total_n += 1
            total_cost += cost
            win = self.pricing.context_window(r["priced_as"] or r["model"])
            if not win:
                unknown["requests"] += 1
                unknown["cost_usd"] += cost
                continue
            m = per.setdefault(r["model"], {
                "model": r["model"], "display_name": self.pricing.display_name(r["model"]),
                "context_window": win, "requests": 0, "cost_usd": 0.0,
                "near_requests": 0, "near_cost_usd": 0.0,
                "over_requests": 0, "over_cost_usd": 0.0})
            m["requests"] += 1
            m["cost_usd"] += cost
            ctx = r["ctx"] or 0
            if ctx > win:
                m["over_requests"] += 1; m["over_cost_usd"] += cost
                over_n += 1; over_cost += cost
            elif ctx >= win * self.NEAR_WINDOW_PCT:
                m["near_requests"] += 1; m["near_cost_usd"] += cost
                near_n += 1; near_cost += cost
        out = sorted(per.values(), key=lambda m: -(m["near_cost_usd"] + m["over_cost_usd"]))
        for m in out:
            m["near_or_over_cost_pct"] = (round(100.0 * (m["near_cost_usd"] + m["over_cost_usd"])
                                                / m["cost_usd"], 1) if m["cost_usd"] else 0.0)
        return {
            "threshold_pct": int(self.NEAR_WINDOW_PCT * 100),
            "models": out,
            "requests": int(total_n), "cost_usd": total_cost,
            "near_requests": near_n, "near_cost_usd": near_cost,
            "over_requests": over_n, "over_cost_usd": over_cost,
            "near_or_over_cost_pct": (round(100.0 * (near_cost + over_cost) / total_cost, 1)
                                      if total_cost else 0.0),
            "unknown_window": unknown,
            "basis": "actual",
        }

    def recommendations(self, f=None):
        w, p = self.where(f)
        recs = []


        # What follows are observations, not priced savings. The cache item used to
        # carry the no-cache counterfactual as a "saving" (tens of thousands of dollars
        # on a bill a fraction of that) and the context item multiplied its spend by a
        # guessed 20%. Neither number was something the method could support, so
        # neither is shown; what is observable is.
        eff = self.efficiency(f)
        c = eff["cache"]
        split = c.get("cost_split") or {}
        if c["reads"] and split.get("read_cost_share"):
            recs.append({
                "type": "cache_working", "confidence": "observed",
                "title": "Prompt caching is doing its job — keep sessions long-lived",
                "detail": (f"Reads are {split['read_cost_share']*100:.0f}% of your cache cost "
                           f"({eff['cache_hit_ratio']*100:.0f}% of cache tokens). Restarting "
                           f"sessions throws that prefix away and pays to write it again."),
                "actual_cost_usd": c["cost_with_cache"],
                "estimated_alternative_cost_usd": None,
                "estimated_savings_usd": None, "estimated_savings_pct": None,
                "caveat": "No saving is claimed: what an uncached run would have cost is a "
                          "counterfactual, not money you avoided.",
                "basis": "actual",
            })

        ctx = self.context_analysis(f)
        if ctx["large_context_cost_pct"] > 15:
            recs.append({
                "type": "context_reduction", "confidence": "observed",
                "title": f"{ctx['large_context_cost_pct']}% of spend comes from >"
                         f"{ctx['threshold']//1000}K-context requests",
                "detail": (f"{ctx['large_context_requests']:,} requests re-sent a large prefix "
                           f"on every turn. /compact or a fresh session resets it; how much "
                           f"that would have saved depends on what the work needed, and is "
                           f"not estimated here."),
                "scope": f"{ctx['large_context_requests']:,} requests",
                "actual_cost_usd": ctx["large_context_cost"],
                "estimated_alternative_cost_usd": None,
                "estimated_savings_usd": None, "estimated_savings_pct": None,
                "caveat": "Observed share of spend. No reduction is assumed.",
                "basis": "actual",
            })
        recs.sort(key=lambda r: -(r.get("actual_cost_usd") or 0))
        return {"recommendations": recs, "basis": "actual"}

    # ---------------- forecast ----------------
    def forecast(self, f=None):
        w, p = self.where(f)
        bp = self.billing_period()
        rows = self.q(f"""SELECT r.day, SUM(r.est_cost_usd) cost, SUM(r.billable_tokens) tokens
                          FROM requests r WHERE {w} AND r.day <> '' GROUP BY 1 ORDER BY 1""", p)
        if not rows:
            return {"available": False, "message": "No usage in the selected range."}
        # calendar days, idle days as zero, excluding today (still partial) — the rate
        # below is multiplied by calendar days remaining, so a per-active-day mean
        # would overstate every scenario and a partial today would understate it
        yesterday = (self.today() - timedelta(days=1)).isoformat()
        recent = [r for r in self.daily_series(f, days=15, end=yesterday)]   # complete days only
        priced = [r["cost"] for r in recent]
        sample_days = sum(1 for c in priced if c > 0)
        mean = statistics.fmean(priced) if priced else 0.0
        sd = statistics.pstdev(priced) if len(priced) > 1 else 0.0
        in_period = [r for r in rows if bp["start"] <= r["day"] <= bp["end"]]
        used = sum(r["cost"] for r in in_period)
        used_tok = sum(r["tokens"] for r in in_period)
        left = bp["remaining_days"]
        insufficient = sample_days < 7

        def band(rate, spread=0.0):
            # spend on different days is treated as independent, so the spread of a
            # sum over `left` days grows with sqrt(left), not left
            return {"daily_rate": rate,
                    "end_of_period_cost": used + rate * left + spread * (left ** 0.5)}

        scenarios = {"expected": band(mean)}
        if not insufficient:
            scenarios["conservative"] = band(mean, -sd)
            scenarios["high"] = band(mean, sd)
            scenarios["conservative"]["end_of_period_cost"] = max(
                scenarios["conservative"]["end_of_period_cost"], used)

        tok_mean = statistics.fmean([r["tokens"] for r in recent]) if recent else 0.0

        out = {
            "available": True,
            "method": ("mean of the last 14 complete calendar days (idle days as zero); "
                       "bands are ±1 sd × sqrt(days remaining)"),
            "sample_days": sample_days,
            "insufficient_history": insufficient,
            "daily_mean": mean, "daily_stdev": sd,
            "period_used": used, "period_used_tokens": used_tok,
            "remaining_days": left,
            "scenarios": scenarios,
            "end_of_period_tokens": used_tok + tok_mean * left,
            "estimated_monthly_cost": used + mean * left,
            "basis": "forecast",
        }
        allowance = self.settings["limits"].get("monthly_cost_allowance_usd")
        if allowance:
            rem = allowance - used
            out["limit_exhaustion_date"] = (
                (_d(bp["today"]) + timedelta(days=rem / mean)).isoformat()
                if mean > 0 and rem > 0 else bp["today"])
            out["will_exceed"] = scenarios["expected"]["end_of_period_cost"] > allowance
        else:
            out["limit_exhaustion_date"] = UNAVAILABLE
            out["will_exceed"] = None
        return out

    # ---------------- budgets ----------------
    def budgets(self, f=None):
        b = self.settings["budgets"]
        bp = self.billing_period()
        fc = self.forecast(f)
        w, p = self.where(f)
        period = self.one(f"SELECT COALESCE(SUM(r.est_cost_usd),0) c,"
                          f" COALESCE(SUM(r.billable_tokens),0) t FROM requests r"
                          f" WHERE {w} AND r.day>=? AND r.day<=?", p + [bp["start"], bp["end"]])
        today = self.one(f"SELECT COALESCE(SUM(r.est_cost_usd),0) c FROM requests r"
                         f" WHERE {w} AND r.day=?", p + [bp["today"]])
        out = {"thresholds_pct": self.settings["alert_thresholds_pct"], "lines": [],
               "basis": "estimated vs configured budget"}

        def line(name, budget, actual, forecast_val, unit="USD"):
            if not budget:
                return {"name": name, "configured": False, "message": "No budget configured",
                        "actual": actual, "unit": unit}
            pct = 100.0 * actual / budget
            fpct = 100.0 * forecast_val / budget if forecast_val is not None else None
            breached = [t for t in out["thresholds_pct"] if pct >= t]
            fbreach = [t for t in out["thresholds_pct"]
                       if fpct is not None and fpct >= t and t not in breached]
            return {"name": name, "configured": True, "budget": budget, "actual": actual,
                    "forecast": forecast_val,
                    "variance": (forecast_val - budget) if forecast_val is not None else None,
                    "used_pct": round(pct, 1),
                    "forecast_pct": round(fpct, 1) if fpct is not None else None,
                    "status": self._status(fpct if fpct is not None else pct),
                    "thresholds_breached": breached, "thresholds_forecast_breach": fbreach,
                    "unit": unit}

        fc_cost = fc.get("scenarios", {}).get("expected", {}).get("end_of_period_cost")
        out["lines"].append(line("Monthly spend", b.get("monthly_usd"), period["c"], fc_cost))
        out["lines"].append(line("Daily spend", b.get("daily_usd"), today["c"], today["c"]))
        out["lines"].append(line("Monthly tokens", b.get("monthly_tokens"), period["t"],
                                 fc.get("end_of_period_tokens"), unit="tokens"))
        for proj, bud in (b.get("per_project_usd") or {}).items():
            act = self.one("""SELECT COALESCE(SUM(r.est_cost_usd),0) c FROM requests r
                              JOIN projects pr ON pr.id=r.project_id
                              WHERE pr.name=? AND r.day>=? AND r.day<=?""",
                           (proj, bp["start"], bp["end"]))["c"]
            out["lines"].append(line(f"Project: {proj}", bud, act, None))
        for model, bud in (b.get("per_model_usd") or {}).items():
            act = self.one("""SELECT COALESCE(SUM(est_cost_usd),0) c FROM requests
                              WHERE model=? AND day>=? AND day<=?""",
                           (model, bp["start"], bp["end"]))["c"]
            out["lines"].append(line(f"Model: {self.pricing.display_name(model)}", bud, act, None))
        return out

    # ---------------- anomalies ----------------
    def anomalies(self, f=None):
        w, p = self.where(f)
        cfg = self.settings["anomaly"]
        found = []
        yesterday = (self.today() - timedelta(days=1)).isoformat()
        series = [d for d in self.daily_series(dict(f or {}, agents=["claude"]), end=yesterday)]
        priced = [d for d in series if d["cost"] > 0]
        if len(priced) >= 14:
            # Median/MAD, not mean/stdev: unpriced $0 days from agents without pricing
            # data (e.g. Cursor) would otherwise pollute the mean/sd baseline and
            # either mask real spikes or manufacture fake ones. MAD is scaled by
            # 1.4826 so it estimates the same thing a standard deviation would under
            # a normal distribution, without a few extreme days inflating it the way
            # a real stdev would.
            vals = [d["cost"] for d in priced]
            med = statistics.median(vals)
            mad = statistics.median(abs(v - med) for v in vals) * 1.4826 or 1e-9
            for d in priced:
                score = (d["cost"] - med) / mad
                ratio = d["cost"] / med if med else 0
                if score >= cfg.get("daily_robust_z", 3.5) and ratio >= cfg["daily_ratio"]:
                    found.append({
                        "severity": "high", "type": "daily_spike", "date": d["day"],
                        "title": f"{d['day']} spend was {ratio:.1f}x your daily average",
                        "detail": f"${d['cost']:,.2f} vs a ${med:,.2f} median priced day (robust z={score:.1f}).",
                        "metric_value": d["cost"], "baseline": med, "ratio": round(ratio, 2),
                        "drilldown": {"filter": {"start": d["day"], "end": d["day"]}},
                        "basis": "estimated",
                    })
        sess = self.sessions(f, limit=100000, order="cost")
        if len(sess) >= 5:
            # Median, not mean: session token counts are heavily right-skewed, and a
            # mean lets the outliers inflate the very baseline they are measured
            # against — which understates how far out they really are. Candidates are
            # ranked by tokens too, since that is the metric being tested; ordering by
            # cost hid token-heavy work on cheap models.
            # Sessions with no token data (Cursor transcripts don't always carry it)
            # are not comparable and would drag the baseline down.
            vals = [s["tokens"] for s in sess if (s["tokens"] or 0) > 0]
            mean = (statistics.median(vals) if vals else 0) or 1
            # Session sizes are heavy-tailed enough that any fixed multiple of the
            # baseline still matches a fifth of them, so the threshold alone cannot
            # keep this list short. Take the most extreme few and leave room for the
            # other anomaly types, which have much smaller ratios and would otherwise
            # be sorted off the end of the list.
            outliers = 0
            for s in sorted(sess, key=lambda x: -(x["tokens"] or 0))[:40]:
                ratio = s["tokens"] / mean
                if ratio >= cfg["session_ratio"] and outliers < cfg.get("max_session_outliers", 5):
                    outliers += 1
                    found.append({
                        "severity": "low", "type": "session_outlier",
                        "title": f"Among your largest sessions: {ratio:.1f}x the median",
                        "detail": f"{s['title'] or s['session_id'][:8]} — {s['tokens']:,} tokens, "
                                  f"${s['cost']:,.2f} in {s['project']}.",
                        "metric_value": s["tokens"], "baseline": mean, "ratio": round(ratio, 2),
                        "drilldown": {"session_id": s["session_id"]},
                        "basis": "estimated",
                    })
        # week-over-week model shift
        if self.last_day:
            end = _d(self.last_day)
            cur_s = (end - timedelta(days=6)).isoformat()
            prev_s, prev_e = (end - timedelta(days=13)).isoformat(), (end - timedelta(days=7)).isoformat()
            for m in self.q(f"SELECT DISTINCT r.model FROM requests r WHERE {w}", p):
                model = m["model"]
                a = self.one(f"SELECT COALESCE(SUM(r.est_cost_usd),0) c FROM requests r WHERE {w}"
                             f" AND r.model=? AND r.day>=?", p + [model, cur_s])["c"]
                bq = self.one(f"SELECT COALESCE(SUM(r.est_cost_usd),0) c FROM requests r WHERE {w}"
                              f" AND r.model=? AND r.day>=? AND r.day<=?",
                              p + [model, prev_s, prev_e])["c"]
                if bq > 1 and a > bq * 1.4:
                    found.append({
                        "severity": "medium", "type": "model_shift",
                        "title": f"{self.pricing.display_name(model)} spend rose "
                                 f"{100*(a-bq)/bq:.0f}% week over week",
                        "detail": f"${bq:,.2f} → ${a:,.2f}.",
                        "metric_value": a, "baseline": bq, "ratio": round(a / bq, 2),
                        "drilldown": {"filter": {"models": [model], "start": cur_s}},
                        "basis": "estimated",
                    })
        found.sort(key=lambda x: -x.get("ratio", 0))
        return {"anomalies": found[:25], "basis": "estimated"}

    # ---------------- scorecard ----------------
    def scorecard(self, f=None):
        eff = self.efficiency(f)
        ctx = self.context_analysis(f)
        wst = self.waste(f)
        bud = self.budgets(f)
        mdl = self.models(f)
        dims = []

        def dim(name, score, detail, weight=1.0):
            dims.append({"name": name, "score": max(0, min(100, round(score))),
                         "detail": detail, "weight": weight})

        chr_ = eff["cache_hit_ratio"]
        ccs = eff["cache_read_cost_share"]
        if chr_ is None or ccs is None:
            dim("Cache efficiency", 50, "No cache activity in range", 1.0)
        else:
            # Graded on cost share, not token share: writes are a couple of percent of
            # cache tokens but a much larger share of cache spend, so the token ratio
            # scores near 100 even when writes are material money.
            split = eff["cache"]["cost_split"]
            detail = (f"Reads are {ccs*100:.1f}% of cache cost "
                      f"({chr_*100:.1f}% of cache tokens).")
            mult = split.get("write_vs_read_multiple")
            if mult:
                detail += f" A write token costs {mult:.1f}x a read token."
            h1 = split.get("write_1h_token_share")
            if h1 is not None and h1 >= 0.5 and split.get("write_tokens"):
                detail += (f" {h1*100:.0f}% of your writes are 1h writes; the TTL replay in"
                           " Context & cache prices whether that is the cheaper choice for"
                           " your real inter-turn gaps.")
            dim("Cache efficiency", ccs * 100, detail, 1.2)

        sc_cfg = self.settings.get("scorecard", {})
        target = sc_cfg.get("target_output_ratio", 0.0088)
        outr = eff["output_ratio"]
        dim("Token efficiency", min(outr / target, 1.0) * 100,
            f"Output is {outr*100:.2f}% of billable tokens against a "
            f"{target*100:.2f}% reference.", 1.2)

        big_pct = ctx["large_context_cost_pct"]
        dim("Context efficiency", 100 - big_pct,
            f"{big_pct}% of spend came from requests above "
            f"{ctx['threshold']//1000}K context.", 1.0)

        excess = wst["excess_pct"]
        dim("Waste control", 100 - min(excess, 100),
            f"{excess}% of spend is estimated excess over a reasonable baseline "
            f"({wst['exposed_pct']}% of spend sits in items a rule touched).", 1.3)

        priced = [r for r in mdl["rows"] if r["tier"] != "none" and r["cost"]]
        frontier_pct = (100.0 * sum(r["cost"] for r in priced if r["tier"] == "frontier")
                        / (sum(r["cost"] for r in priced) or 1))
        allow = sc_cfg.get("frontier_cost_share_allowance_pct", 40)
        dim("Model selection", 100 - max(frontier_pct - allow, 0) * 1.5,
            f"{frontier_pct:.0f}% of spend is on frontier-tier models.", 1.1)

        ml = next((l for l in bud["lines"] if l["name"] == "Monthly spend"), None)
        if ml and ml.get("configured"):
            fp = ml.get("forecast_pct") or ml["used_pct"]
            dim("Budget adherence", 100 - max(fp - 100, 0) * 2 - max(fp - 85, 0),
                f"Forecast is {fp:.0f}% of the configured monthly budget.", 1.3)
        else:
            dim("Budget adherence", 50,
                "No monthly budget configured — set one in config/settings.json to be graded.", 0.4)

        cpo = eff["cost_per_1k_output"]
        cpo_target = sc_cfg.get("target_cost_per_1k_output_usd", 0.30)
        dim("Cost efficiency", 100 - min(cpo / (cpo_target * 2) * 100, 100),
            f"${cpo:.3f} estimated per 1K output tokens.", 1.0)

        tw = sum(d["weight"] for d in dims)
        total = round(sum(d["score"] * d["weight"] for d in dims) / tw)
        strong = sorted(dims, key=lambda d: -d["score"])[:3]
        weak = sorted(dims, key=lambda d: d["score"])[:3]
        top = wst["findings"][0] if wst["findings"] else None
        return {
            "score": total, "grade": ("A" if total >= 85 else "B" if total >= 70
                                      else "C" if total >= 55 else "D" if total >= 40 else "F"),
            "dimensions": dims,
            "what_is_good": [f"{d['name']}: {d['detail']}" for d in strong if d["score"] >= 60],
            "needs_attention": [f"{d['name']}: {d['detail']}" for d in weak if d["score"] < 70],
            "biggest_opportunity": (
                {"title": top["title"], "detail": top["detail"],
                 "exposed_cost_usd": top["est_cost_usd"],
                 "estimated_excess_usd": top["est_excess_usd"],
                 "action": top["recommended_action"]}
                if top else None),
            "basis": "estimated",
        }

    # ---------------- advisor ----------------
    def advisor(self, f=None):
        actions = []
        wst = self.waste(f)
        recs = self.recommendations(f)
        anos = self.anomalies(f)
        fc = self.forecast(f)
        bud = self.budgets(f)
        ov = self.overview(f)

        for a in anos["anomalies"][:2]:
            actions.append({"priority": 1, "kind": "anomaly", "text": a["title"],
                            "detail": a["detail"], "drilldown": a.get("drilldown"),
                            "basis": "estimated"})
        for wf in wst["findings"][:2]:
            actions.append({"priority": 2, "kind": "waste",
                            "text": wf["title"],
                            "detail": f"{wf['detail']} ~${wf['est_excess_usd']:,.2f} estimated "
                                      f"excess across ${wf['est_cost_usd']:,.2f} of exposed spend. "
                                      f"{wf['recommended_action']}",
                            "basis": "estimated"})
        for r in recs["recommendations"][:2]:
            if r["type"] == "cache_working":
                continue
            actions.append({"priority": 3, "kind": "recommendation", "text": r["title"],
                            "detail": r.get("detail") or r.get("caveat") or "",
                            "basis": r.get("basis", "recommendation")})
        ml = next((l for l in bud["lines"] if l["name"] == "Monthly spend"), None)
        if ml and ml.get("configured") and ml.get("forecast_pct"):
            if ml["forecast_pct"] >= 90:
                actions.append({
                    "priority": 1, "kind": "budget",
                    "text": f"Forecast to finish the period at {ml['forecast_pct']:.0f}% of budget",
                    "detail": f"${ml['actual']:,.2f} spent, ${ml['forecast']:,.2f} forecast against "
                              f"a ${ml['budget']:,.2f} budget.", "basis": "forecast"})
        # concentration
        pr = self.prompts(f, limit=3, order="cost")
        if pr and ov["est_cost_usd"]:
            share = 100.0 * sum(x["pcost"] for x in pr) / ov["est_cost_usd"]
            if share >= 8:
                actions.append({
                    "priority": 2, "kind": "concentration",
                    "text": f"3 prompts account for {share:.0f}% of spend in range",
                    "detail": "; ".join(f"\"{x['preview'][:60]}…\" (${x['pcost']:,.2f})" for x in pr),
                    "basis": "estimated"})
        actions.sort(key=lambda a: a["priority"])
        # No "savings opportunity" range: the old one was a guessed 20% of large-context
        # spend, then 0.6x of that for a low end. Neither factor came from the data.
        return {
            "question": "What should I do today?",
            "actions": actions[:6],
            "generated_from": "Live dashboard data for the current filter selection.",
            "basis": "mixed: see per-item basis",
        }

    # ---------------- claude code / developer ----------------
    def developer(self, f=None):
        w, p = self.where(f)
        tools = self.q(f"""SELECT t.name, COUNT(*) calls, COUNT(DISTINCT t.session_id) sessions
                           FROM tool_calls t JOIN requests r ON r.id = t.request_pk
                           WHERE {w} GROUP BY 1 ORDER BY 2 DESC""", p)
        files = self.q(f"""SELECT ft.path, COUNT(*) touches, COUNT(DISTINCT ft.session_id) sessions,
                           GROUP_CONCAT(DISTINCT ft.op) ops
                           FROM files_touched ft WHERE ft.session_id IN
                           (SELECT DISTINCT r.session_id FROM requests r WHERE {w})
                           GROUP BY 1 ORDER BY 2 DESC LIMIT 40""", p)
        branches = self.q(f"""SELECT COALESCE(s.git_branch,'(none)') branch,
                              COUNT(DISTINCT s.id) sessions, SUM(r.est_cost_usd) cost,
                              SUM(r.billable_tokens) tokens
                              FROM requests r JOIN sessions s ON s.id=r.session_id
                              WHERE {w} GROUP BY 1 ORDER BY cost DESC LIMIT 25""", p)
        repos = [r for r in self.projects(f) if not r["is_sandbox"]]
        return {
            "tools": tools, "files": files, "branches": branches, "repositories": repos,
            "cost_per_repository": [{"repository": r["name"], "cost": r["cost"],
                                     "sessions": r["sessions"], "prompts": r["prompts"],
                                     "files_touched": r["files_touched"],
                                     "cost_per_session": r["avg_cost_per_session"]}
                                    for r in repos],
            "unavailable": {
                "lines_changed": UNAVAILABLE + " (transcripts record file paths, not diff size)",
                "commits": UNAVAILABLE, "pull_requests": UNAVAILABLE,
                "bugs_fixed": UNAVAILABLE,
                "cost_per_pr": UNAVAILABLE + " — no PR linkage in Claude Code transcripts",
            },
            "basis": "actual tool activity, estimated cost",
        }

    # ---------------- global search ----------------
    def search(self, term, limit=40):
        like = f"%{term}%"
        return {
            "term": term,
            "prompts": self.q(
                "SELECT id prompt_id, ts, category, session_id, substr(text,1,240) preview,"
                " est_cost_usd, billable_tokens FROM prompts WHERE text LIKE ?"
                " ORDER BY est_cost_usd DESC LIMIT ?", (like, limit)),
            "sessions": self.q(
                "SELECT s.id session_id, s.title, s.git_branch, pr.name project, s.started_at,"
                " s.est_cost_usd, s.billable_tokens FROM sessions s"
                " JOIN projects pr ON pr.id=s.project_id"
                " WHERE s.id LIKE ? OR s.title LIKE ? OR s.git_branch LIKE ?"
                " ORDER BY s.est_cost_usd DESC LIMIT ?", (like, like, like, limit)),
            "projects": self.q(
                "SELECT id project_id, name, path, slug FROM projects"
                " WHERE name LIKE ? OR path LIKE ? LIMIT ?", (like, like, limit)),
            "models": self.q(
                "SELECT model, COUNT(*) requests, SUM(est_cost_usd) cost FROM requests"
                " WHERE model LIKE ? GROUP BY 1", (like,)),
            "tools": self.q(
                "SELECT name, target, COUNT(*) n FROM tool_calls"
                " WHERE name LIKE ? OR target LIKE ? GROUP BY name, target"
                " ORDER BY n DESC LIMIT ?", (like, like, limit)),
            "days": self.q(
                "SELECT day, COUNT(*) requests, SUM(est_cost_usd) cost FROM requests"
                " WHERE day LIKE ? GROUP BY 1 ORDER BY 1", (like,)),
        }

    # ---------------- filter option lists ----------------
    def agents(self):
        """Every agent detected on this machine, with what its local data can show."""
        from .agents import AGENTS, detect
        found = detect()
        used = {r["agent"]: r for r in self.q("""SELECT agent, COUNT(*) requests,
            COUNT(DISTINCT session_id) sessions, SUM(billable_tokens) tokens,
            SUM(est_cost_usd) cost, MIN(day) first, MAX(day) last FROM requests GROUP BY agent""")}
        out = []
        for k, a in AGENTS.items():
            u = used.get(k) or {}
            if not found.get(k) and not u:
                continue
            out.append({"id": k, "name": a["name"], "data": a["data"], "note": a["note"],
                        "requests": u.get("requests") or 0, "sessions": u.get("sessions") or 0,
                        "tokens": u.get("tokens") or 0, "cost": u.get("cost") or 0,
                        "first": u.get("first"), "last": u.get("last")})
        return out

    def by_agent(self, f=None):
        """Side-by-side totals per agent for the multi-agent view."""
        f = dict(f or {})
        w, p = self.where(f)
        rows = self.q(f"""SELECT r.agent, COUNT(*) requests, COUNT(DISTINCT r.session_id) sessions,
            COUNT(DISTINCT r.prompt_id) prompts, SUM(r.billable_tokens) tokens,
            SUM(r.input_tokens) input_tokens, SUM(r.output_tokens) output_tokens,
            SUM(r.cache_read_tokens) cache_read_tokens, SUM(r.est_cost_usd) cost,
            SUM(r.tool_call_count) tool_calls, COUNT(DISTINCT r.day) active_days,
            GROUP_CONCAT(DISTINCT r.model) models
            FROM requests r WHERE {w} GROUP BY r.agent ORDER BY cost DESC, requests DESC""", p)
        daily = self.q(f"""SELECT r.day, r.agent, SUM(r.billable_tokens) tokens, SUM(r.est_cost_usd) cost,
            COUNT(*) requests FROM requests r WHERE {w} GROUP BY 1, 2 ORDER BY 1""", p)
        from .agents import AGENTS
        for r in rows:
            a = AGENTS.get(r["agent"], {})
            r["name"], r["data"], r["note"] = a.get("name", r["agent"]), a.get("data"), a.get("note")
        return {"agents": rows, "daily": daily, "basis": "estimated"}

    def options(self):
        return {
            "agents": self.agents(),
            "models": self.q("SELECT model, agent, COUNT(*) n FROM requests GROUP BY 1 ORDER BY 3 DESC"),
            "projects": self.q("SELECT pr.id project_id, pr.name, pr.is_sandbox, pr.agent, COUNT(r.id) n"
                               " FROM projects pr LEFT JOIN requests r ON r.project_id=pr.id"
                               " GROUP BY pr.id HAVING n>0 ORDER BY n DESC"),
            "categories": self.q("SELECT category, COUNT(*) n FROM prompts GROUP BY 1 ORDER BY 2 DESC"),
            "date_range": {"first": self.first_day, "last": self.last_day},
            "billing_period": self.billing_period(),
            "meta": self.meta,
            "pricing": {"updated": self.pricing.updated, "source": self.pricing.source,
                        "models": self.pricing.models},
            "settings": self.settings,
            "version": _version(),
            "unavailable_label": UNAVAILABLE,
        }
