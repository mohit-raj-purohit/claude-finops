"""Tests for hygiene(): spend above context thresholds and after first crossing.

Run: python3 -m unittest discover -s tests -v
"""
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from finops.analytics import Analytics
from finops.etl import SCHEMA


def make_db(rows):
    """rows: (session_id, seq, context_tokens, est_cost_usd, is_sidechain)"""
    path = tempfile.mktemp(suffix=".db", prefix="finops-test-")
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.execute("INSERT INTO meta (key, value) VALUES ('built_at', 'test')")
    db.execute("INSERT INTO projects (id, slug, path, name) VALUES (1, 'p', '/p', 'proj')")
    for sid in {r[0] for r in rows}:
        db.execute("INSERT INTO sessions (id, project_id, title) VALUES (?, 1, ?)", (sid, "title " + sid))
    for i, (sid, seq, ctx, cost, side) in enumerate(rows):
        db.execute("""INSERT INTO requests (uuid, session_id, project_id, prompt_id, ts, day, model,
                        priced_as, context_tokens, est_cost_usd, is_sidechain,
                        input_tokens, output_tokens, thinking_tokens, cache_read_tokens,
                        cache_write_tokens, billable_tokens)
                      VALUES (?, ?, 1, 1, ?, '2026-01-01', 'm', 'm', ?, ?, ?, 0, 10, 0, ?, 0, ?)""",
                   (f"u{i}", sid, f"2026-01-01T00:{seq:02d}:00Z", ctx, cost, side, ctx, ctx + 10))
    db.commit()
    db.close()
    return path


class TestHygiene(unittest.TestCase):
    def analytics(self, rows, thresholds=(100_000, 150_000)):
        path = make_db(rows)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        a = Analytics(path)
        a.settings["hygiene"] = {"context_thresholds": list(thresholds)}
        return a

    ROWS = [
        # session A climbs through both thresholds
        ("A", 0, 50_000, 1.0, 0), ("A", 1, 120_000, 2.0, 0),
        ("A", 2, 160_000, 3.0, 0), ("A", 3, 170_000, 4.0, 0),
        # session B never crosses
        ("B", 0, 20_000, 5.0, 0), ("B", 1, 30_000, 5.0, 0),
        # a subagent turn inside A: excluded entirely
        ("A", 4, 190_000, 100.0, 1),
    ]

    def test_share_of_spend_in_requests_above_each_threshold(self):
        out = self.analytics(self.ROWS).hygiene()
        self.assertAlmostEqual(out["cost_usd"], 20.0)          # sidechain $100 excluded
        a100, a150 = out["above"]["100000"], out["above"]["150000"]
        self.assertEqual(a100["requests"], 3)
        self.assertAlmostEqual(a100["cost_usd"], 9.0)
        self.assertAlmostEqual(a100["share_pct"], 45.0)
        self.assertEqual(a150["requests"], 2)
        self.assertAlmostEqual(a150["cost_usd"], 7.0)
        self.assertAlmostEqual(a150["share_pct"], 35.0)

    def test_spend_after_first_crossing_counts_everything_from_that_request_on(self):
        out = self.analytics(self.ROWS).hygiene()
        s = out["sessions_ranked"][0]
        self.assertEqual(s["session_id"], "A")
        self.assertEqual(s["first_cross"]["100000"], 1)
        self.assertEqual(s["first_cross"]["150000"], 2)
        self.assertAlmostEqual(s["cost_after"]["100000"], 9.0)
        self.assertAlmostEqual(s["cost_after"]["150000"], 7.0)
        self.assertAlmostEqual(s["cost_after_pct"]["150000"], 70.0)
        self.assertEqual(out["above"]["150000"]["sessions"], 1)
        self.assertAlmostEqual(out["above"]["150000"]["share_after_first_cross_pct"], 35.0)

    def test_sessions_that_never_cross_are_not_ranked_first(self):
        out = self.analytics(self.ROWS).hygiene()
        ids = [s["session_id"] for s in out["sessions_ranked"]]
        self.assertEqual(ids[0], "A")
        b = next(s for s in out["sessions_ranked"] if s["session_id"] == "B")
        self.assertIsNone(b["first_cross"]["100000"])
        self.assertAlmostEqual(b["cost_after"]["150000"], 0.0)

    def test_trajectory_is_in_request_order_and_downsampled(self):
        out = self.analytics(self.ROWS).hygiene(trajectory_points=3)
        s = out["sessions_ranked"][0]
        self.assertEqual(len(s["context_trajectory"]), 3)
        self.assertEqual(s["context_trajectory"][0], 50_000)
        full = self.analytics(self.ROWS).hygiene()["sessions_ranked"][0]
        self.assertEqual(full["context_trajectory"], [50_000, 120_000, 160_000, 170_000])
        self.assertEqual(full["cumulative_cost"], [1.0, 3.0, 6.0, 10.0])

    def test_no_savings_anywhere_in_the_payload(self):
        out = self.analytics(self.ROWS).hygiene()
        flat = str(out).lower()
        self.assertNotIn("saving", flat)
        self.assertEqual(out["basis"], "actual")

    def test_thresholds_are_configurable(self):
        out = self.analytics(self.ROWS, thresholds=(25_000,)).hygiene()
        self.assertEqual(out["thresholds"], [25_000])
        self.assertEqual(out["above"]["25000"]["requests"], 5)

    def test_empty_range(self):
        out = self.analytics([]).hygiene()
        self.assertEqual(out["requests"], 0)
        self.assertEqual(out["sessions_ranked"], [])
        self.assertEqual(out["above"]["100000"]["share_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()
