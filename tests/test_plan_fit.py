"""Plan fit: reset parsing, limit windows, verdicts. Run: python3 -m unittest tests.test_plan_fit -v"""
import os, sys, unittest
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finops import plan_fit as P

DAY = 86400
T0 = datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp()


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


class FakeA:
    def one(self, sql, params=()):
        return {"requests": 3, "tokens": 1_000_000, "cache_read": 900_000, "cost": 1.5}

    def q(self, sql, params=()):
        return []


def weeks_series(peaks, five=10):
    """One sample a day, weekly % climbing to each week's peak, 5-hour at `five`."""
    out = []
    for w, peak in enumerate(peaks):
        for d in range(7):
            out.append({"t": iso(T0 + (w * 7 + d) * DAY), "weekly": round(peak * (d + 1) / 7),
                        "five_hour": five})
    return out


def hist(peaks, five=10):
    return {"ok": True, "series": weeks_series(peaks, five)}


class TestResetAt(unittest.TestCase):
    def test_date_and_zone(self):
        ref = datetime(2026, 10, 29, 5, 0, tzinfo=timezone.utc).timestamp()
        got = P.reset_at("Nov 4 at 6:30am (Asia/Calcutta)", ref)
        self.assertEqual(iso(got), "2026-11-04T01:00:00Z")

    def test_year_rolls_over(self):
        ref = datetime(2026, 12, 30, tzinfo=timezone.utc).timestamp()
        self.assertEqual(iso(P.reset_at("Jan 2 at 12am", ref))[:10], "2027-01-02")

    def test_bare_time_is_next_occurrence(self):
        ref = datetime(2026, 10, 29, 20, 0, tzinfo=timezone.utc).timestamp()
        self.assertEqual(iso(P.reset_at("2:30pm", ref)), "2026-10-30T14:30:00Z")

    def test_unparseable(self):
        self.assertIsNone(P.reset_at("soon", T0))


class TestWindows(unittest.TestCase):
    def test_split_on_drop_and_span(self):
        s = [{"t": iso(T0), "weekly": 10}, {"t": iso(T0 + DAY), "weekly": 30},
             {"t": iso(T0 + 2 * DAY), "weekly": 2},           # reset
             {"t": iso(T0 + 12 * DAY), "weekly": 5}]          # longer than a week later
        w = P.windows(s, "weekly", P.WEEK_S)
        self.assertEqual([x["peak"] for x in w], [30, 2, 5])


class TestVerdict(unittest.TestCase):
    def run_fit(self, plan, peaks, five=10):
        now = T0 + (len(peaks) * 7 + 8) * DAY
        return P.plan_fit(FakeA(), plan, hist(peaks, five), None, now=now)

    def test_downgrade_when_light(self):
        r = self.run_fit("Max 5x", [10, 12, 8])
        self.assertEqual(r["verdict"], "downgrade")
        self.assertEqual(r["suggest"], "Pro")                 # 12% x 5 = 60% < 80%
        self.assertEqual(r["saving_usd_month"], 80)

    def test_stay_when_smaller_would_run_out(self):
        r = self.run_fit("Max 5x", [50, 37, 40])
        self.assertEqual(r["verdict"], "stay")
        pro = next(p for p in r["plans"] if p["plan"] == "Pro")
        self.assertEqual(pro["weekly_peak_pct"], 250)
        self.assertAlmostEqual(pro["runs_out_day"], 2.8)
        self.assertIn("day 3 of 7", r["reason"])

    def test_upgrade_when_hitting_limit(self):
        r = self.run_fit("Max 5x", [95, 92, 100])
        self.assertEqual(r["verdict"], "upgrade")
        self.assertEqual(r["suggest"], "Max 20x")

    def test_top_plan_hitting_limit_stays(self):
        self.assertEqual(self.run_fit("Max 20x", [95, 100])["verdict"], "stay")

    def test_busy_five_hour_blocks_downgrade(self):
        # weekly is light, but 5-hour windows at 30% would be 150% on Pro
        self.assertEqual(self.run_fit("Max 5x", [10, 10, 10], five=30)["verdict"], "stay")

    def test_unknown_plan(self):
        self.assertEqual(self.run_fit("Team", [10, 10])["verdict"], "unknown")

    def test_no_history(self):
        r = P.plan_fit(FakeA(), "Max 5x", {"ok": False, "reason": "none"}, None, now=T0)
        self.assertEqual((r["verdict"], r["reason"]), ("unknown", "none"))

    def test_zero_week_is_not_observed(self):
        r = self.run_fit("Max 5x", [50, 0, 40])
        self.assertEqual(r["history"]["weeks"], 2)

    def test_early_history_says_so(self):
        r = self.run_fit("Max 5x", [10])
        self.assertEqual(r["confidence"], "low")
        self.assertIn("early", r["reason"])


class TestAdvice(unittest.TestCase):
    def fit(self, plan, peaks, five=10):
        now = T0 + (len(peaks) * 7 + 8) * DAY
        return P.plan_fit(FakeA(), plan, hist(peaks, five), None, now=now)["advice"]

    def test_stay_says_neither_down_nor_up(self):
        ad = self.fit("Max 5x", [50, 37, 40])
        self.assertTrue(ad["decision"][0].startswith("Don't move down to Pro"))
        self.assertTrue(ad["decision"][1].startswith("Don't move up to Max 20x"))
        self.assertEqual(ad["decision"][-1], "Stay on Max 5x.")
        self.assertEqual(ad["utilization"]["status"], "under-used")
        self.assertEqual(ad["utilization"]["unused_pct"], 60)
        self.assertTrue(ad["tips"])

    def test_downgrade_decision(self):
        ad = self.fit("Max 5x", [10, 12, 8])
        self.assertEqual(ad["decision"][-1], "Switch to Pro.")

    def test_well_used_has_no_tips(self):
        ad = self.fit("Max 5x", [60, 70, 65])
        self.assertEqual(ad["utilization"]["status"], "well used")
        self.assertEqual(ad["tips"], [])

    def test_value_multiple(self):
        class A(FakeA):
            def q(self, sql, params=()):
                return [{"day": "2026-10-01", "tokens": 10, "cost": 280.0}] if "GROUP BY day" in sql else []
        r = P.plan_fit(A(), "Max 5x", hist([50, 40]), None, now=T0 + 22 * DAY)
        self.assertEqual(r["advice"]["value"], {"paid_usd": 100, "api_equivalent_usd_month": 300.0,
                                                "multiple": 3.0})


class TestCurrentWindows(unittest.TestCase):
    def test_tokens_and_per_pct(self):
        read = datetime(2026, 10, 29, 5, 0, tzinfo=timezone.utc).timestamp()
        u = {"fetched_at": read, "limits": [
            {"label": "Current week (all models)", "pct": 5.0, "resets": "Nov 4 at 6:30am (Asia/Calcutta)"},
            {"label": "Current session", "pct": 2.0, "resets": "Oct 29 at 2:30pm (Asia/Calcutta)"}]}
        wk, s = P.current_windows(FakeA(), u, read + 10 * 3600)
        self.assertEqual(wk["start"], "2026-10-28T01:00:00.000Z")
        self.assertEqual(wk["tokens_per_pct"], 200_000)
        self.assertFalse(wk["reset_passed"])
        self.assertIsNone(s["tokens_per_pct"])                # 2% is too small to divide by
        self.assertTrue(s["reset_passed"])


if __name__ == "__main__":
    unittest.main()
