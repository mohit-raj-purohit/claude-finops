"""Subagent models: which subagent types could run on a cheaper model, measured per run.

The removed Model switch view repriced past requests at another model's rates. That
ignored extra turns and the per-model prompt cache, so its savings were noise. A
subagent run is different on both counts: it starts its own cache, and Claude Code
lets you choose its model. So everything here is measured per run, from runs that
actually happened on each model:

  * a run is one agent_id; its cost, turns, output and peak context are summed or
    maxed over its requests;
  * types are compared on the median $/run of each model they ran on;
  * no savings total is produced. The only money figures are spend and $/run.

The experiment sets CLAUDE_CODE_SUBAGENT_MODEL in ~/.claude/settings.json for a while
and compares runs before and after, which is the only way to see what a switch does
on your own work rather than on whatever tasks each model happened to get.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from statistics import median

from . import integrate
from .paths import LOCAL_SETTINGS_PATH

ENV_VAR = "CLAUDE_CODE_SUBAGENT_MODEL"
ALIASES = ("haiku", "sonnet", "opus", "fable")
DEFAULT_MIN_RUNS = 5
HISTORY_KEEP = 10
BEFORE_CAP_DAYS = 30


# ------------------------------------------------------------------ runs ----

def _alias(model):
    """The alias Claude Code accepts for a model id, or None."""
    m = (model or "").lower()
    return next((a for a in ALIASES if a in m), None)


def _priced(pricing, model):
    return pricing.tier(model) != "unpriced"


def load_runs(a, f, extra="", params=()):
    """One dict per subagent run: type, dominant model, cost, turns, output, context, ts.

    A run that used several models is filed under the one that cost it the most:
    that is the model whose price the run actually paid.
    """
    w, p = a.where(f)
    rows = a.q(f"""SELECT r.agent_id, r.agent_type, r.model, SUM(r.est_cost_usd) cost,
                   COUNT(*) turns, SUM(r.output_tokens) output, MAX(r.context_tokens) context,
                   SUM(r.billable_tokens) tokens, MIN(r.ts) ts
                   FROM requests r
                   WHERE {w} AND r.is_sidechain=1 AND r.agent='claude' AND r.agent_id IS NOT NULL
                   {extra}
                   GROUP BY r.agent_id, r.model""", list(p) + list(params))
    runs = {}
    for r in rows:
        cur = runs.get(r["agent_id"])
        if cur is None:
            runs[r["agent_id"]] = cur = {
                "agent_id": r["agent_id"], "type": r["agent_type"] or "(unknown)",
                "model": r["model"], "cost": 0.0, "turns": 0, "output": 0, "context": 0,
                "ts": r["ts"], "_top": (-1.0, -1)}
        cost = r["cost"] or 0.0
        cur["cost"] += cost
        cur["turns"] += r["turns"] or 0
        cur["output"] += r["output"] or 0
        cur["context"] = max(cur["context"], r["context"] or 0)
        cur["ts"] = min(cur["ts"] or r["ts"], r["ts"] or cur["ts"])
        if (cost, r["turns"] or 0) > cur["_top"]:
            cur["_top"] = (cost, r["turns"] or 0)
            cur["model"] = r["model"]
    out = list(runs.values())
    for r in out:
        del r["_top"]
    return out


def _cell(pricing, model, runs):
    return {"model": model, "name": pricing.display_name(model), "alias": _alias(model),
            "priced": _priced(pricing, model), "price_out": (pricing.rates(model) or {}).get("output"),
            "runs": len(runs), "cost": sum(r["cost"] for r in runs),
            "med_cost": median(r["cost"] for r in runs),
            "med_turns": median(r["turns"] for r in runs),
            "med_output": median(r["output"] for r in runs),
            "med_context": median(r["context"] for r in runs)}


# ------------------------------------------------------------- suggestions ----

def suggest(type_name, rows, min_runs, custom):
    """One suggestion for a subagent type. `rows` are its per-model cells."""
    if type_name == "fork":
        return {"kind": "fork", "med_context": median(c["med_context"] for c in rows)}
    priced = [c for c in rows if c["priced"]]
    if not priced:
        return {"kind": "unpriced", "models": [c["name"] for c in rows]}
    main = max(priced, key=lambda c: (c["cost"], c["runs"]))
    if all((c["price_out"] or 0) >= (main["price_out"] or 0) for c in priced):
        return {"kind": "cheapest", "model": main["name"]}
    better = [c for c in priced
              if (c["price_out"] or 0) < (main["price_out"] or 0)
              and c["runs"] >= min_runs and main["runs"] >= min_runs
              and c["med_cost"] < main["med_cost"]]
    if not better:
        return {"kind": "not_enough", "model": main["name"], "min_runs": min_runs}
    to = min(better, key=lambda c: c["med_cost"])
    s = {"kind": "switch", "from": main["name"], "from_med": main["med_cost"], "from_runs": main["runs"],
         "to": to["name"], "to_med": to["med_cost"], "to_runs": to["runs"],
         "alias": to["alias"] or to["model"]}
    agent = custom.get(type_name)
    if agent:
        s["fix"] = {"kind": "file", "path": agent["path"], "line": f"model: {s['alias']}",
                    "current": agent.get("model")}
    else:
        s["fix"] = {"kind": "experiment", "model": s["alias"]}
    return s


def _frontmatter(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read(8192)
    except OSError:
        return {}
    m = re.match(r"\A---\s*\n(.*?)\n---", text, re.S)
    out = {}
    for line in (m.group(1).splitlines() if m else []):
        k, sep, v = line.partition(":")
        if sep and k.strip() in ("name", "model"):
            out[k.strip()] = v.strip().strip("'\"")
    return out


def custom_agents(project_paths, home=None):
    """name -> {path, model} for agent files in ~/.claude/agents and each project's
    .claude/agents. A project's own file wins over the user-level one, as in Claude Code."""
    home = home or os.path.expanduser("~")
    dirs = [os.path.join(home, ".claude", "agents")] + \
           [os.path.join(p, ".claude", "agents") for p in project_paths if p]
    found = {}
    for d in dirs:
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for fn in names:
            if not fn.endswith(".md"):
                continue
            path = os.path.join(d, fn)
            fm = _frontmatter(path)
            found[fm.get("name") or fn[:-3]] = {"path": path, "model": fm.get("model")}
    return found


