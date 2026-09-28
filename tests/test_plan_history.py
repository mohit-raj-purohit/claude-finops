"""Plan-limit history parsing. Run: python3 -m unittest tests.test_plan_history -v"""
import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finops import plan_history


def write(obj):
    p = tempfile.mktemp(suffix=".json", prefix="finops-plan-")
    with open(p, "w") as fh:
        fh.write(obj if isinstance(obj, str) else json.dumps(obj))
    return p


def s(t, fh, sd, org="o1"):
    return {"t": t, "org": org, "u": {"fh": fh, "sd": sd}}


class TestPlanHistory(unittest.TestCase):
    def test_series_and_summary(self):
        r = plan_history.history(write({"version": 2, "samples": [
            s(1000, 10, 5), s(2000, 95, 20), s(3000, 100, 30), s(4000, 40, 31), s(5000, 91, 32)]}))
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["series"]), 5)
        self.assertEqual(r["series"][0], {"t": "1970-01-01T00:00:01Z", "five_hour": 10.0, "weekly": 5.0})
        sm = r["summary"]
        self.assertEqual(sm["five_hour_peak"], 100.0)
        self.assertEqual(sm["five_hour_ge90"], 2)     # two separate runs at 90%+
        self.assertEqual(sm["five_hour_hit100"], 1)
        self.assertEqual(sm["weekly_peak"], 32.0)

    def test_missing_file(self):
        r = plan_history.history("/nonexistent/plan-usage-history.json")
        self.assertFalse(r["ok"]); self.assertIn("reason", r)

    def test_malformed_json(self):
        self.assertFalse(plan_history.history(write("{not json"))["ok"])

    def test_unknown_version(self):
        self.assertFalse(plan_history.history(write({"version": 99, "samples": [s(1, 1, 1)]}))["ok"])

    def test_latest_org_wins(self):
        r = plan_history.history(write({"version": 2, "samples": [
            s(1000, 50, 5, "old"), s(2000, 60, 6, "new"), s(3000, 70, 7, "new")]}))
        self.assertEqual(r["org_count"], 2)
        self.assertEqual([x["five_hour"] for x in r["series"]], [60.0, 70.0])

    def test_bad_values_become_gaps(self):
        r = plan_history.history(write({"version": 2, "samples": [
            s(1000, "x", None), {"t": 2000, "org": "o1", "u": {}}, s(3000, 20, 2), {"t": "bad"}]}))
        self.assertEqual([x["five_hour"] for x in r["series"]], [None, None, 20.0])
        self.assertEqual(r["summary"]["five_hour_peak"], 20.0)
