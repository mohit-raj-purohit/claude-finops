"""Run the trial the evidence asks for, instead of only recommending one.

model_evidence() ends with an honest caveat: your prompts were never randomly
assigned to models, so a category can simply have been easier on one of them.
That caveat can only be closed by actually running the same prompt on both
models and looking at what came back.

So: take real prompts out of your own history, re-run them headlessly on the
candidate model, and compare what they cost against what they cost the first
time. `claude -p ... --output-format json` reports cost, turns and errors for
exactly this purpose.

Two things this deliberately does not do:

  * It never runs anything on its own. A trial spends real money and drives a
    real agent, so it happens on an explicit click, with the bill shown first.
  * It runs in a scratch directory by default, not your repo. Headless Claude
    cannot ask for permission, so tools that need it are denied and recorded —
    but a trial that edits your working tree to prove a point is not a trial
    anyone wants.
"""
import json
import os
import shutil
import subprocess
import tempfile
import time

from .advisor import model_alias
from .paths import DB_PATH

TIMEOUT_S = 600
MAX_PROMPTS = 5
MAX_CHARS = 6000          # a prompt longer than this is usually a paste, not a task


def samples(category, limit=3):
    """Real prompts from this category, with what they actually cost first time.

    Picks around the middle of the cost distribution: the cheapest prompts are
    usually "yes"/"continue" and the most expensive are outliers, and neither
    tells you much about a model.
    """
    from .analytics import Analytics
    a = Analytics(DB_PATH)
    rows = a.q("""SELECT p.id, p.text, p.models model, p.est_cost_usd cost,
        p.request_count turns, p.tool_calls tools, p.session_id, p.day
        FROM prompts p
        WHERE COALESCE(p.category,'other') = ? AND p.agent = 'claude'
          AND p.models IS NOT NULL AND p.models NOT LIKE '%,%'
          AND p.models != '<synthetic>' AND p.est_cost_usd > 0
          AND LENGTH(p.text) BETWEEN 40 AND ?
          -- Things you actually typed. System turns, tool results, queued
          -- follow-ups and SDK traffic are not tasks anyone would re-run, and
          -- a trial built from task-notification XML measures nothing.
          AND p.source = 'typed'
          AND p.text NOT LIKE '<%' AND p.text NOT LIKE '[Request interrupted%'
          AND p.text NOT LIKE 'Caveat:%' AND p.text NOT LIKE '%<local-command%' 
        ORDER BY p.est_cost_usd""", (category, MAX_CHARS))
    if not rows:
        return []
    lo = int(len(rows) * 0.35)
    hi = max(lo + 1, int(len(rows) * 0.85))
    mid = rows[lo:hi] or rows
    step = max(1, len(mid) // limit)
    picked = mid[::step][:limit]
    return [{"prompt_id": r["id"], "text": r["text"], "baseline_model": r["model"],
             "baseline_name": a.pricing.display_name(r["model"]),
             "baseline_cost_usd": round(r["cost"], 4),
             "baseline_turns": r["turns"], "baseline_tools": r["tools"],
             "day": r["day"]} for r in picked]


def available():
    return bool(shutil.which("claude"))


def run_one(text, model, cwd=None, timeout=TIMEOUT_S):
    """One headless run. Returns what it cost and whether it got there."""
    exe = shutil.which("claude")
    if not exe:
        return {"ok": False, "error": "The `claude` CLI is not on PATH."}
    sandbox = cwd or tempfile.mkdtemp(prefix="finops-trial-")
    started = time.time()
    try:
        p = subprocess.run([exe, "-p", text, "--model", model_alias(model),
                            "--output-format", "json"],
                           capture_output=True, text=True, timeout=timeout, cwd=sandbox)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Gave up after {timeout}s.",
                "elapsed_s": round(time.time() - started, 1)}
    except OSError as e:
        return {"ok": False, "error": str(e)}
    try:
        d = json.loads(p.stdout)
    except ValueError:
        return {"ok": False, "error": (p.stderr or p.stdout or "no output")[:400],
                "elapsed_s": round(time.time() - started, 1)}
    usage = d.get("usage") or {}
    return {
        "ok": not d.get("is_error"),
        "cost_usd": d.get("total_cost_usd"),
        "turns": d.get("num_turns"),
        "elapsed_s": round(time.time() - started, 1),
        "duration_api_ms": d.get("duration_api_ms"),
        "output_tokens": usage.get("output_tokens"),
        "input_tokens": usage.get("input_tokens"),
        "denials": len(d.get("permission_denials") or []),
        "stop_reason": d.get("stop_reason") or d.get("terminal_reason"),
        "result": (d.get("result") or "")[:4000],
        "session_id": d.get("session_id"),
        "sandbox": sandbox,
    }


def _verdict(runs, baseline_cost):
    """What the runs actually showed, in the same language as the back-test."""
    done = [r for r in runs if r.get("ok") and r.get("cost_usd") is not None]
    if not done:
        return "failed", ("Every run errored or produced nothing, so this says nothing about "
                          "cost. Check the errors below before reading anything into it.")
    got = sum(r["cost_usd"] for r in done)
    base = sum(baseline_cost)
    denials = sum(r.get("denials") or 0 for r in done)
    if base <= 0:
        return "unclear", "No usable baseline cost for these prompts."
    pct = 100.0 * (1 - got / base)
    tail = (f" {denials} tool call(s) were denied because a headless run cannot ask for "
            f"permission, so the real task may be larger than this." if denials else "")
    if len(done) < len(runs):
        tail += f" {len(runs) - len(done)} of {len(runs)} runs failed and are excluded."
    if pct >= 25:
        return "confirmed", (f"Re-running your own prompts cost {pct:.0f}% less on the cheaper "
                             f"model (${got:.2f} against ${base:.2f} the first time).{tail}")
    if pct >= 0:
        return "marginal", (f"Only {pct:.0f}% cheaper on a re-run (${got:.2f} against "
                            f"${base:.2f}). Not the saving the history suggested.{tail}")
    return "contradicted", (f"The re-run cost {abs(pct):.0f}% *more* (${got:.2f} against "
                            f"${base:.2f}). The history overstated this switch.{tail}")


def run(category, model, prompts, cwd=None):
    """Run a set of sample prompts on a candidate model and judge the result."""
    if not prompts:
        return {"ok": False, "error": "Nothing to run."}
    prompts = prompts[:MAX_PROMPTS]
    runs, baseline = [], []
    for item in prompts:
        text = (item.get("text") or "").strip()
        if not text:
            continue
        r = run_one(text, model, cwd=cwd)
        r["prompt"] = text[:300]
        r["baseline_cost_usd"] = item.get("baseline_cost_usd") or 0
        r["baseline_turns"] = item.get("baseline_turns")
        runs.append(r)
        baseline.append(r["baseline_cost_usd"])
    verdict, why = _verdict(runs, baseline)
    done = [r for r in runs if r.get("ok") and r.get("cost_usd") is not None]
    return {
        "ok": True, "category": category, "model": model, "alias": model_alias(model),
        "runs": runs, "verdict": verdict, "why": why,
        "measured_cost_usd": round(sum(r["cost_usd"] for r in done), 4),
        "baseline_cost_usd": round(sum(baseline), 4),
        "ran_at": int(time.time()),
        "note": ("Measured by re-running these exact prompts headlessly. The agent had no "
                 "permission to use tools that ask, and worked in a scratch directory, so a "
                 "task that needs your repo will look smaller here than it is."),
    }
