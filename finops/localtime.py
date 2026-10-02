"""Calendar days in the user's own time zone.

Transcripts carry UTC timestamps. Cutting days at UTC midnight puts work on the wrong
date for anyone far from UTC (in India, everything between midnight and 05:30 local
time lands on the previous day), so "today", the daily charts and billing-period edges
disagree with the user's own calendar. Every day
and hour column is derived here instead, in the machine's local zone, or in the zone
named by CLAUDE_FINOPS_TZ (an IANA name such as "Asia/Kolkata").
"""
import os
from datetime import datetime, timezone


def _zone():
    name = os.environ.get("CLAUDE_FINOPS_TZ")
    if name:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name)
        except Exception:
            pass
    return None                     # astimezone(None) = the machine's local zone


def parse(ts):
    """An aware datetime for an ISO timestamp (Z or offset), or None."""
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def local(ts):
    t = parse(ts)
    return t.astimezone(_zone()) if t else None


def day(ts):
    """YYYY-MM-DD of the timestamp in local time; '' when it can't be read."""
    t = local(ts)
    return t.date().isoformat() if t else ""


def hour(ts):
    t = local(ts)
    return t.hour if t else None


def today():
    return datetime.now(timezone.utc).astimezone(_zone()).date()
