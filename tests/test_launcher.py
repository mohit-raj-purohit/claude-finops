"""Launcher smoke tests. Run: python3 -m unittest tests.test_launcher -v"""
import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _package_version():
    with open(os.path.join(ROOT, "package.json")) as fh:
        return str(json.load(fh).get("version") or "").strip()


class TestLauncher(unittest.TestCase):
    def test_help_exits_zero(self):
        r = subprocess.run([sys.executable, "run.py", "--help"], cwd=ROOT,
                            capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("claude-finops", r.stdout)

    def test_version_exits_zero_and_matches_package_json(self):
        r = subprocess.run([sys.executable, "run.py", "--version", "--no-update-check"],
                            cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(_package_version(), r.stdout)


if __name__ == "__main__":
    unittest.main()
