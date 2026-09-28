"""Claude parity features: heatmap, vs-yesterday, resume, plan history.

Run: python3 -m unittest tests.test_parity -v
"""
import json, os, sqlite3, sys, tempfile, time, unittest
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finops.analytics import Analytics
from finops.etl import SCHEMA


def make_db(requests, sessions=(("A", "/x/.claude/projects/-p/A.jsonl"),)):
    """requests: iterable of (ts, day, cost, tokens, session_id)."""
    path = tempfile.mktemp(suffix=".db", prefix="finops-parity-")
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.execute("INSERT INTO meta VALUES ('built_at','test')")
    db.execute("INSERT INTO projects (id, slug, path, name) VALUES (1,'p','/p','proj')")
    for sid, src in sessions:
        db.execute("INSERT INTO sessions (id, project_id, source_file) VALUES (?,1,?)", (sid, src))
    for i, (ts, day, cost, tokens, sid) in enumerate(requests):
        db.execute("""INSERT INTO requests (uuid, session_id, project_id, ts, day, model,
                        est_cost_usd, billable_tokens) VALUES (?,?,1,?,?,'claude-opus-5',?,?)""",
                   (f"u{i}", sid, ts, day, cost, tokens))
    db.commit(); db.close()
    return path


class LocalTZ:
    def __init__(self, tz): self.tz = tz
    def __enter__(self):
        self.old = os.environ.get("TZ"); os.environ["TZ"] = self.tz; time.tzset()
    def __exit__(self, *a):
        if self.old is None: os.environ.pop("TZ", None)
        else: os.environ["TZ"] = self.old
        time.tzset()


class TestHeatmap(unittest.TestCase):
    def test_168_cells_monday_first_local_time(self):
        # 2026-01-04 is a Sunday; 23:00 UTC is Monday 04:30 in Asia/Kolkata
        a = Analytics(make_db([("2026-01-04T23:00:00Z", "2026-01-04", 2.0, 100, "A")]))
        with LocalTZ("Asia/Kolkata"):
            h = a.heatmap({})
        self.assertEqual(len(h["cells"]), 168)
        hot = [c for c in h["cells"] if c["requests"]]
        self.assertEqual(hot, [{"dow": 0, "hour": 4, "cost": 2.0, "tokens": 100, "requests": 1}])
        self.assertEqual(h["cost_basis"], "estimated")

    def test_utc_zone_keeps_utc_hour(self):
        a = Analytics(make_db([("2026-01-04T23:00:00Z", "2026-01-04", 1.0, 5, "A")]))
        with LocalTZ("UTC"):
            hot = [c for c in a.heatmap({})["cells"] if c["requests"]]
        self.assertEqual((hot[0]["dow"], hot[0]["hour"]), (6, 23))   # Sunday 23:00

    def test_filter_matching_nothing_gives_zero_grid(self):
        a = Analytics(make_db([("2026-01-04T23:00:00Z", "2026-01-04", 1.0, 5, "A")]))
        h = a.heatmap({"start": "2030-01-01"})
        self.assertEqual(len(h["cells"]), 168)
        self.assertEqual(sum(c["requests"] for c in h["cells"]), 0)


class TestYesterday(unittest.TestCase):
    def test_cost_yesterday(self):
        a = Analytics(make_db([("2026-03-09T10:00:00Z", "2026-03-09", 4.0, 10, "A"),
                               ("2026-03-10T10:00:00Z", "2026-03-10", 6.0, 10, "A")]))
        a._today = date(2026, 3, 10)
        o = a.overview({})
        self.assertAlmostEqual(o["cost_yesterday"]["c"], 4.0)
        self.assertAlmostEqual(o["cost_today"]["c"], 6.0)

    def test_no_spend_yesterday_is_zero_not_error(self):
        a = Analytics(make_db([("2026-03-10T10:00:00Z", "2026-03-10", 6.0, 10, "A")]))
        a._today = date(2026, 3, 10)
        self.assertEqual(a.overview({})["cost_yesterday"]["c"], 0)
