"""Tooltip labels must be escaped. Run: python3 -m unittest tests.test_charts_escape -v"""
import os, shutil, subprocess, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@unittest.skipUnless(shutil.which("node"), "node not installed")
class TestEsc(unittest.TestCase):
    def test_esc_exported_and_escapes(self):
        js = ("import {esc} from '" + os.path.join(ROOT, "web", "charts.js").replace("\\", "/") + "';"
              "const s = esc('<img src=x onerror=alert(1)>&\"');"
              "if (s !== '&lt;img src=x onerror=alert(1)&gt;&amp;&quot;') { console.error(s); process.exit(1) }")
        # charts.js touches document lazily, so importing it under node is safe
        r = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
