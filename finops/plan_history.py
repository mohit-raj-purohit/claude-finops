"""Plan-limit history, as the Claude desktop app records it.

The desktop app samples plan usage every few minutes into plan-usage-history.json:
`fh` is the 5-hour window and `sd` the weekly (seven-day) window, both percent used.
The format is undocumented, so anything unexpected returns {"ok": False, "reason"}
instead of raising. Read on request, cached on the file's mtime.
"""
import json
import os
from datetime import datetime, timezone

from .paths import PLAN_HISTORY_PATH

KNOWN_VERSIONS = {2}
_cache = {}


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _runs(values, threshold):
    """Separate stretches at or above the threshold, not raw sample counts."""
    n, above = 0, False
    for v in values:
        hit = v is not None and v >= threshold
        n += hit and not above
        above = hit
    return n


def _peak(values):
    return max((v for v in values if v is not None), default=None)


def history(path=None):
    path = path or PLAN_HISTORY_PATH
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {"ok": False, "reason": "No plan history on this machine. The Claude desktop "
                                       "app records it; it is not installed or has not saved any yet."}
    hit = _cache.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    res = _read(path)
    _cache[path] = (mtime, res)
    return res


def _read(path):
    try:
        with open(path) as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return {"ok": False, "reason": "The plan history file could not be read."}
    version = raw.get("version") if isinstance(raw, dict) else None
    if version not in KNOWN_VERSIONS:
        return {"ok": False, "reason": f"Unrecognised plan history format (version {version})."}
    samples = [x for x in raw.get("samples") or []
               if isinstance(x, dict) and _num(x.get("t")) is not None]
    if not samples:
        return {"ok": False, "reason": "The plan history file has no samples yet."}
    samples.sort(key=lambda x: x["t"])
    orgs = {x.get("org") for x in samples}
    org = samples[-1].get("org")
    series = []
    for x in samples:
        if x.get("org") != org:
            continue
        u = x.get("u") if isinstance(x.get("u"), dict) else {}
        t = datetime.fromtimestamp(x["t"] / 1000, timezone.utc).isoformat().replace("+00:00", "Z")
        series.append({"t": t, "five_hour": _num(u.get("fh")), "weekly": _num(u.get("sd"))})
    fh = [r["five_hour"] for r in series]
    sd = [r["weekly"] for r in series]
    return {"ok": True, "source": path, "org_count": len(orgs), "series": series,
            "summary": {"five_hour_peak": _peak(fh), "weekly_peak": _peak(sd),
                        "five_hour_ge90": _runs(fh, 90), "five_hour_hit100": _runs(fh, 100),
                        "weekly_ge90": _runs(sd, 90), "weekly_hit100": _runs(sd, 100),
                        "first": series[0]["t"], "last": series[-1]["t"]}}
