"""load_settings() defensive coercion of settings.local.json.

Run: python3 -m unittest tests.test_settings -v
"""
import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finops import analytics as finops_analytics


class TestLoadSettingsCoercion(unittest.TestCase):
    def setUp(self):
        self._orig = finops_analytics.LOCAL_SETTINGS_PATH
        self._tmp = tempfile.mktemp(suffix=".json", prefix="finops-local-")
        finops_analytics.LOCAL_SETTINGS_PATH = self._tmp

    def tearDown(self):
        finops_analytics.LOCAL_SETTINGS_PATH = self._orig
        if os.path.exists(self._tmp):
            os.remove(self._tmp)

    def _write(self, obj):
        with open(self._tmp, "w") as fh:
            if isinstance(obj, str):
                fh.write(obj)
            else:
                json.dump(obj, fh)

    def test_non_dict_budgets_section_falls_back_to_default(self):
        self._write({"budgets": "not a dict"})
        s = finops_analytics.load_settings()
        self.assertIsInstance(s["budgets"], dict)
        self.assertIsNone(s["budgets"]["monthly_usd"])

    def test_non_numeric_leaf_falls_back_to_default(self):
        self._write({"budgets": {"monthly_usd": "five hundred"}})
        s = finops_analytics.load_settings()
        self.assertIsNone(s["budgets"]["monthly_usd"])

    def test_valid_leaf_is_kept(self):
        self._write({"budgets": {"monthly_usd": 500}})
        s = finops_analytics.load_settings()
        self.assertEqual(s["budgets"]["monthly_usd"], 500)

    def test_bad_per_project_usd_falls_back(self):
        self._write({"budgets": {"per_project_usd": {"a": "x"}}})
        s = finops_analytics.load_settings()
        self.assertEqual(s["budgets"]["per_project_usd"], {})

    def test_bad_alert_thresholds_falls_back(self):
        self._write({"alert_thresholds_pct": ["x", 50]})
        s = finops_analytics.load_settings()
        self.assertEqual(s["alert_thresholds_pct"], [50, 75, 90, 100])

    def test_malformed_json_falls_back_entirely(self):
        self._write("{not json")
        s = finops_analytics.load_settings()
        self.assertIsInstance(s["budgets"], dict)
        self.assertIsNone(s["budgets"]["monthly_usd"])


if __name__ == "__main__":
    unittest.main()