# -------------------------------------------------------------- experiment ----

class SettingsError(Exception):
    """Claude Code's settings.json exists but cannot be read; nothing is written."""


def _claude_settings():
    path = integrate.SETTINGS
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        raise SettingsError(f"Couldn't read {path}: {e}. Nothing was changed.")
    if not isinstance(data, dict):
        raise SettingsError(f"{path} is not a JSON object. Nothing was changed.")
    return data


def _env_value(s):
    env = s.get("env")
    return env.get(ENV_VAR) if isinstance(env, dict) else None


def _local():
    if not os.path.exists(LOCAL_SETTINGS_PATH):
        return {}
    with open(LOCAL_SETTINGS_PATH) as fh:
        data = json.load(fh)
    return data if isinstance(data, dict) else {}


def _state():
    st = _local().get("subagent_experiment")
    st = st if isinstance(st, dict) else {}
    return {"active": st.get("active") if isinstance(st.get("active"), dict) else None,
            "history": [h for h in (st.get("history") or []) if isinstance(h, dict)]}


def _save_state(st):
    local = _local()
    local["subagent_experiment"] = st
    os.makedirs(os.path.dirname(LOCAL_SETTINGS_PATH), exist_ok=True)
    with open(LOCAL_SETTINGS_PATH, "w") as fh:
        json.dump(local, fh, indent=2)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def start_experiment(model):
    if model not in ALIASES:
        raise ValueError(f"Pick one of: {', '.join(ALIASES)}.")
    st = _state()
    if st["active"]:
        return {"ok": False, "message": "An experiment is already running. Stop it first."}
    s = _claude_settings()
    cur = _env_value(s)
    if cur:
        return {"ok": False, "message": f"{ENV_VAR} is already set to {cur} in {integrate.SETTINGS}. "
                                        "finops won't overwrite a value it didn't set."}
    env = s.get("env") if isinstance(s.get("env"), dict) else {}
    env[ENV_VAR] = model
    s["env"] = env
    integrate._save_settings(s)
    st["active"] = {"model": model, "started_at": _now(), "stopped_at": None}
    _save_state(st)
    return {"ok": True, "message": f"Started. New Claude Code sessions will run subagents on {model} "
                                   f"unless an agent file or the main model picks another. "
                                   f"Changed {integrate.SETTINGS} (backup: settings.json.finops-backup)."}


def stop_experiment():
    st = _state()
    act = st["active"]
    if not act:
        return {"ok": True, "message": "No experiment is running; nothing changed."}
    s = _claude_settings()
    cur = _env_value(s)
    if cur == act["model"]:
        env = dict(s["env"])
        env.pop(ENV_VAR, None)
        if env:
            s["env"] = env
        else:
            s.pop("env", None)
        integrate._save_settings(s)
        msg = f"Stopped. Removed {ENV_VAR} from {integrate.SETTINGS}."
    elif cur:
        msg = (f"Stopped. {ENV_VAR} is now {cur}, which finops didn't set, so it was left as it is.")
    else:
        msg = f"Stopped. {ENV_VAR} was already gone from {integrate.SETTINGS}."
    act = dict(act, stopped_at=_now())
    st["history"] = ([act] + st["history"])[:HISTORY_KEEP]
    st["active"] = None
    _save_state(st)
    return {"ok": True, "message": msg}


