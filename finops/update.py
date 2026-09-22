"""Is there a newer release on npm?

npm has no way to push: a global install stays on whatever version it was
installed at until someone runs `npm i -g claude-finops@latest`. So we ask the
registry ourselves, once a day, and say so if there is something newer.

This is the only outbound request the app makes on its own. It sends nothing
about you — no identifiers, no usage, not even a User-Agent beyond the package
name and the version you already published to npm by installing it. Turn it off
with NO_UPDATE_NOTIFIER=1 (the ecosystem-wide convention) or
CLAUDE_FINOPS_NO_UPDATE_CHECK=1.

The answer is cached in ~/.claude-finops/data/update_cache.json for a day, so a
dashboard that restarts twenty times makes one request. Every failure is
silent: no network, a proxy, an offline laptop and a 500 from the registry all
look the same to the caller, which is "we don't know", never an error.
"""
import json
import os
import re
import time
import urllib.request

from .paths import DATA_DIR, ROOT, ensure_dirs

CACHE = os.path.join(DATA_DIR, "update_cache.json")
URL = "https://registry.npmjs.org/claude-finops/latest"
TTL_S = 24 * 60 * 60
TIMEOUT_S = 3

_NUM = re.compile(r"\d+")


def _installed():
    """Our own version, from the package.json we ship next to the code."""
    try:
        with open(os.path.join(ROOT, "package.json")) as fh:
            return str(json.load(fh).get("version") or "").strip()
    except (OSError, ValueError):
        return ""


def _key(v):
    """Compare 1.10.0 above 1.9.0, and treat 1.0.0-rc.1 as below 1.0.0.

    Only the numeric core is ordered; a prerelease suffix just loses the tie.
    That is enough for a notifier — we publish plain releases.
    """
    core, _, pre = str(v).partition("-")
    nums = [int(n) for n in _NUM.findall(core)[:3]]
    return (nums + [0, 0, 0])[:3], 0 if pre else 1


def _newer(latest, current):
    return bool(latest) and bool(current) and _key(latest) > _key(current)


def _read_cache():
    try:
        with open(CACHE) as fh:
            c = json.load(fh)
        return c if isinstance(c, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_cache(latest):
    ensure_dirs()
    tmp = CACHE + ".tmp"
    try:
        with open(tmp, "w") as fh:
            json.dump({"latest": latest, "checked_at": int(time.time())}, fh)
        os.replace(tmp, CACHE)
    except OSError:
        pass


def _fetch():
    req = urllib.request.Request(URL, headers={
        "Accept": "application/json",
        "User-Agent": f"claude-finops/{_installed() or '0'}",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        return str(json.load(r).get("version") or "").strip()


def disabled():
    return bool(os.environ.get("NO_UPDATE_NOTIFIER")
                or os.environ.get("CLAUDE_FINOPS_NO_UPDATE_CHECK"))


def check(force=False):
    """Return {current, latest, update_available, checked_at, ...}.

    Reads the cache unless it is older than a day (or `force`). Never raises,
    and never blocks longer than TIMEOUT_S.
    """
    current = _installed()
    out = {"current": current, "latest": "", "update_available": False,
           "command": "npm i -g claude-finops@latest", "checked_at": 0,
           "disabled": disabled()}
    if out["disabled"]:
        return out

    cache = _read_cache()
    age = time.time() - float(cache.get("checked_at") or 0)
    if cache.get("latest") and age < TTL_S and not force:
        out.update(latest=cache["latest"], checked_at=int(cache["checked_at"]), cached=True)
    else:
        try:
            latest = _fetch()
        except Exception:
            # Offline, blocked, rate-limited — fall back to the last good answer
            # rather than telling anyone anything is wrong.
            latest = str(cache.get("latest") or "")
            out["checked_at"] = int(cache.get("checked_at") or 0)
            out["stale"] = True
        else:
            _write_cache(latest)
            out["checked_at"] = int(time.time())
        out["latest"] = latest
    out["update_available"] = _newer(out["latest"], current)
    return out


def notify(log=print):
    """Print one line if a newer version is out. Safe to call from a thread."""
    try:
        u = check()
    except Exception:
        return
    if u.get("update_available"):
        log(f"  update    : {u['current']} -> {u['latest']}   {u['command']}")
