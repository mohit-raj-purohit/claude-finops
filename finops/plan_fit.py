"""Does the plan you are on fit how you use it?

Everything it reads is already on this machine:
- the plan, from ~/.claude.json (actual);
- the 5-hour and weekly percentages the Claude desktop app samples (plan_history);
- the last /usage read (limits.cached), for the windows you are in now;
- the warehouse, for the tokens this machine spent inside those windows.

The one assumption is the published plan ratio: Max 5x gives 5x Pro's usage and
Max 20x gives 20x. A percentage on one plan is rescaled by that ratio to say what it
would have been on another. Anthropic does not publish the limits in tokens, so no
token allowance is claimed; tokens shown are what was spent in the window.
"""
import re
import statistics
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:          # pragma: no cover
    ZoneInfo = None

PLANS = [("Pro", 1, 20), ("Max 5x", 5, 100), ("Max 20x", 20, 200)]   # name, usage vs Pro, USD/month
WEEK_S, FIVE_H_S = 7 * 86400, 5 * 3600
FIT_WEEKLY = 80      # a smaller plan fits only if your heaviest week stays under this...
FIT_FIVE_HOUR = 100  # ...and nine in ten of your 5-hour windows stay under this
MIN_WEEKS = 2        # fewer full weeks of history than this and the verdict says it is early

# "Oct 29 at 2:30pm (Asia/Calcutta)" / "2:30pm (Asia/Calcutta)" / "Nov 4 at 6:30am"
_RESET = re.compile(r"(?:(?P<mon>[A-Z][a-z]{2})\s+(?P<day>\d{1,2})\s+at\s+)?"
                    r"(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm)"
                    r"(?:\s*\((?P<tz>[^)]+)\))?", re.I)
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def _ts(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def reset_at(text, ref):
    """The reset time /usage printed, as a UTC timestamp. `ref` is when it was read: the
    reset is the first matching moment after it (the year and, for a bare time, the day
    are not printed)."""
    m = _RESET.search(text or "")
    if not m:
        return None
    tz = timezone.utc
    if m.group("tz") and ZoneInfo:
        try:
            tz = ZoneInfo(m.group("tz"))
        except Exception:
            pass
    h = int(m.group("h")) % 12 + (12 if m.group("ap").lower() == "pm" else 0)
    mi = int(m.group("m") or 0)
    base = datetime.fromtimestamp(ref, tz)
    if m.group("mon"):
        mon = _MONTHS.index(m.group("mon").lower()) + 1 if m.group("mon").lower() in _MONTHS else None
        if not mon:
            return None
        for y in (base.year, base.year + 1):
            try:
                d = datetime(y, mon, int(m.group("day")), h, mi, tzinfo=tz)
            except ValueError:
                return None
            if d.timestamp() > ref - 60:
                return d.timestamp()
        return None
    d = base.replace(hour=h, minute=mi, second=0, microsecond=0)
    if d.timestamp() <= ref - 60:
        d += timedelta(days=1)
    return d.timestamp()


def windows(series, key, span_s):
    """Split one limit's samples into its reset windows; each window's start, end and peak.
    A window ends where the percentage falls (the limit reset) or once it would have run
    longer than the limit's own span."""
    out, prev = [], None
    for r in series:
        v = r.get(key)
        if v is None:
            continue
        t = _ts(r["t"])
        cur = out[-1] if out else None
        if cur is None or v < prev - 1 or t - cur["start"] >= span_s:
            out.append({"start": t, "end": t, "peak": v})
        else:
            cur["end"], cur["peak"] = t, max(cur["peak"], v)
        prev = v
    return out


def _p90(values):
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(round(0.9 * (len(s) - 1))))]


def _mult(plan):
    return next((m for n, m, _ in PLANS if n == plan), None)


def _days_into_week(pct):
    """At an even pace, how many days into a week a limit reached `pct` of it would run out."""
    return 7 * 100.0 / pct if pct > 100 else None


def _spent(a, start, end, model=None):
    sql = ("SELECT COUNT(*) requests, COALESCE(SUM(billable_tokens),0) tokens,"
           " COALESCE(SUM(cache_read_tokens),0) cache_read, COALESCE(SUM(est_cost_usd),0) cost"
           " FROM requests WHERE ts >= ? AND ts < ?")
    params = [_iso(start), _iso(end)]
    if model:
        sql += " AND LOWER(model) LIKE ?"
        params.append(f"%{model.lower()}%")
    return a.one(sql, tuple(params))


