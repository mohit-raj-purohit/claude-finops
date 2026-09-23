"""Scorecard dimensions are measured and non-overlapping. Run: python3 -m unittest tests.test_scorecard -v"""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finops.analytics import Analytics
from tests.test_waste import make_db


class TestScorecard(unittest.TestCase):
    def test_dimensions(self):
        a = Analytics(make_db([{"id": "s", "reads": 1_000_000, "writes5": 100_000, "writes1": 0,
                                "cost": 10.0, "model": "claude-sonnet-5"}]))
        sc = a.scorecard({})
        self.assertEqual([d["name"] for d in sc["dimensions"]],
                         ["Context share", "Cache break-even", "Budget adherence"])
        self.assertNotIn("grade", sc)
        cbe = sc["dimensions"][1]
        # discount = 1e6*(2.0-0.2)/1e6 = 1.8 ; premium = 1e5*(2.5-2.0)/1e6 = 0.05
        # cache_cost = 1e6*0.2/1e6 + 1e5*2.5/1e6 = 0.2 + 0.25 = 0.45
        # margin = (1.8 - 0.05) / 0.45 = 3.89 -> clamps to 1.0 -> score 100
        self.assertEqual(cbe["score"], 100)


if __name__ == "__main__":
    unittest.main()
