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


class Analytics:
    def __init__(self, db_path=DB_PATH):
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.pricing = Pricing()
        self.settings = load_settings()
        self.meta = {r["key"]: r["value"] for r in self.db.execute("SELECT * FROM meta")}
        row = self.db.execute("SELECT MIN(day) a, MAX(day) b FROM requests WHERE day<>''").fetchone()
        self.first_day, self.last_day = row["a"], row["b"]

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

    # ---------------- billing period ----------------
    def billing_period(self, today=None):
        bp = self.settings["billing_period"]
        today = today or (_d(self.last_day) if self.last_day else date.today())
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

        # Trailing rates are measured over the last N days of ACTUAL activity, not
        # only the slice inside the billing period — early in a period that slice is
        # too short to be a rate. This keeps burn and forecast on one methodology.
        recent = self.q(f"""SELECT r.day, SUM(r.est_cost_usd) cost,
                            SUM(r.billable_tokens) tokens, COUNT(*) requests
                            FROM requests r WHERE {w} AND r.day <> ''
                            GROUP BY 1 ORDER BY 1""", p)
        last7, last14 = recent[-7:], recent[-14:]
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
            "forecast_note": ("Projection uses the %d-day mean daily spend, the same rate as the "
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
            days_left = (remaining / rate) if rate > 0 else None
            proj = used_val + rate * bp["remaining_days"]
            out["allowances"][label] = {
                "configured": True, "allowance": allowance, "used": used_val,
                "remaining": remaining, "used_pct": round(pct, 1),
                "remaining_pct": round(max(100 - pct, 0), 1),
                "days_until_limit": (round(days_left, 1) if days_left is not None else None),
                "limit_date": ((_d(bp["today"]) + timedelta(days=days_left)).isoformat()
                               if days_left is not None and days_left < 3650 else None),
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
            r["utilization_pct"] = (round(100.0 * (r["avg_context"] or 0) / r["context_window"], 1)
                                    if r["context_window"] else None)
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
            r["output_ratio"] = r["output_tokens"] / r["tokens"] if r["tokens"] else 0
            r["cache_hit_ratio"] = (r["cache_read_tokens"] /
                                    (r["cache_read_tokens"] + r["cache_write_tokens"])
                                    if (r["cache_read_tokens"] + r["cache_write_tokens"]) else None)
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
        p["advisor"] = self.prompt_advisor(p)
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
            "tokens_per_request": tot / (t["n"] or 1),
            "avg_context_tokens": t["avgctx"],
            "cost_per_1k_output": (1000.0 * (t["cost"] or 0) / (t["o"] or 1)),
            "cost_per_prompt": ((t["cost"] or 0) /
                                max(self.one(f"SELECT COUNT(DISTINCT r.prompt_id) n FROM requests r WHERE {w}", p)["n"], 1)),
            "cache": {
                "reads": t["cr"], "writes": t["cw"],
                "cost_with_cache": t["cost"], "cost_without_cache": t["cost_nc"],
                "estimated_savings_usd": (t["cost_nc"] or 0) - (t["cost"] or 0),
                "savings_pct": (round(100.0 * ((t["cost_nc"] or 0) - (t["cost"] or 0))
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
            over_tokens = max(r["char_len"] - budget_chars, 0) / 4.0   # ~4 chars/token
            resent = over_tokens * max(r["requests"], 1)
            r["excess"] = (r["cost"] * resent / r["tokens"]) if r["tokens"] else 0
        if rows:
            add("high", "long_prompts",
                f"{len(rows)} very long prompts (>{budget_chars:,} chars)",
                "Long pasted prompts inflate the cached prefix re-sent on every following turn.",
                rows, "Move large pasted context into a file and reference it, or summarize first.",
                "prompt_id",
                f"share of spend from prompt text beyond {budget_chars:,} characters, re-sent per request")

        # 2. duplicate prompts — excess is the cost of the repeats, not the first ask.
        dups = self.q(f"""SELECT pr.norm_hash, COUNT(*) n, substr(MIN(pr.text),1,160) preview,
                          SUM(pr.est_cost_usd) cost, SUM(pr.billable_tokens) tokens,
                          MIN(pr.est_cost_usd) first_cost, GROUP_CONCAT(pr.id) prompt_ids
                          FROM prompts pr WHERE {pfilter} AND pr.char_len > 25
                          GROUP BY pr.norm_hash HAVING n > 1
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
        ratios = [r["x"] for r in self.q(
            "SELECT CAST(output_tokens AS REAL)/billable_tokens x FROM sessions"
            " WHERE billable_tokens > 100000")]
        median_ratio = statistics.median(ratios) if ratios else 0.0
        cutoff = median_ratio * rules["low_output_ratio_vs_median"]
        low = self.q(f"""SELECT s.id session_id, s.title, s.billable_tokens tokens,
                         s.output_tokens out_tokens, s.est_cost_usd cost, s.request_count requests,
                         (CAST(s.output_tokens AS REAL)/MAX(s.billable_tokens,1)) output_ratio,
                         proj.name project FROM sessions s JOIN projects proj ON proj.id=s.project_id
                         WHERE {sfilter} AND s.billable_tokens > ?
                           AND (CAST(s.output_tokens AS REAL)/MAX(s.billable_tokens,1)) < ?
                         ORDER BY cost DESC LIMIT 15""",
                     p + [rules["huge_session_tokens"], cutoff])
        for r in low:
            # at the median ratio the same output needs out/median tokens, so the
            # baseline cost scales by (actual ratio / median ratio)
            r["excess"] = r["cost"] * (1 - (r["output_ratio"] / median_ratio)) if median_ratio else 0
        if low:
            add("high", "low_yield_sessions",
                f"{len(low)} large sessions yielded under {cutoff*100:.2f}% output tokens",
                f"Your median session turns {median_ratio*100:.2f}% of billable tokens into output. "
                f"These ran well below that while consuming heavy context.",
                low, "Start a fresh session or /compact once a thread stops producing new output.",
                "session_id", "spend above what the same output would cost at your median session efficiency")

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
                               pr.cache_read_tokens, pr.cache_write_tokens
                               FROM prompts pr WHERE {pfilter}
                                 AND pr.output_tokens < ? AND pr.est_cost_usd > 0
                                 AND EXISTS (SELECT 1 FROM requests r2 WHERE r2.prompt_id=pr.id
                                             AND r2.model IN ({ph}))
                               ORDER BY cost DESC LIMIT 15""",
                           p + [rules["simple_task_output_tokens"]] + frontier)
            for r in small:
                alt = (self.pricing.estimate(cheaper, r["input_tokens"], r["out_tokens"],
                                             r["cache_read_tokens"], r["cache_write_tokens"], 0)
                       if cheaper else r["cost"])
                r["excess"] = max(r["cost"] - alt, 0)
            if small:
                add("medium", "frontier_on_small_tasks",
                    f"{len(small)} frontier-model prompts produced under "
                    f"{rules['simple_task_output_tokens']} output tokens",
                    "Short, simple turns running on the most expensive model tier.",
                    small, "Route short lookups and confirmations to a cheaper model tier.",
                    "prompt_id",
                    f"difference against the same tokens priced at {self.pricing.display_name(cheaper)}"
                    if cheaper else "n/a")

        # 5. tool loops — excess is the share of the loop beyond the threshold.
        loops = self.q(f"""SELECT pr.id prompt_id, substr(pr.text,1,160) preview, pr.session_id,
                           pr.tool_calls tools, pr.est_cost_usd cost, pr.billable_tokens tokens
                           FROM prompts pr WHERE {pfilter} AND pr.tool_calls > 40
                           ORDER BY cost DESC LIMIT 15""", p)
        for r in loops:
            r["excess"] = r["cost"] * max(r["tools"] - 40, 0) / max(r["tools"], 1)
        if loops:
            add("medium", "tool_loops", f"{len(loops)} prompts triggered 40+ tool calls",
                "Long agentic loops re-send the whole conversation each step, so cost grows super-linearly.",
                loops, "Split the task, or give more precise instructions up front.", "prompt_id",
                "share of the loop beyond the first 40 tool calls")

        # 6. poor cache reuse — excess is the write premium over plain input pricing.
        poor = self.q(f"""SELECT s.id session_id, s.title, proj.name project,
                          s.cache_read_tokens reads, s.cache_write_tokens writes,
                          s.est_cost_usd cost, s.request_count requests, s.models
                          FROM sessions s JOIN projects proj ON proj.id=s.project_id
                          WHERE {sfilter} AND s.cache_write_tokens > 500000
                            AND s.cache_read_tokens < s.cache_write_tokens * 3
                          ORDER BY cost DESC LIMIT 15""", p)
        for r in poor:
            m = (r["models"] or "").split(",")[0]
            rt = self.pricing.rates(m)
            premium = float(rt.get("cache_write_5m", 0)) - float(rt.get("input", 0))
            r["excess"] = max(r["writes"] * premium / 1_000_000.0, 0)
        if poor:
            add("medium", "poor_cache_reuse", f"{len(poor)} sessions wrote cache they barely reused",
                "Cache writes cost more than plain input; they only pay off when read back repeatedly.",
                poor, "Keep related work in one continuous session so the cached prefix is reused.",
                "session_id", "the cache-write premium over plain input pricing on those writes")

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
                "basis": "estimated"}

    # ---------------- recommendations ----------------
    # ---------------- model switch advisor ----------------
    # Which work needs the frontier model and which does not. "keep" = reasoning-heavy work
    # where a cheaper model is a quality risk; the rest is routed down a tier.
    SWITCH_RULES = {
        "casual": ("economy", "high"), "other": ("economy", "medium"),
        "documentation": ("balanced", "high"), "writing": ("balanced", "high"),
        "learning": ("balanced", "high"), "testing": ("balanced", "medium"),
        "research": ("balanced", "medium"), "data_analysis": ("balanced", "medium"),
        "automation": ("balanced", "medium"), "coding": ("balanced", "low"),
        "refactoring": ("balanced", "low"),
        "debugging": ("keep", None), "architecture": ("keep", None),
        "planning": ("keep", None), "code_review": ("keep", None),
    }
    TIER_RANK = {"economy": 0, "balanced": 1, "frontier": 2}

    # How each vendor's agent is told to change model. Used by model_switch() and by the
    # model_downgrade recommendations, which must name the agent they keep you inside.
    SWITCH_HOW_BY_AGENT = {
        "anthropic": {"agent": "Claude Code", "session": "/model <name>",
                      "project": '"model": "<name>" in <repo>/.claude/settings.json'},
        "openai": {"agent": "Codex", "session": "/model in Codex, or codex -m <name>",
                   "project": 'model = "<name>" in ~/.codex/config.toml (or a profile)'},
        "google": {"agent": "Gemini CLI", "session": "/model in Gemini CLI, or gemini -m <name>",
                   "project": '"model": {"name": "<name>"} in <repo>/.gemini/settings.json'},
    }

    def _cheapest(self, tier, provider=None):
        """Cheapest model in a tier — from the same provider, since an agent can only
        switch between its own vendor's models (Codex can't run Haiku)."""
        ms = [m for m, v in self.pricing.models.items() if v.get("tier") == tier
              and (provider is None or v.get("provider", "anthropic") == provider)]
        return min(ms, key=lambda m: self.pricing.rates(m).get("output", 1e9)) if ms else None

    # ---------------- evidence: what the cheaper model actually did ----------------
    # model_switch() reprices your tokens on a cheaper model, which assumes the cheaper
    # model would have done the same work in the same number of turns. Often it would
    # not: a weaker model can take five times the turns on the same task, and the
    # repricing then promises a saving that never arrives.
    #
    # Where you have already run more than one model on the same kind of work, we do not
    # have to assume anything. This compares what each model actually cost per prompt on
    # that category, and how much work it took to get there.

    MIN_PROMPTS = 8          # below this a per-category average is noise, not evidence
    SAVING_FLOOR_PCT = 20    # smaller gaps are inside the noise of what you happened to ask
    TURN_TOLERANCE = 1.35    # more turns than this and the cheaper model was grinding
    REPEAT_TOLERANCE = 12.0  # percentage points of extra re-asking we will accept

    def model_evidence(self, f=None):
        """Back-test a model switch against your own history.

        For every category where you ran more than one model, report what each one
        actually cost per prompt and what it took: turns, tool calls, and how often you
        had to ask the same thing again. A candidate is only recommended when it was
        genuinely cheaper per prompt *and* did not need materially more work to get
        there — which is the part a repricing cannot see.
        """
        w, p = self.where(f)
        # The filter is request-scoped, so select the prompts it touches as a subquery.
        # Joining requests directly would repeat each prompt once per request and quietly
        # multiply both the counts and every average by the turn count.
        scope = f"pr.id IN (SELECT r.prompt_id FROM requests r WHERE {w})"
        # Single-model prompts only: a prompt answered by two models cannot be
        # attributed to either, and mixed rows would blur the comparison.
        clean = ("pr.models IS NOT NULL AND pr.models NOT LIKE '%,%' "
                 "AND pr.models != '<synthetic>' AND pr.est_cost_usd > 0")
        rows = self.q(f"""SELECT COALESCE(pr.category,'other') category, pr.models model,
            pr.agent agent, COUNT(*) prompts, SUM(pr.est_cost_usd) cost,
            AVG(pr.est_cost_usd) cost_per_prompt,
            AVG(pr.request_count) turns, AVG(pr.tool_calls) tools,
            AVG(pr.output_tokens) out_tokens, AVG(pr.max_context_tokens) ctx
            FROM prompts pr WHERE {scope} AND {clean}
            GROUP BY 1, 2, 3 HAVING prompts >= ?""", p + [self.MIN_PROMPTS])

        repeats = {(r["category"], r["model"]): r["pct"] for r in self.q(f"""
            SELECT COALESCE(pr.category,'other') category, pr.models model,
                ROUND(100.0 * SUM(CASE WHEN dup.n > 1 THEN 1 ELSE 0 END) / COUNT(*), 1) pct
            FROM prompts pr
            LEFT JOIN (SELECT norm_hash, COUNT(*) n FROM prompts GROUP BY norm_hash) dup
                   ON dup.norm_hash = pr.norm_hash
            WHERE {scope} AND {clean}
            GROUP BY 1, 2""", p)}

        by_cat = defaultdict(list)
        for r in rows:
            r["repeat_pct"] = repeats.get((r["category"], r["model"]), 0.0) or 0.0
            r["name"] = self.pricing.display_name(r["model"])
            r["tier"] = self.pricing.tier(r["model"])
            by_cat[r["category"]].append(r)

        out, total_save = [], 0.0
        for cat, models in by_cat.items():
            if len(models) < 2:
                continue
            # The incumbent is what you spend the most on here — that is the bill a
            # switch would actually change.
            cur = max(models, key=lambda m: m["cost"])
            rule = self.SWITCH_RULES.get(cat, ("balanced", "low"))[0]
            cands = []
            for m in models:
                if m["model"] == cur["model"] or m["cost_per_prompt"] >= cur["cost_per_prompt"]:
                    continue
                # Only models the same agent can run. Telling a Claude Code user to use a
                # GPT model is not a setting change, it is a different tool, and the
                # comparison would be between two different ways of working.
                if m["agent"] != cur["agent"]:
                    continue
                save_pct = 100.0 * (1 - m["cost_per_prompt"] / cur["cost_per_prompt"])
                turn_ratio = (m["turns"] / cur["turns"]) if cur["turns"] else 1.0
                repeat_delta = m["repeat_pct"] - cur["repeat_pct"]
                # Observed, not repriced: what your own prompts cost on each side.
                save = (cur["cost_per_prompt"] - m["cost_per_prompt"]) * cur["prompts"]
                if save_pct < self.SAVING_FLOOR_PCT:
                    verdict, why = "marginal", (
                        f"Only {save_pct:.0f}% cheaper per prompt — inside the noise of what "
                        f"you happened to ask each model.")
                elif turn_ratio > self.TURN_TOLERANCE:
                    verdict, why = "risky", (
                        f"Cost {save_pct:.0f}% less per prompt but took {turn_ratio:.1f}x the "
                        f"turns ({m['turns']:.0f} vs {cur['turns']:.0f}). It got there by "
                        f"grinding, and that is the cost the headline number misses.")
                elif repeat_delta > self.REPEAT_TOLERANCE:
                    verdict, why = "risky", (
                        f"{save_pct:.0f}% cheaper per prompt, but you re-asked "
                        f"{m['repeat_pct']:.0f}% of these prompts against "
                        f"{cur['repeat_pct']:.0f}% on {cur['name']} — rework you paid for twice.")
                elif rule == "keep":
                    verdict, why = "caution", (
                        f"{save_pct:.0f}% cheaper per prompt and no more turns, but {cat.replace('_',' ')} "
                        f"is reasoning-heavy work where a miss is expensive in ways this data "
                        f"cannot show. Worth a trial, not a default.")
                else:
                    verdict, why = "supported", (
                        f"{save_pct:.0f}% cheaper per prompt on {m['prompts']} of your own "
                        f"{cat.replace('_',' ')} prompts, in {turn_ratio:.1f}x the turns "
                        f"({m['turns']:.0f} vs {cur['turns']:.0f}) with "
                        f"{'less' if repeat_delta <= 0 else 'similar'} re-asking. "
                        f"This is measured, not modelled.")
                cands.append({
                    "model": m["model"], "name": m["name"], "tier": m["tier"],
                    "prompts": m["prompts"], "cost_per_prompt": m["cost_per_prompt"],
                    "turns": m["turns"], "tools": m["tools"], "repeat_pct": m["repeat_pct"],
                    "savings_pct": round(save_pct, 1), "turn_ratio": round(turn_ratio, 2),
                    "repeat_delta": round(repeat_delta, 1),
                    "estimated_savings_usd": round(save, 2), "verdict": verdict, "why": why})
            if not cands:
                continue
            cands.sort(key=lambda c: (c["verdict"] != "supported", -c["estimated_savings_usd"]))
            best = cands[0] if cands[0]["verdict"] == "supported" else None
            if best:
                total_save += best["estimated_savings_usd"]
            out.append({
                "category": cat,
                "agent": cur["agent"],
                "current": {"model": cur["model"], "name": cur["name"], "prompts": cur["prompts"],
                            "cost": cur["cost"], "cost_per_prompt": cur["cost_per_prompt"],
                            "turns": cur["turns"], "tools": cur["tools"],
                            "repeat_pct": cur["repeat_pct"]},
                "candidates": cands,
                "recommended": best["model"] if best else None,
                "recommended_name": best["name"] if best else None,
                "estimated_savings_usd": best["estimated_savings_usd"] if best else 0.0,
                "verdict": best["verdict"] if best else cands[0]["verdict"],
                "why": best["why"] if best else cands[0]["why"],
                "rule": rule,
            })
        out.sort(key=lambda c: -c["estimated_savings_usd"])
        return {
            "categories": out,
            "estimated_savings_usd": round(total_save, 2),
            "min_prompts": self.MIN_PROMPTS,
            "basis": "actual",
            "method": (f"Compares what each model actually cost per prompt on the same category "
                       f"of work, using only categories where you ran both with at least "
                       f"{self.MIN_PROMPTS} prompts each. Turns and re-asked prompts are shown "
                       f"because a cheaper model that needs more of both is not cheaper. "
                       f"Nothing here is repriced or modelled."),
            "caveat": ("Your prompts were not randomly assigned to models, so a category can "
                       "differ in difficulty between them. Treat this as strong evidence for a "
                       "trial, not proof."),
        }

    def model_switch(self, f=None):
        """Per-request what-if: reprice each request on the model its work needs.

        Token counts are held constant (actual); costs on both sides are estimated at the
        configured prices. Requests whose context exceeds the target's window stay put.
        """
        w, p = self.where(f)
        rows = self.q(f"""SELECT r.model, r.is_sidechain side, r.agent_type,
            COALESCE(pr.category,'other') category, r.context_tokens ctx,
            pj.id project_id, pj.name project,
            r.prompt_id, r.session_id, r.input_tokens i, r.output_tokens o,
            r.cache_read_tokens cr, r.cache_write_5m c5, r.cache_write_1h c1, r.est_cost_usd cost
            FROM requests r LEFT JOIN prompts pr ON pr.id=r.prompt_id
            JOIN projects pj ON pj.id=r.project_id WHERE {w}""", p)
        target_of = {}
        agents, proj_prov = set(), {}
        groups, projects = {}, defaultdict(lambda: defaultdict(float))
        total = blocked = 0.0
        blocked_n = 0
        for r in rows:
            cost = r["cost"] or 0.0
            total += cost
            tier = self.pricing.tier(r["model"])
            if tier not in self.TIER_RANK:
                continue
            if r["side"]:
                is_explore = (r["agent_type"] or "").lower() == "explore"
                want, conf = ("economy", "high") if is_explore else ("balanced", "medium")
                scope = f"Subagent: {r['agent_type'] or 'general'}"
            else:
                want, conf = self.SWITCH_RULES.get(r["category"], ("balanced", "low"))
                scope = f"Prompts: {r['category'].replace('_', ' ')}"
            pj = projects[(r["project_id"], r["project"])]
            pj["cost"] += cost
            if tier == "frontier":
                pj["frontier_cost"] += cost
                proj_prov[(r["project_id"], r["project"])] = \
                    self.pricing.rates(r["model"]).get("provider", "anthropic")
            if want == "keep" or self.TIER_RANK[want] >= self.TIER_RANK[tier]:
                if want == "keep" and tier == "frontier":
                    pj["keep_cost"] += cost
                continue
            prov = self.pricing.rates(r["model"]).get("provider", "anthropic")
            if (want, prov) not in target_of:
                target_of[(want, prov)] = self._cheapest(want, prov)
            tgt = target_of[(want, prov)]
            agents.add(prov)
            if not tgt:
                continue
            win = self.pricing.context_window(tgt) or 0
            if win and (r["ctx"] or 0) > win * 0.9:
                blocked += cost
                blocked_n += 1
                continue
            alt = self.pricing.estimate(tgt, r["i"] or 0, r["o"] or 0, r["cr"] or 0,
                                        r["c5"] or 0, r["c1"] or 0)
            if alt >= cost:
                continue
            key = (scope, r["model"], tgt)
            g = groups.setdefault(key, {"scope": scope, "current_model": r["model"],
                                        "recommended_model": tgt, "confidence": conf,
                                        "requests": 0, "prompts": set(), "sessions": set(),
                                        "cost": 0.0, "alt": 0.0})
            g["requests"] += 1
            g["prompts"].add(r["prompt_id"])
            g["sessions"].add(r["session_id"])
            g["cost"] += cost
            g["alt"] += alt
            pj["savings"] += cost - alt

        out = []
        for g in groups.values():
            save = g["cost"] - g["alt"]
            if save < 0.5:
                continue
            out.append({**g, "prompts": len(g["prompts"] - {None}), "sessions": len(g["sessions"]),
                        "current_name": self.pricing.display_name(g["current_model"]),
                        "recommended_name": self.pricing.display_name(g["recommended_model"]),
                        "estimated_savings_usd": save,
                        "estimated_savings_pct": round(100 * save / g["cost"], 1) if g["cost"] else 0})
        out.sort(key=lambda x: -x["estimated_savings_usd"])

        by_conf = defaultdict(float)
        for g in out:
            by_conf[g["confidence"]] += g["estimated_savings_usd"]

        proj = []
        for (pid, name), v in projects.items():
            if v["frontier_cost"] < 1:
                continue
            balanced = self._cheapest("balanced", proj_prov.get((pid, name), "anthropic"))
            keep_pct = round(100 * v["keep_cost"] / v["frontier_cost"], 1)
            default = "keep" if keep_pct >= 50 else "switch"
            proj.append({"project": name, "project_id": pid, "cost": v["cost"],
                         "frontier_cost": v["frontier_cost"], "keep_pct": keep_pct,
                         "estimated_savings_usd": v["savings"],
                         "suggested_default": (self.pricing.display_name(balanced)
                                               if default == "switch" and balanced else "Keep current"),
                         "why": (f"{keep_pct}% of frontier spend here is debugging/architecture/"
                                 f"planning/review, which benefits from the top model."
                                 if default == "keep" else
                                 f"Only {keep_pct}% of frontier spend here is reasoning-heavy work. "
                                 f"Make {self.pricing.display_name(balanced)} the default and "
                                 f"switch up with /model only for hard problems.")})
        proj.sort(key=lambda x: -x["estimated_savings_usd"])

        return {
            "total_cost_usd": total,
            "switches": out,
            "projects": proj,
            "savings_by_confidence": dict(by_conf),
            "estimated_savings_usd": sum(by_conf.values()),
            "safe_savings_usd": by_conf.get("high", 0) + by_conf.get("medium", 0),
            "blocked_by_context_usd": blocked, "blocked_by_context_requests": blocked_n,
            "rules": {k: v[0] for k, v in self.SWITCH_RULES.items()},
            "how": {"session": "/model <name> in Claude Code",
                    "project": '"model": "<name>" in <repo>/.claude/settings.json',
                    "subagent": "model: haiku (or sonnet) in the agent's frontmatter in .claude/agents/"},
            "how_by_agent": self.SWITCH_HOW_BY_AGENT,
            "providers": sorted(agents),
            "caveat": "Same token counts repriced on the cheaper model. Output quality and any "
                      "extra turns a cheaper model might need are not modelled. Try it on a "
                      "sample of work before switching everything.",
            "basis": "recommendation",
        }

    def recommendations(self, f=None):
        w, p = self.where(f)
        recs = []

        # Frontier work a cheaper model could have done. Candidates are always from the
        # same vendor: an agent can only switch within its own family (Codex can't run
        # Haiku), so mixed frontier spend is split per vendor before anything is compared.
        # Each recommendation offers the ladder — one step down (balanced) and the floor
        # (economy) — priced separately, because that trade-off is the user's to make.
        frontier_by_provider = defaultdict(list)
        for m, v in self.pricing.models.items():
            if v.get("tier") == "frontier":
                frontier_by_provider[v.get("provider", "anthropic")].append(m)

        for prov, models in sorted(frontier_by_provider.items()):
            cands = []
            for tier in ("balanced", "economy"):
                c = self._cheapest(tier, prov)
                if c and c not in cands:
                    cands.append(c)
            if not cands:
                continue           # this vendor exposes nothing cheaper to move to
            ph = ",".join("?" * len(models))
            rows = self.q(f"""SELECT pr.category, COUNT(DISTINCT pr.id) prompts,
                              GROUP_CONCAT(DISTINCT r.model) mods,
                              SUM(r.est_cost_usd) cost, SUM(r.input_tokens) i,
                              SUM(r.output_tokens) o, SUM(r.cache_read_tokens) cr,
                              SUM(r.cache_write_5m) c5, SUM(r.cache_write_1h) c1
                              FROM prompts pr JOIN requests r ON r.prompt_id=pr.id
                              WHERE {w} AND r.model IN ({ph})
                              GROUP BY pr.category HAVING prompts >= 3 AND cost > 0.5
                              ORDER BY cost DESC""", p + models)
            for r in rows:
                # The same rules the Model switch dashboard applies, so the two pages can
                # never contradict each other: work the rules say to keep on a frontier
                # model is not offered a downgrade at all.
                target, conf = self.SWITCH_RULES.get(r["category"], ("balanced", "low"))
                if target == "keep":
                    continue
                alts = []
                for m in cands:
                    alt = self.pricing.estimate(m, r["i"], r["o"], r["cr"], r["c5"], r["c1"])
                    if alt >= r["cost"] * 0.9:
                        continue   # too close to the current cost to be worth the quality risk
                    alts.append({
                        "model": m, "name": self.pricing.display_name(m),
                        "tier": self.pricing.tier(m),
                        "estimated_cost_usd": alt,
                        "estimated_savings_usd": r["cost"] - alt,
                        "estimated_savings_pct": round(100.0 * (r["cost"] - alt) / r["cost"], 1),
                    })
                if not alts:
                    continue
                # Safest step first: balanced before economy, so the ladder reads as
                # increasing saving and increasing risk.
                alts.sort(key=lambda a: -self.TIER_RANK.get(a["tier"], 0))
                for a in alts:
                    a["suggested"] = a["tier"] == target
                # The headline is the tier the rules actually recommend for this kind of
                # work, not simply the smallest step; the rest stay on offer below it.
                head = next((a for a in alts if a["suggested"]), alts[0])
                agent = self.SWITCH_HOW_BY_AGENT.get(prov, {}).get("agent", prov)
                recs.append({
                    "type": "model_downgrade",
                    "confidence": conf or "low",
                    "title": f"Consider a cheaper {agent} model for '{r['category']}' work",
                    "current_model": ", ".join(self.pricing.display_name(m)
                                               for m in (r["mods"] or "").split(",") if m),
                    "recommended_model": head["name"],
                    "provider": prov,
                    "agent": agent,
                    "alternatives": alts,
                    "scope": f"{r['prompts']} prompts categorized as {r['category']}",
                    "actual_cost_usd": r["cost"],
                    "estimated_alternative_cost_usd": head["estimated_cost_usd"],
                    "estimated_savings_usd": head["estimated_savings_usd"],
                    "estimated_savings_pct": head["estimated_savings_pct"],
                    "caveat": f"Both options stay inside {agent}, so this is a setting change, not "
                              "a change of agent. Assumes identical token usage on the cheaper "
                              "model. Output quality is not modelled — validate on a sample "
                              "before switching.",
                    "basis": "recommendation",
                })
        # Biggest opportunity first, now that several vendors can each contribute one.
        recs.sort(key=lambda r: -r["estimated_savings_usd"])

        eff = self.efficiency(f)
        c = eff["cache"]
        if c["reads"] and c["estimated_savings_usd"] > 0:
            recs.append({
                "type": "cache_working", "confidence": "high",
                "title": "Prompt caching is already saving money — keep sessions long-lived",
                "actual_cost_usd": c["cost_with_cache"],
                "estimated_alternative_cost_usd": c["cost_without_cache"],
                "estimated_savings_usd": c["estimated_savings_usd"],
                "estimated_savings_pct": c["savings_pct"],
                "caveat": "Savings vs a hypothetical no-cache baseline at configured list prices.",
                "basis": "recommendation",
            })

        ctx = self.context_analysis(f)
        if ctx["large_context_cost_pct"] > 15:
            recs.append({
                "type": "context_reduction", "confidence": "medium",
                "title": f"{ctx['large_context_cost_pct']}% of spend comes from >"
                         f"{ctx['threshold']//1000}K-context requests",
                "scope": f"{ctx['large_context_requests']:,} requests",
                "actual_cost_usd": ctx["large_context_cost"],
                "estimated_savings_usd": ctx["large_context_cost"] * 0.2,
                "estimated_savings_pct": 20.0,
                "caveat": "Assumes a 20% context reduction is achievable via /compact and tighter "
                          "file scoping. Not a measured saving.",
                "basis": "recommendation",
            })
        recs.sort(key=lambda r: -(r.get("estimated_savings_usd") or 0))
        return {"recommendations": recs,
                "total_estimated_savings_usd": sum(r.get("estimated_savings_usd") or 0
                                                   for r in recs if r["type"] != "cache_working"),
                "basis": "recommendation"}

    # ---------------- forecast ----------------
    def forecast(self, f=None):
        w, p = self.where(f)
        bp = self.billing_period()
        rows = self.q(f"""SELECT r.day, SUM(r.est_cost_usd) cost, SUM(r.billable_tokens) tokens
                          FROM requests r WHERE {w} AND r.day <> '' GROUP BY 1 ORDER BY 1""", p)
        if not rows:
            return {"available": False, "message": "No usage in the selected range."}
        recent = rows[-14:]
        costs = [r["cost"] for r in recent]
        mean = statistics.fmean(costs)
        sd = statistics.pstdev(costs) if len(costs) > 1 else 0.0
        in_period = [r for r in rows if bp["start"] <= r["day"] <= bp["end"]]
        used = sum(r["cost"] for r in in_period)
        used_tok = sum(r["tokens"] for r in in_period)
        left = bp["remaining_days"]

        def band(rate):
            return {"daily_rate": rate, "end_of_period_cost": used + rate * left}

        scenarios = {
            "conservative": band(max(mean - sd, 0)),
            "expected": band(mean),
            "high": band(mean + sd),
        }
        tok_mean = statistics.fmean([r["tokens"] for r in recent])
        today_rows = [r for r in rows if r["day"] == bp["today"]]
        hours = max(datetime.now(timezone.utc).hour, 1)
        eod = (today_rows[0]["cost"] / hours * 24) if today_rows else mean

        wk_start = (_d(bp["today"]) - timedelta(days=_d(bp["today"]).weekday())).isoformat()
        wk_used = sum(r["cost"] for r in rows if r["day"] >= wk_start)
        wk_left = 6 - _d(bp["today"]).weekday()

        out = {
            "available": True,
            "method": "14-day mean daily spend with ±1 standard deviation bands",
            "sample_days": len(recent),
            "daily_mean": mean, "daily_stdev": sd,
            "period_used": used, "period_used_tokens": used_tok,
            "remaining_days": left,
            "scenarios": scenarios,
            "end_of_day_cost": eod,
            "end_of_week_cost": wk_used + mean * max(wk_left, 0),
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
        days = self.q(f"""SELECT r.day, SUM(r.est_cost_usd) cost, SUM(r.billable_tokens) tokens,
                          COUNT(*) requests FROM requests r WHERE {w} AND r.day<>''
                          GROUP BY 1 ORDER BY 1""", p)
        if len(days) >= 5:
            vals = [d["cost"] for d in days]
            mean, sd = statistics.fmean(vals), (statistics.pstdev(vals) or 1e-9)
            for d in days:
                z = (d["cost"] - mean) / sd
                ratio = d["cost"] / mean if mean else 0
                if z >= cfg["daily_zscore"] and ratio >= cfg["daily_ratio"]:
                    found.append({
                        "severity": "high", "type": "daily_spike", "date": d["day"],
                        "title": f"{d['day']} spend was {ratio:.1f}x your daily average",
                        "detail": f"${d['cost']:,.2f} vs a ${mean:,.2f} daily mean (z={z:.1f}).",
                        "metric_value": d["cost"], "baseline": mean, "ratio": round(ratio, 2),
                        "drilldown": {"filter": {"start": d["day"], "end": d["day"]}},
                        "basis": "estimated",
                    })
        sess = self.sessions(f, limit=100000, order="cost")
        if len(sess) >= 5:
            vals = [s["tokens"] for s in sess]
            mean = statistics.fmean(vals) or 1
            for s in sess[:40]:
                ratio = s["tokens"] / mean
                if ratio >= cfg["session_ratio"]:
                    found.append({
                        "severity": "medium", "type": "session_outlier",
                        "title": f"Session consumed {ratio:.1f}x the average session tokens",
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
        if chr_ is None:
            dim("Cache efficiency", 50, "No cache activity in range", 1.0)
        else:
            dim("Cache efficiency", chr_ * 100,
                f"{chr_*100:.1f}% of cache tokens were reads (reuse) rather than writes.", 1.2)

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
            # Name the models. A saving is meaningless without the swap it assumes, and
            # the options are what the reader actually has to choose between.
            opts = " or ".join(f"{a['name']} (~${a['estimated_savings_usd']:,.0f}, "
                               f"{a['estimated_savings_pct']}%)" for a in r.get("alternatives", []))
            swap = f"{r['current_model']} → {opts}. " if opts else ""
            actions.append({"priority": 3, "kind": "recommendation", "text": r["title"],
                            "detail": f"{swap}Estimated saving ~${r['estimated_savings_usd']:,.2f} "
                                      f"({r['estimated_savings_pct']}%) on the suggested option. "
                                      f"{r['caveat']}",
                            "basis": "recommendation"})
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
        savings = recs["total_estimated_savings_usd"]
        return {
            "question": "What should I do today?",
            "actions": actions[:6],
            "estimated_savings_range_usd": [round(savings * 0.6, 2), round(savings, 2)],
            "generated_from": "Live dashboard data for the current filter selection.",
            "basis": "mixed: see per-item basis",
        }

    # ---------------- prompt-level advisor ----------------
    def prompt_advisor(self, p):
        """Deterministic, evidence-based analysis of one prompt. Estimates only."""
        reasons, suggestions = [], []
        chars = p.get("char_len") or 0
        ctx = p.get("max_context_tokens") or 0
        tools = p.get("tool_calls") or 0
        out = p.get("output_tokens") or 0
        tot = p.get("billable_tokens") or 0
        reduction = 0.0
        if chars > 4000:
            reasons.append(f"The prompt itself is {chars:,} characters, which is cached and "
                           f"re-sent on every follow-up turn.")
            suggestions.append("Move long pasted content into a file and reference the path.")
            reduction += 0.10
        if ctx > 150000:
            reasons.append(f"It ran with up to {ctx:,} context tokens per request.")
            suggestions.append("Run /compact or start a fresh session before a task this large.")
            reduction += 0.25
        if tools > 40:
            reasons.append(f"It triggered {tools} tool calls; each one re-sends the conversation.")
            suggestions.append("Split into smaller, explicitly scoped sub-tasks.")
            reduction += 0.15
        if tot and out / tot < 0.01:
            reasons.append(f"Only {100*out/tot:.2f}% of the tokens were output — most of the "
                           f"cost was re-reading context.")
            suggestions.append("Narrow the files and history in scope before asking.")
            reduction += 0.10
        models = (p.get("models") or "")
        if "opus" in models and out < 400:
            reasons.append("A frontier-tier model produced a short answer.")
            suggestions.append("Route short turns to a cheaper model tier.")
            reduction += 0.20
        if not reasons:
            return {"available": False,
                    "message": "No cost-driver pattern detected for this prompt."}
        reduction = min(reduction, 0.6)
        return {
            "available": True,
            "why_expensive": reasons,
            "suggestions": suggestions,
            "estimated_token_reduction_pct": round(reduction * 100),
            "estimated_cost_reduction_pct": round(reduction * 100 * 0.85),
            "estimated_cost_reduction_usd": round((p.get("est_cost_usd") or 0) * reduction * 0.85, 2),
            "disclaimer": "ESTIMATE from structural heuristics. Not a measured saving and not a "
                          "guarantee of equivalent output quality.",
            "basis": "recommendation",
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
            "unavailable_label": UNAVAILABLE,
        }