def current_windows(a, usage, now):
    """The windows /usage last reported, each with what this machine spent inside it."""
    if not usage:
        return []
    out, read_at = [], usage.get("fetched_at") or now
    for l in usage.get("limits") or []:
        label = l.get("label") or ""
        weekly = "week" in label.lower()
        span = WEEK_S if weekly else FIVE_H_S
        end = reset_at(l.get("resets"), read_at)
        row = {"label": label, "pct": l.get("pct"), "resets": l.get("resets"),
               "kind": "weekly" if weekly else "five_hour"}
        if end:
            start = end - span
            m = re.search(r"\(([^)]+)\)", label)
            model = m.group(1) if m and m.group(1).lower() != "all models" else None
            s = _spent(a, start, min(end, now), model)
            pct = l.get("pct") or 0
            row.update({"start": _iso(start), "reset": _iso(end), "reset_passed": end <= now,
                        "tokens": s["tokens"], "cache_read_tokens": s["cache_read"],
                        "requests": s["requests"], "est_cost_usd": s["cost"],
                        # only from a reading big enough that rounding to a whole % isn't most of it
                        "tokens_per_pct": round(s["tokens"] / pct) if pct >= 5 and s["tokens"] else None})
        out.append(row)
    return out


def pattern(a, now, days=28):
    """How the last four weeks were used on this machine (warehouse, actual tokens)."""
    since = _iso(now - days * 86400)
    rows = a.q("SELECT day, COALESCE(SUM(billable_tokens),0) tokens, COALESCE(SUM(est_cost_usd),0) cost"
               " FROM requests WHERE ts >= ? GROUP BY day", (since,))
    hours = a.q("SELECT hour, COALESCE(SUM(billable_tokens),0) tokens FROM requests"
                " WHERE ts >= ? GROUP BY hour ORDER BY tokens DESC LIMIT 3", (since,))
    total = sum(r["tokens"] for r in rows)
    wd = {}
    for r in rows:
        try:
            k = datetime.fromisoformat(r["day"]).strftime("%A")
        except (TypeError, ValueError):
            continue
        wd[k] = wd.get(k, 0) + r["tokens"]
    return {"days": days, "active_days": len(rows), "tokens": total,
            "est_cost_usd": sum(r["cost"] for r in rows),
            "tokens_per_active_day": round(total / len(rows)) if rows else 0,
            "busiest_weekday": max(wd, key=wd.get) if wd else None,
            "busiest_hours": [h["hour"] for h in hours if h["hour"] is not None]}


