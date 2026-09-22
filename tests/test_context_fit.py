"""Tests for context_window_fit(), the observation kept from the removed reprice.

Runs Analytics against a throwaway warehouse built from the real schema, so the
query and the bucketing are exercised end to end without touching ~/.claude-finops.

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


class FixedWindowPricing:
    """Two models: a 200K window and one with no window at all."""

    WINDOWS = {"m200k": 200_000, "m200k[1m]": 1_000_000}

    def context_window(self, model):
        return self.WINDOWS.get(model)

    def display_name(self, model):
        return model

    def tier(self, model):
        return "balanced"


class TestModelsUtilisation(unittest.TestCase):
    """models() utilisation is measured against the window that served each request."""

    def analytics(self, rows):
        path = make_db(rows)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        a = Analytics(path)
        a.pricing = FixedWindowPricing()
        return a

    def test_long_context_requests_use_their_own_window(self):
        # two standard requests at 50% of 200K, two long-context at 50% of 1M
        a = self.analytics([
            ("m200k", "m200k", 100_000, 1.0), ("m200k", "m200k", 100_000, 1.0),
            ("m200k", "m200k[1m]", 500_000, 1.0), ("m200k", "m200k[1m]", 500_000, 1.0),
        ])
        row = a.models()["rows"][0]
        self.assertAlmostEqual(row["utilization_pct"], 50.0)   # never 200%+ any more
        self.assertEqual(row["long_context_requests"], 2)
        self.assertEqual(row["over_window_requests"], 0)

    def test_unexplained_over_window_requests_are_counted_not_averaged(self):
        path = make_db([("m200k", "m200k", 100_000, 1.0)])
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        db = sqlite3.connect(path)
        db.execute("""INSERT INTO requests (uuid, session_id, project_id, prompt_id, ts, day,
                        model, priced_as, context_tokens, est_cost_usd, unpriced_long_context,
                        input_tokens, output_tokens, thinking_tokens, cache_read_tokens,
                        cache_write_tokens, billable_tokens)
                      VALUES ('u9', 's', 1, 1, '2026-01-01T00:00:09Z', '2026-01-01',
                              'm200k', 'm200k', 400000, 1.0, 1, 0, 100, 0, 400000, 0, 400100)""")
        db.commit(); db.close()
        a = Analytics(path)
        a.pricing = FixedWindowPricing()
        row = a.models()["rows"][0]
        self.assertAlmostEqual(row["utilization_pct"], 50.0)
        self.assertEqual(row["over_window_requests"], 1)


def make_db(rows):
    """rows: (model, priced_as, context_tokens, est_cost_usd)"""
    path = tempfile.mktemp(suffix=".db", prefix="finops-test-")
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.execute("INSERT INTO meta (key, value) VALUES ('built_at', 'test')")
    for i, (model, priced_as, ctx, cost) in enumerate(rows):
        db.execute("""INSERT INTO requests (uuid, session_id, project_id, prompt_id, ts, day,
                        model, priced_as, context_tokens, est_cost_usd,
                        input_tokens, output_tokens, thinking_tokens, cache_read_tokens,
                        cache_write_tokens, billable_tokens)
                      VALUES (?, 's', 1, 1, ?, '2026-01-01', ?, ?, ?, ?, 0, 100, 0, ?, 0, ?)""",
                   (f"u{i}", f"2026-01-01T00:00:{i:02d}Z", model, priced_as, ctx, cost,
                    ctx, ctx + 100))
    db.commit()
    db.close()
    return path


class TestContextWindowFit(unittest.TestCase):
    def analytics(self, rows):
        path = make_db(rows)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        a = Analytics(path)
        a.pricing = FixedWindowPricing()
        return a

    def test_buckets_near_and_over_against_the_models_own_window(self):
        a = self.analytics([
            ("m200k", "m200k", 50_000, 1.0),        # comfortably inside
            ("m200k", "m200k", 180_000, 2.0),       # >= 90%: near
            ("m200k", "m200k", 199_000, 3.0),       # near
            ("m200k", "m200k[1m]", 300_000, 4.0),   # over the standard window
        ])
        out = a.context_window_fit()
        self.assertEqual(out["requests"], 4)
        self.assertEqual(out["near_requests"], 2)
        self.assertAlmostEqual(out["near_cost_usd"], 5.0)
        self.assertEqual(out["over_requests"], 0)   # priced_as [1m] has a 1M window: inside it
        self.assertAlmostEqual(out["cost_usd"], 10.0)
        self.assertAlmostEqual(out["near_or_over_cost_pct"], 50.0)

    def test_over_means_context_exceeded_the_window_in_use(self):
        a = self.analytics([("m200k", "m200k", 250_000, 5.0)])
        out = a.context_window_fit()
        self.assertEqual(out["over_requests"], 1)
        self.assertAlmostEqual(out["over_cost_usd"], 5.0)
        self.assertEqual(out["near_requests"], 0)

    def test_unknown_window_is_reported_not_guessed(self):
        a = self.analytics([("mystery", "mystery", 999_999, 7.0)])
        out = a.context_window_fit()
        self.assertEqual(out["models"], [])
        self.assertEqual(out["unknown_window"]["requests"], 1)
        self.assertAlmostEqual(out["unknown_window"]["cost_usd"], 7.0)
        self.assertAlmostEqual(out["near_or_over_cost_pct"], 0.0)

    def test_per_model_rows_are_ranked_by_near_or_over_spend(self):
        a = self.analytics([
            ("m200k", "m200k", 190_000, 1.0),
            ("m200k", "m200k", 190_000, 1.0),
            ("m200k[1m]", "m200k[1m]", 950_000, 9.0),
        ])
        out = a.context_window_fit()
        self.assertEqual([m["model"] for m in out["models"]], ["m200k[1m]", "m200k"])
        self.assertAlmostEqual(out["models"][0]["near_or_over_cost_pct"], 100.0)

    def test_no_savings_fields_survive(self):
        a = self.analytics([("m200k", "m200k", 10, 1.0)])
        out = a.context_window_fit()
        for k in out:
            self.assertNotIn("saving", k)
        self.assertEqual(out["basis"], "actual")

    def test_empty_warehouse(self):
        a = self.analytics([])
        out = a.context_window_fit()
        self.assertEqual(out["requests"], 0)
        self.assertEqual(out["near_or_over_cost_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()
