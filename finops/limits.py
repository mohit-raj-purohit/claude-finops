"""Current plan usage, as `/usage` reports it.

Claude Code owns those numbers (5-hour session window and weekly windows); they
are not in any file on this machine. The one way to read them without an account
API is to ask Claude Code itself:

    claude -p "/usage" --output-format json

That runs the local slash command, spends no tokens and makes no model call, and
returns the same text `/usage` prints in the TUI. The reply is parsed here and
cached briefly so the dashboard never shells out on every render.
"""
import json
import os
import re
import shutil
import subprocess
import time

from .paths import DATA_DIR, ensure_dirs

CACHE = os.path.join(DATA_DIR, "usage_cache.json")
TTL_S = 120
TIMEOUT_S = 90

# "Current session: 0% used · resets Sep 22 at 2:40pm (Asia/Calcutta)"
_LIMIT = re.compile(r"^(?P<label>[^:]+?):\s*(?P<pct>\d+(?:\.\d+)?)%\s*used"
                    r"(?:\s*[·-]\s*resets\s*(?P<resets>.+?))?\s*$")
# "  40% of your usage was at >150k context"   /   "  Top skills: /run 2%"
_WINDOW = re.compile(r"^Last\s+(?P<window>\S+)\s*·\s*(?P<detail>.+)$")


def _parse(text):
    limits, windows, cur = [], [], None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _WINDOW.match(line)
        if m:
            cur = {"window": m.group("window"), "detail": m.group("detail"), "notes": []}
            windows.append(cur)
            continue
        m = _LIMIT.match(line)
        if m and line.lower().startswith("current"):
            limits.append({"label": m.group("label").strip(),
                           "pct": float(m.group("pct")),
                           "resets": (m.group("resets") or "").strip()})
            cur = None
            continue
        if cur is not None and raw.startswith((" ", "\t")):
            cur["notes"].append(line)
    return limits, windows


def _run():
    exe = shutil.which("claude")
    if not exe:
        return {"ok": False, "error": "`claude` is not on PATH, so /usage can't be read here."}
    try:
        p = subprocess.run([exe, "-p", "/usage", "--output-format", "json"],
                           capture_output=True, text=True, timeout=TIMEOUT_S,
                           cwd=os.path.expanduser("~"))
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"`claude -p /usage` did not answer within {TIMEOUT_S}s."}
    except OSError as e:
        return {"ok": False, "error": f"Could not run `claude`: {e}"}
    if p.returncode != 0:
        return {"ok": False, "error": (p.stderr or p.stdout or "claude exited non-zero").strip()[:400]}
    try:
        text = (json.loads(p.stdout) or {}).get("result") or ""
    except ValueError:
        return {"ok": False, "error": "Could not parse the reply from `claude -p /usage`."}
    if not text.strip():
        return {"ok": False, "error": "`/usage` returned nothing."}
    limits, windows = _parse(text)
    return {"ok": True, "limits": limits, "windows": windows, "text": text,
            "fetched_at": time.time(),
            "note": "Read from Claude Code's own /usage. Percentages are plan limits, not cost; "
                    "the contributing-behaviour lines cover local sessions on this machine only."}


def usage(force=False):
    """Cached /usage. Reads the cache unless it is stale or `force` is set."""
    ensure_dirs()
    cached = None
    try:
        with open(CACHE) as fh:
            cached = json.load(fh)
    except (OSError, ValueError):
        pass
    if cached and cached.get("ok") and not force and time.time() - cached.get("fetched_at", 0) < TTL_S:
        return dict(cached, cached=True, age_s=int(time.time() - cached["fetched_at"]))
    res = _run()
    if res.get("ok"):
        try:
            with open(CACHE, "w") as fh:
                json.dump(res, fh)
        except OSError:
            pass
        return dict(res, cached=False, age_s=0)
    if cached and cached.get("ok"):          # keep showing the last good read
        return dict(cached, cached=True, stale=True, error=res.get("error"),
                    age_s=int(time.time() - cached["fetched_at"]))
    return res
