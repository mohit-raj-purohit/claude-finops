"""Demo data for the guide screenshot. Run: python3 -m unittest tests.test_demo_data -v"""
import json, os, sqlite3, sys, tempfile, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import demo_data


class TestDemoData(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="finops-demo-")
        self.out = demo_data.build(self.home)

    def test_builds_a_warehouse_with_demo_projects(self):
        db = sqlite3.connect(os.path.join(self.home, "data", "finops.db"))
        names = {r[0] for r in db.execute("SELECT name FROM projects")}
        self.assertTrue({"shop-app", "blog", "data-pipeline"} <= names)
        sizes = [r[0] for r in db.execute("SELECT billable_tokens FROM sessions ORDER BY 1")]
        self.assertGreaterEqual(len(sizes), 20)
        self.assertGreater(sizes[-1], 10 * sizes[len(sizes) // 2])   # a real spread

    def test_writes_example_budgets(self):
        with open(os.path.join(self.home, "settings.local.json")) as fh:
            s = json.load(fh)
        self.assertGreater(s["budgets"]["monthly_usd"], 0)
        self.assertGreater(s["guard"]["session_tokens"], 0)

    def test_contains_nothing_from_the_real_machine(self):
        real = os.path.expanduser("~")
        for dirpath, _, files in os.walk(self.home):
            for f in files:
                if f.endswith((".jsonl", ".json")):
                    with open(os.path.join(dirpath, f), errors="replace") as fh:
                        self.assertNotIn(real, fh.read(), f)


if __name__ == "__main__":
    unittest.main()
