"""Jev (TypeSafe's "System One" model): install its Claude Code plugin, keep its API key.

Jev makes fast, typed decisions (pick one, yes or no, a score, a value). It does not write
text or code, so it cannot stand in for the model behind Claude Code; the plugin teaches
Claude how to use Jev in the software you build. https://docs.typesafe.ai

  install    claude plugin marketplace add typesafe-ai/skills
             claude plugin install typesafe@typesafe-ai
  uninstall  claude plugin uninstall typesafe@typesafe-ai
  API key    env.TYPESAFE_API_KEY in ~/.claude/settings.json, which Claude Code hands to
             every new session (the page never gets the key back, only its last four)

Every command is a fixed argument list, run only when the user clicks.
"""
import json
import os
import re
import shutil
import subprocess

from . import integrate

PLUGIN = "typesafe@typesafe-ai"
MARKETPLACE = "typesafe-ai/skills"
ENV_KEY = "TYPESAFE_API_KEY"
JEV_USD_PER_INPUT_TOKEN = 0.042 / 1e6      # TypeSafe's published price; output is free


# ------------------------------------------------------------------ plugin ----

def _claude():
    return shutil.which("claude")


def _run(cmd, log):
    """Run one fixed command, log every output line, and put its last lines in any error
    (so "already added" can be recognised). No shell."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(f"{' '.join(cmd[1:4])} could not run: {e}") from None
    lines = [ln.strip() for ln in (r.stdout + "\n" + r.stderr).splitlines() if ln.strip()]
    for ln in lines:
        log(ln[-200:])
    if r.returncode:
        raise RuntimeError(f"{' '.join(cmd[1:4])} failed: " + (" ".join(lines[-3:]) or f"exit {r.returncode}"))


def status():
    """Is the plugin installed? Never raises: a broken `claude` just reads as not installed."""
    exe = _claude()
    out = {"claude": bool(exe), "installed": False, "version": None, "enabled": None}
    if not exe:
        return out
    try:
        r = subprocess.run([exe, "plugin", "list", "--json"], capture_output=True, text=True,
                           timeout=20)
        rows = json.loads(r.stdout or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return out
    for p in rows if isinstance(rows, list) else []:
        if isinstance(p, dict) and p.get("id") == PLUGIN:
            out.update(installed=True, version=p.get("version"), enabled=p.get("enabled"))
    return out


def _need_claude():
    exe = _claude()
    if not exe:
        raise RuntimeError("Claude Code's `claude` command was not found. Install Claude Code, "
                           "or start the dashboard from a terminal where `claude` works.")
    return exe


def install(log):
    exe = _need_claude()
    log(f"Adding TypeSafe's plugin list: claude plugin marketplace add {MARKETPLACE}")
    try:
        _run([exe, "plugin", "marketplace", "add", MARKETPLACE], log)
    except RuntimeError as e:
        if "already" not in str(e).lower():
            raise
        log("Already added. Carrying on.")
    log(f"Installing the plugin: claude plugin install {PLUGIN}")
    _run([exe, "plugin", "install", PLUGIN], log)
    log("Installed. New Claude Code sessions can use it; restart any open ones.")
    return status()


def uninstall(log):
    exe = _need_claude()
    log(f"Removing the plugin: claude plugin uninstall {PLUGIN}")
    _run([exe, "plugin", "uninstall", PLUGIN], log)
    log("Removed.")
    return status()


# --------------------------------------------------------------------- key ----

def key_status():
    """Where the key comes from and its last four characters. Never the key."""
    shell = os.environ.get(ENV_KEY)
    stored = ((integrate._load_settings().get("env") or {}).get(ENV_KEY)) or ""
    key = shell or stored
    return {"source": "shell" if shell else "claude_settings" if stored else None,
            "last4": key[-4:] if len(key) >= 12 else None}


def save_key(value):
    value = (value or "").strip()
    if not value:
        raise ValueError("Paste your Jev API key first.")
    if any(ch.isspace() for ch in value):
        raise ValueError("A key has no spaces or line breaks in it.")
    if len(value) < 16:
        raise ValueError("That looks too short for a TypeSafe API key.")
    s = integrate._load_settings()
    env = s.get("env") if isinstance(s.get("env"), dict) else {}
    env[ENV_KEY] = value
    s["env"] = env
    integrate._save_settings(s)


def remove_key():
    s = integrate._load_settings()
    env = s.get("env") if isinstance(s.get("env"), dict) else {}
    if ENV_KEY not in env:
        return
    env.pop(ENV_KEY)
    if env:
        s["env"] = env
    else:
        s.pop("env", None)
    integrate._save_settings(s)


# --------------------------------------------------------------- where it fits ----
# A prompt Jev could have answered: short (at most two model calls), no tools, a short
# reply, and worded as a decision rather than a request to build something.
DECIDE = re.compile(r"\b(classify|categori[sz]e|category|label|which (one|of)|yes or no|true or false|"
                    r"is (this|it) (a|an)\b|extract|score|rate this|triage|route|detect|decide|pick one|"
                    r"choose|prioriti[sz]e|sentiment|is it valid|should (i|we)\b|does (this|it)\b)", re.I)
BUILD = re.compile(r"\b(write|implement|build|create|fix|refactor|add|update|change|make|generate|"
                   r"design|explain|debug)\b", re.I)
# Claude Code's own summary after a context compaction is stored like a prompt; it is not yours.
NOT_YOURS = ("This session is being continued from a previous conversation",)
_MASK = re.compile(r"\S+@\S+|(?:~|\.{0,2})?/[\w.\-/]+|\d{3,}")


def _mask(text):
    return _MASK.sub("…", re.sub(r"\s+", " ", text or "")).strip()[:140]


def fit(a, f=None):
    """How much of the filtered Claude Code work was decision-shaped, and what it would cost on Jev."""
    w, p = a.where(f)
    total = a.one(f"SELECT COALESCE(SUM(r.est_cost_usd),0) c FROM requests r "
                  f"WHERE {w} AND r.agent='claude'", p)["c"]
    rows = a.q(f"""SELECT pr.text, pr.request_count, pr.output_tokens, pr.est_cost_usd cost,
                          COALESCE(pr.input_tokens,0) + COALESCE(pr.cache_read_tokens,0)
                            + COALESCE(pr.cache_write_tokens,0) ctx
                   FROM prompts pr WHERE pr.agent='claude' AND pr.id IN (
                     SELECT r.prompt_id FROM requests r WHERE {w} AND r.agent='claude')
                     AND COALESCE(pr.request_count,0) <= 2 AND COALESCE(pr.tool_calls,0) = 0
                     AND COALESCE(pr.output_tokens,0) <= 300""", p)
    hits = [r for r in rows if DECIDE.search(r["text"] or "") and not BUILD.search((r["text"] or "")[:200])
            and not (r["text"] or "").lstrip().startswith(NOT_YOURS)]
    n_all = a.one(f"SELECT COUNT(DISTINCT r.prompt_id) n FROM requests r "
                  f"WHERE {w} AND r.agent='claude' AND r.prompt_id IS NOT NULL", p)["n"]
    cost = sum(r["cost"] or 0 for r in hits)
    return {"prompts": len(hits), "total_prompts": n_all, "claude_cost": cost,
            "jev_cost": sum(r["ctx"] or 0 for r in hits) * JEV_USD_PER_INPUT_TOKEN,
            "share_pct": 100.0 * cost / total if total else 0.0, "total_cost": total,
            "examples": [{"text": _mask(r["text"]), "cost": r["cost"] or 0}
                         for r in sorted(hits, key=lambda r: -(r["cost"] or 0))[:5]]}
