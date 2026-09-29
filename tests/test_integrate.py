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


class TestGuardInstall(unittest.TestCase):
    setUp, tearDown, read = TestInstall.setUp, TestInstall.tearDown, TestInstall.read
    MINE = {"matcher": "Bash", "hooks": [{"type": "command", "command": "~/.claude/check.sh"}]}

    def seed(self):
        with open(self.path, "w") as fh:
            json.dump({"hooks": {"PreToolUse": [self.MINE],
                                 "Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]},
                       "model": "opus"}, fh)

    def test_install_adds_one_entry_and_keeps_other_hooks(self):
        self.seed()
        self.assertTrue(integrate.install_guard()["ok"])
        s = self.read()
        pre = s["hooks"]["PreToolUse"]
        self.assertEqual(pre[0], self.MINE)
        self.assertEqual(len(pre), 2)
        ours = pre[1]["hooks"][0]
        self.assertIn("--guard", ours["command"].split())
        self.assertTrue(ours["command"].endswith("|| true"))
        self.assertEqual(ours["timeout"], 10)
        self.assertEqual(pre[1]["matcher"], "")
        self.assertIn("Stop", s["hooks"])
        self.assertEqual(s["model"], "opus")
        self.assertTrue(os.path.exists(self.path + ".finops-backup"))
        self.assertEqual(integrate.guard_state(), "installed")

    def test_second_install_is_a_no_op(self):
        integrate.install_guard()
        integrate.install_guard()
        self.assertEqual(len(self.read()["hooks"]["PreToolUse"]), 1)

    def test_uninstall_removes_only_ours(self):
        self.seed()
        integrate.install_guard()
        integrate.install_guard(remove=True)
        s = self.read()
        self.assertEqual(s["hooks"]["PreToolUse"], [self.MINE])
        self.assertIn("Stop", s["hooks"])
        self.assertIsNone(integrate.guard_state())

    def test_uninstall_cleans_up_empty_sections(self):
        integrate.install_guard()
        integrate.install_guard(remove=True)
        self.assertNotIn("hooks", self.read())


class TestGuardCommand(unittest.TestCase):
    """The hook must run this installation's code, and must never block a tool call."""
    setUp, tearDown, read = TestInstall.setUp, TestInstall.tearDown, TestInstall.read

    def installed_command(self):
        integrate.install_guard()
        return self.read()["hooks"]["PreToolUse"][0]["hooks"][0]["command"]

    def test_runs_this_installation_not_whatever_is_on_path(self):
        # an older claude-finops on PATH does not know --guard (it exits 2, which
        # Claude Code treats as "block this tool call")
        with mock.patch.object(integrate.shutil, "which", return_value="/usr/local/bin/claude-finops"):
            cmd = self.installed_command()
        self.assertNotIn("/usr/local/bin/claude-finops", cmd)
        root = os.path.dirname(os.path.dirname(os.path.abspath(integrate.__file__)))
        self.assertIn(os.path.join(root, "run.py"), cmd)

    def test_command_can_never_exit_with_the_blocking_code(self):
        import subprocess
        cmd = self.installed_command().replace("--guard", "--no-such-flag-xyz")
        env = dict(os.environ, CLAUDE_FINOPS_HOME=tempfile.mkdtemp(prefix="finops-cmd-"))
        r = subprocess.run(cmd, shell=True, input="{}", capture_output=True, text=True, timeout=30, env=env)
        self.assertEqual(r.returncode, 0)

    def test_old_style_entry_is_still_recognised_and_removed(self):
        with open(self.path, "w") as fh:
            json.dump({"hooks": {"PreToolUse": [{"matcher": "", "hooks": [
                {"type": "command", "command": "/x/bin/claude-finops --guard", "timeout": 10}]}]}}, fh)
        self.assertEqual(integrate.guard_state(), "installed")
        integrate.install_guard(remove=True)
        self.assertNotIn("hooks", self.read())