def plan_fit(a, plan, history, usage, now=None):
    """The whole recommendation. `plan` is detect_account()'s plan ("Max 5x"...),
    `history` plan_history.history(), `usage` limits.cached()."""
    import time
    now = now or time.time()
    out = {"plan": plan or None, "plans": [], "current": current_windows(a, usage, now),
           "usage_read_at": _iso(usage["fetched_at"]) if usage and usage.get("fetched_at") else None,
           "pattern": pattern(a, now), "basis": "recommendation",
           "note": "The desktop app only samples while it is open, so peaks are at least this high. "
                   "Plans are compared with Anthropic's published ratio (Max 5x = 5x Pro, Max 20x = 20x Pro)."}
    cur_mult = _mult(plan)
    if not history or not history.get("ok"):
        out.update(verdict="unknown", reason=(history or {}).get("reason")
                   or "No plan-limit history on this machine.")
        return out
    series = history["series"]
    weeks = windows(series, "weekly", WEEK_S)
    fives = [w for w in windows(series, "five_hour", FIVE_H_S) if w["peak"] > 0]
    # A week whose samples never rose above 0 was not observed (the app was closed), not idle
    # for sure, so it counts towards neither the median nor the full weeks.
    seen = [w for w in weeks if w["peak"] > 0]
    done = [w for w in seen if now - w["start"] >= WEEK_S]
    week_peaks = [w["peak"] for w in done] or [w["peak"] for w in seen]
    five_peaks = [w["peak"] for w in fives]
    weekly_max = max(w["peak"] for w in weeks) if weeks else 0
    five_p90 = _p90(five_peaks) or 0
    span_weeks = max((_ts(series[-1]["t"]) - _ts(series[0]["t"])) / WEEK_S, 1 / 7)
    out["history"] = {
        "first": series[0]["t"], "last": series[-1]["t"], "samples": len(series),
        "weeks": len(seen), "full_weeks": len(done),
        "weekly_peaks": [{"start": _iso(w["start"]), "peak": w["peak"],
                          "in_progress": now - w["start"] < WEEK_S} for w in seen],
        "weekly_max": weekly_max,
        "weekly_median": statistics.median(week_peaks) if week_peaks else None,
        "five_hour_windows": len(fives),
        "five_hour_windows_per_week": round(len(fives) / span_weeks, 1),
        "five_hour_median": statistics.median(five_peaks) if five_peaks else None,
        "five_hour_p90": five_p90, "five_hour_max": max(five_peaks) if five_peaks else None,
        "five_hour_ge90": sum(p >= 90 for p in five_peaks),
        "five_hour_hit100": sum(p >= 100 for p in five_peaks),
        "weekly_ge90": sum(w["peak"] >= 90 for w in weeks),
        "weekly_hit100": sum(w["peak"] >= 100 for w in weeks),
    }
    if not cur_mult:
        out.update(verdict="unknown", reason=(
            f"Your plan ({plan}) is not Pro or Max, so it can't be compared with them."
            if plan else "Your plan isn't recorded in ~/.claude.json, so there is nothing to compare."))
        return out
    for name, mult, price in PLANS:
        f = cur_mult / mult
        wk, fh = weekly_max * f, five_p90 * f
        out["plans"].append({
            "plan": name, "price_usd": price, "current": mult == cur_mult,
            "weekly_peak_pct": round(wk), "five_hour_p90_pct": round(fh),
            "runs_out_day": (round(_days_into_week(wk), 1) if _days_into_week(wk) else None),
            "fits": wk <= FIT_WEEKLY and fh <= FIT_FIVE_HOUR})
    h = out["history"]
    cheaper = [p for p in out["plans"] if p["price_usd"] < _price(plan) and p["fits"]]
    bigger = [p for p in out["plans"] if p["price_usd"] > _price(plan)]
    pressed = h["weekly_hit100"] or h["weekly_ge90"] >= 2 or h["five_hour_hit100"] >= 3
    early = h["full_weeks"] < MIN_WEEKS
    out["confidence"] = "low" if early else "medium" if h["full_weeks"] < 4 else "high"
    if cheaper:
        best = min(cheaper, key=lambda p: p["price_usd"])
        out.update(verdict="downgrade", suggest=best["plan"],
                   saving_usd_month=_price(plan) - best["price_usd"],
                   reason=(f"Your heaviest week used {weekly_max:.0f}% of {plan}, and nine in ten of your "
                           f"5-hour windows stayed under {five_p90:.0f}%. On {best['plan']} that is about "
                           f"{best['weekly_peak_pct']}% of the week and {best['five_hour_p90_pct']}% of a "
                           f"5-hour window, which still leaves room."))
    elif pressed and bigger:
        out.update(verdict="upgrade", suggest=bigger[0]["plan"],
                   reason=(f"You reached the limit: {h['weekly_ge90']} week(s) at 90%+ and "
                           f"{h['five_hour_hit100']} five-hour window(s) at 100%. {bigger[0]['plan']} "
                           f"would put your heaviest week near "
                           f"{round(weekly_max * cur_mult / _mult(bigger[0]['plan']))}%."))
    else:
        lower = [p for p in out["plans"] if p["price_usd"] < _price(plan)]
        nxt = max(lower, key=lambda p: p["price_usd"]) if lower else None
        why = ""
        if nxt:
            why = (f" On {nxt['plan']} your heaviest week would be about {nxt['weekly_peak_pct']}% "
                   f"of the limit" + (f", so it would run out around day {nxt['runs_out_day']:.0f} of 7"
                                      if nxt["runs_out_day"] else "")
                   + (f", and your busiest 5-hour windows would reach {nxt['five_hour_p90_pct']}%."
                      if nxt["five_hour_p90_pct"] > FIT_FIVE_HOUR else "."))
        out.update(verdict="stay", suggest=plan,
                   reason=(f"Your heaviest week used {weekly_max:.0f}% of {plan} and nine in ten 5-hour "
                           f"windows stayed under {five_p90:.0f}%.{why}"))
    if early:
        out["reason"] += (f" Only {h['full_weeks']} full week(s) of history so far, so treat this as early.")
    out["advice"] = advice(out, plan, now)
    return out


UNDER_USED = 50      # a typical week below this leaves half the plan unused