def _parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def windows(started_at, stopped_at=None, now=None):
    """(before_start, start, end): after runs start→end; before is the same length right
    before the start, capped at BEFORE_CAP_DAYS."""
    start = _parse(started_at)
    end = _parse(stopped_at) if stopped_at else (now or datetime.now(timezone.utc))
    length = min(end - start, timedelta(days=BEFORE_CAP_DAYS))
    return _iso(start - length), _iso(start), _iso(end)


def compare_experiment(a, exp, min_runs, now=None):
    """Before/after medians per type for one experiment. Ignores the page's date filter."""
    b0, s, e = windows(exp["started_at"], exp.get("stopped_at"), now)
    runs = load_runs(a, {}, "AND r.agent_id IN (SELECT agent_id FROM requests "
                            "WHERE is_sidechain=1 AND ts >= ? AND ts < ?)", (b0, e))
    want = exp["model"]
    types = {}
    for r in runs:
        t = types.setdefault(r["type"], {"before": [], "after": [], "ignored": 0})
        if r["ts"] < s:
            t["before"].append(r)
        elif _alias(r["model"]) == want:
            t["after"].append(r)
        else:
            t["ignored"] += 1

    def side(rs):
        if not rs:
            return {"runs": 0}
        return {"runs": len(rs), "med_cost": median(r["cost"] for r in rs),
                "med_turns": median(r["turns"] for r in rs),
                "med_output": median(r["output"] for r in rs)}
    out = []
    for name, t in sorted(types.items(), key=lambda kv: -len(kv[1]["after"]) - len(kv[1]["before"])):
        bef, aft = side(t["before"]), side(t["after"])
        ready = bef["runs"] >= min_runs and aft["runs"] >= min_runs
        out.append({"type": name, "before": bef, "after": aft, "ignored": t["ignored"], "ready": ready,
                    "change": (aft["med_cost"] - bef["med_cost"]) if ready else None})
    return {"model": exp["model"], "started_at": exp["started_at"], "stopped_at": exp.get("stopped_at"),
            "before_from": b0, "end": e, "types": out}


def experiment_status(a, min_runs, now=None):
    st = _state()
    out = {"model_choices": list(ALIASES), "env_var": ENV_VAR, "settings_path": integrate.SETTINGS,
           "active": None, "history": [], "warning": None, "settings_error": None,
           "shell_value": os.environ.get(ENV_VAR)}
    try:
        cur = _env_value(_claude_settings())
    except SettingsError as e:
        cur, out["settings_error"] = None, str(e)
    out["current_value"] = cur
    if st["active"]:
        out["active"] = compare_experiment(a, st["active"], min_runs, now)
        if cur != st["active"]["model"] and not out["settings_error"]:
            out["warning"] = (f"{ENV_VAR} is no longer set to {st['active']['model']} in your settings"
                              + (f" (it is {cur})" if cur else "")
                              + ". New runs may not reflect the experiment.")
    out["history"] = [compare_experiment(a, h, min_runs, now) for h in st["history"]]
    return out


# ------------------------------------------------------------------- page ----

def report(a, f, home=None, now=None):
    min_runs = int(((a.settings.get("subagents") or {}).get("min_runs")) or DEFAULT_MIN_RUNS)
    agents = f.get("agents") or []
    claude = not agents or "claude" in agents
    out = {"claude_selected": claude, "min_runs": min_runs, "types": [],
           "kpis": {"sub_cost": 0.0, "total_cost": 0.0, "share_pct": 0.0, "runs": 0, "types": 0}}
    if claude:
        runs = load_runs(a, f)
        w, p = a.where(f)
        total = a.one(f"SELECT SUM(r.est_cost_usd) c FROM requests r WHERE {w} AND r.agent='claude'", p)
        paths = [r["path"] for r in a.q("SELECT DISTINCT path FROM projects WHERE path IS NOT NULL")]
        custom = custom_agents(paths, home)
        by_type = {}
        for r in runs:
            by_type.setdefault(r["type"], {}).setdefault(r["model"], []).append(r)
        for name, models in by_type.items():
            rows = sorted((_cell(a.pricing, m, rs) for m, rs in models.items()), key=lambda c: -c["cost"])
            out["types"].append({"type": name, "custom": name in custom,
                                 "runs": sum(c["runs"] for c in rows), "cost": sum(c["cost"] for c in rows),
                                 "rows": rows, "suggestion": suggest(name, rows, min_runs, custom)})
        out["types"].sort(key=lambda t: -t["cost"])
        sub = sum(r["cost"] for r in runs)
        tot = total.get("c") or 0.0
        out["kpis"] = {"sub_cost": sub, "total_cost": tot, "share_pct": 100 * sub / tot if tot else 0.0,
                       "runs": len(runs), "types": len(by_type)}
    out["experiment"] = experiment_status(a, min_runs, now)
    return out
