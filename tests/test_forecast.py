"""Forecast and anomaly statistics. Run: python3 -m unittest tests.test_forecast -v"""
import os, sqlite3, sys, tempfile, unittest
from datetime import date, timedelta
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finops.analytics import Analytics
from finops.etl import SCHEMA

TODAY = date(2026, 9, 23)


def make_db(day_costs):
    """day_costs: {iso_day: cost}. One request per day, agent 'claude'."""
    path = tempfile.mktemp(suffix=".db", prefix="finops-test-")
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.execute("INSERT INTO meta VALUES ('built_at','test')")
    db.execute("INSERT INTO projects (id, slug, path, name) VALUES (1,'p','/p','proj')")
    db.execute("INSERT INTO sessions (id, project_id, title) VALUES ('s',1,'s')")
    for i, (d, c) in enumerate(sorted(day_costs.items())):
        db.execute("""INSERT INTO requests (uuid, session_id, project_id, prompt_id, ts, day, model, priced_as,
                      billable_tokens, est_cost_usd, is_sidechain, agent)
                      VALUES (?, 's', 1, 1, ?, ?, 'claude-opus-5', 'claude-opus-5', 1000, ?, 0, 'claude')""",
                   (f"u{i}", d + "T12:00:00Z", d, c))
    db.commit(); db.close()
    return path


def days_back(n, cost):
    return {(TODAY - timedelta(days=i)).isoformat(): cost for i in range(1, n + 1)}


class TestForecast(unittest.TestCase):
    def analytics(self, day_costs):
        a = Analytics(make_db(day_costs))
        a._today = TODAY          # test seam added in Step 3
        return a

    def test_partial_today_excluded_from_rate(self):
        dc = days_back(14, 100.0); dc[TODAY.isoformat()] = 1.0
        fc = self.analytics(dc).forecast({})
        self.assertAlmostEqual(fc["scenarios"]["expected"]["daily_rate"], 100.0)

    def test_band_scales_with_sqrt_of_days_left(self):
        dc = days_back(14, 100.0)
        for i in range(1, 15, 2):
            dc[(TODAY - timedelta(days=i)).isoformat()] = 200.0   # sd = 50
        a = self.analytics(dc); fc = a.forecast({})
        left = a.billing_period()["remaining_days"]
        exp, hi = fc["scenarios"]["expected"]["end_of_period_cost"], fc["scenarios"]["high"]["end_of_period_cost"]
        self.assertAlmostEqual(hi - exp, 50.0 * left ** 0.5, places=3)

    def test_insufficient_history_has_no_bands(self):
        fc = self.analytics(days_back(3, 100.0)).forecast({})
        self.assertTrue(fc["insufficient_history"])
        self.assertNotIn("high", fc["scenarios"])

    def test_no_end_of_day_extrapolation(self):
        fc = self.analytics(days_back(14, 100.0)).forecast({})
        self.assertNotIn("end_of_day_cost", fc)
        self.assertNotIn("end_of_week_cost", fc)


class TestAnomalies(unittest.TestCase):
    def test_zero_cost_days_do_not_make_normal_days_spikes(self):
        dc = days_back(30, 400.0)
        a = Analytics(make_db(dc)); a._today = TODAY
        # add 60 $0 Cursor days: they are unpriced and must not drag the baseline down
        con = sqlite3.connect(a._db_path)
        for i in range(31, 91):
            d = (TODAY - timedelta(days=i)).isoformat()
            con.execute("""INSERT INTO requests (uuid, session_id, project_id, prompt_id, ts, day, model, priced_as,
                           billable_tokens, est_cost_usd, is_sidechain, agent)
                           VALUES (?, 's', 1, 1, ?, ?, 'cursor', 'cursor', 0, 0, 0, 'cursor')""",
                        (f"c{i}", d + "T12:00:00Z", d))
        con.commit(); con.close()
        types = [x["type"] for x in a.anomalies({})["anomalies"]]
        self.assertNotIn("daily_spike", types)

    def test_a_real_spike_is_found_with_robust_score(self):
        dc = days_back(30, 100.0); dc[(TODAY - timedelta(days=3)).isoformat()] = 1500.0
        a = Analytics(make_db(dc)); a._today = TODAY
        spikes = [x for x in a.anomalies({})["anomalies"] if x["type"] == "daily_spike"]
        self.assertEqual(len(spikes), 1)
        self.assertEqual(spikes[0]["date"], (TODAY - timedelta(days=3)).isoformat())