def advice(fit, plan, now):
    """The plain answer: move or not, are you getting the plan's worth, and how to use what is left."""
    h, p = fit["history"], fit["pattern"]
    by = {x["plan"]: x for x in fit["plans"]}
    cur, price = by.get(plan), _price(plan)
    lower = [x for x in fit["plans"] if x["price_usd"] < price]
    upper = [x for x in fit["plans"] if x["price_usd"] > price]
    down = max(lower, key=lambda x: x["price_usd"]) if lower else None
    up = min(upper, key=lambda x: x["price_usd"]) if upper else None
    decide = []
    if fit["verdict"] == "downgrade":
        decide.append(f"Move down to {fit['suggest']}: about ${fit['saving_usd_month']}/month less, and your "
                      f"heaviest week would still be about {by[fit['suggest']]['weekly_peak_pct']}% of it.")
    elif down:
        decide.append(f"Don't move down to {down['plan']}: your heaviest week would be about "
                      f"{down['weekly_peak_pct']}% of it" + (f", so it would run out around day "
                      f"{down['runs_out_day']:.0f} of 7." if down["runs_out_day"] else "."))
    if fit["verdict"] == "upgrade":
        decide.append(f"Move up to {fit['suggest']}: you are hitting {plan}'s limits.")
    elif up:
        decide.append(f"Don't move up to {up['plan']}: you used at most {cur['weekly_peak_pct']}% of {plan}, "
                      f"so {up['plan']} would sit at about {up['weekly_peak_pct']}%.")
    decide.append({"downgrade": f"Switch to {fit.get('suggest')}.", "upgrade": f"Switch to {fit.get('suggest')}."}
                  .get(fit["verdict"], f"Stay on {plan}."))

    month = p["est_cost_usd"] * 30 / p["days"] if p["days"] else 0
    value = {"paid_usd": price, "api_equivalent_usd_month": round(month, 2),
             "multiple": round(month / price, 1) if price else None}

    typical = h["weekly_median"] or 0
    status = "tight" if typical > FIT_WEEKLY else "well used" if typical >= UNDER_USED else "under-used"
    util = {"typical_week_pct": typical, "heaviest_week_pct": h["weekly_max"],
            "unused_pct": max(0, round(100 - typical)), "status": status}

    week = next((w for w in fit["current"] if w["kind"] == "weekly" and "all models" in w["label"].lower()
                 and w.get("reset") and not w.get("reset_passed")), None)
    this_week = None
    if week and week.get("pct") is not None:
        left = (_ts(week["reset"]) - now) / 86400
        gone = 7 - left
        this_week = {"pct": week["pct"], "days_left": round(left, 1), "reset": week["reset"],
                     "projected_pct": round(week["pct"] * 7 / gone) if gone >= 1 else None}

    tips = []
    if status == "under-used":
        per = typical / h["five_hour_windows_per_week"] if h["five_hour_windows_per_week"] else 0
        if per:
            extra = int((FIT_WEEKLY - typical) // per)
            tips.append(f"A busy 5-hour session costs you about {int(per + 0.5)}% of the week, and you run about "
                        f"{h['five_hour_windows_per_week']:g} a week. There is room for about {extra} more "
                        f"a week before the weekly limit gets close.")
        if (h["five_hour_median"] or 0) < 25:
            tips.append(f"A typical 5-hour window peaks at only {int(h['five_hour_median'] + 0.5)}%. Run two or three "
                        "sessions side by side (separate worktrees, or subagents for review and tests) instead "
                        "of one after another: they share the same window.")
        tips.append("Unused weekly limit does not carry over. Save the big jobs you keep putting off "
                    "(refactors, test coverage, docs, code reviews, dependency upgrades) and run them on "
                    "light days, before the week resets.")
        if this_week and this_week["projected_pct"] is not None and this_week["projected_pct"] < FIT_WEEKLY:
            tips.append(f"This week: {this_week['pct']:.0f}% used with {this_week['days_left']:.1f} days left. "
                        f"At this pace it ends near {this_week['projected_pct']}%, so about "
                        f"{100 - this_week['projected_pct']}% will reset unused.")
    elif status == "tight":
        tips.append("Your typical week is close to the limit. Compact or hand over long sessions, and give "
                    "subagents a cheaper model, before thinking about a bigger plan.")
    return {"decision": decide, "value": value, "utilization": util, "this_week": this_week, "tips": tips}


def _price(plan):
    return next((p for n, _, p in PLANS if n == plan), 0)
