"""Statusline payload and install. Run: python3 -m unittest tests.test_integrate -v"""
import io, json, os, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finops import integrate


def run_statusline(payload):
    out = io.StringIO()
    with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), \
            mock.patch.object(sys, "stdout", out):
        integrate.statusline()
    return out.getvalue().strip()


class TestStatuslinePayload(unittest.TestCase):
    def test_documented_fields(self):
        line = run_statusline({"model": {"display_name": "Opus"},
                               "context_window": {"used_percentage": 8.4},
                               "rate_limits": {"five_hour": {"used_percentage": 41}}})
        self.assertEqual(line, "Opus · 8% ctx · 41% 5h")

    def test_missing_fields_still_prints_model(self):
        self.assertEqual(run_statusline({"model": {"display_name": "Sonnet"}}), "Sonnet")


class TestInstall(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(prefix="finops-cc-"), "settings.json")
        self.p = mock.patch.object(integrate, "SETTINGS", self.path)
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def read(self):
        with open(self.path) as fh:
            return json.load(fh)

    def test_installs_documented_object_shape(self):
        r = integrate.install_statusline()
        self.assertTrue(r["ok"])
        sl = self.read()["statusLine"]
        self.assertEqual(sl["type"], "command")
        self.assertTrue(sl["command"].endswith("--statusline"))
        self.assertEqual(integrate.statusline_state(), "ours")

    def test_command_survives_spaces_in_path(self):
        with mock.patch.object(integrate.shutil, "which", return_value=None), \
                mock.patch.object(integrate.os.path, "abspath", return_value="/a b/finops/integrate.py"):
            cmd = integrate._command()
        self.assertIn("'/a b/run.py'", cmd)

    def test_leaves_someone_elses_statusline_alone(self):
        with open(self.path, "w") as fh:
            json.dump({"statusLine": {"type": "command", "command": "~/.claude/mine.sh"}, "x": 1}, fh)
        r = integrate.install_statusline()
        self.assertFalse(r["ok"])
        self.assertEqual(self.read()["statusLine"]["command"], "~/.claude/mine.sh")
        self.assertEqual(integrate.statusline_state(), "other")

    def test_remove(self):
        integrate.install_statusline()
        integrate.install_statusline(remove=True)
        self.assertNotIn("statusLine", self.read())
        self.assertIsNone(integrate.statusline_state())
