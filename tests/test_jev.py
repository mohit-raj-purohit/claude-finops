"""Jev page: plugin install, API key in Claude Code settings, where Jev fits.
Run: python3 -m unittest tests.test_jev -v"""
import json, os, sqlite3, subprocess, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finops import integrate, jev
from finops.analytics import Analytics
from finops.etl import SCHEMA

KEY = "ts-live-" + "k" * 24 + "WXYZ"


class TestKey(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(prefix="finops-jev-"), "settings.json")
        with open(self.path, "w") as fh:
            json.dump({"model": "opus", "env": {"HTTPS_PROXY": "http://proxy:8080"},
                       "hooks": {"Stop": []}}, fh)
        self.p = [mock.patch.object(integrate, "SETTINGS", self.path),
                  mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": ""})]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()

    def read(self):
        with open(self.path) as fh:
            return json.load(fh)

    def test_save_keeps_everything_else_and_backs_up(self):
        jev.save_key(KEY)
        s = self.read()
        self.assertEqual(s["env"]["TYPESAFE_API_KEY"], KEY)
        self.assertEqual(s["env"]["HTTPS_PROXY"], "http://proxy:8080")
        self.assertEqual(s["model"], "opus")
        self.assertIn("hooks", s)
        self.assertTrue(os.path.exists(self.path + ".finops-backup"))

    def test_paste_whitespace_is_stripped(self):
        jev.save_key("  " + KEY + "\n")
        self.assertEqual(self.read()["env"]["TYPESAFE_API_KEY"], KEY)

    def test_rejects_bad_keys(self):
        for bad in ("", "short", "has a space in it 12345678"):
            with self.assertRaises(ValueError):
                jev.save_key(bad)
        self.assertNotIn("TYPESAFE_API_KEY", self.read()["env"])

    def test_status_never_contains_the_key(self):
        jev.save_key(KEY)
        st = jev.key_status()
        self.assertEqual(st, {"source": "claude_settings", "last4": "WXYZ"})
        self.assertNotIn(KEY, json.dumps(st))

    def test_remove_deletes_only_ours(self):
        jev.save_key(KEY)
        jev.remove_key()
        self.assertEqual(self.read()["env"], {"HTTPS_PROXY": "http://proxy:8080"})
        self.assertIsNone(jev.key_status()["source"])

    def test_remove_drops_an_env_left_empty(self):
        with open(self.path, "w") as fh:
            json.dump({"model": "opus"}, fh)
        jev.save_key(KEY)
        jev.remove_key()
        self.assertEqual(self.read(), {"model": "opus"})

    def test_shell_value_is_reported(self):
        with mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": "shell-key-000000000000ABCD"}):
            self.assertEqual(jev.key_status()["source"], "shell")


class TestPlugin(unittest.TestCase):
    def run_result(self, out, code=0):
        return subprocess.CompletedProcess([], code, stdout=out, stderr="")

    def test_status_parses_plugin_list(self):
        listing = json.dumps([{"id": "brag@brag", "version": "1"},
                              {"id": "typesafe@typesafe-ai", "version": "0.4.0", "enabled": True}])
        with mock.patch.object(jev.shutil, "which", return_value="/bin/claude"), \
                mock.patch.object(jev.subprocess, "run", return_value=self.run_result(listing)) as run:
            st = jev.status()
        self.assertEqual(st["installed"], True)
        self.assertEqual(st["version"], "0.4.0")
        self.assertEqual(run.call_args[0][0], ["/bin/claude", "plugin", "list", "--json"])

    def test_status_survives_non_json_and_missing_claude(self):
        with mock.patch.object(jev.shutil, "which", return_value="/bin/claude"), \
                mock.patch.object(jev.subprocess, "run", return_value=self.run_result("oops")):
            self.assertFalse(jev.status()["installed"])
        with mock.patch.object(jev.shutil, "which", return_value=None):
            st = jev.status()
        self.assertFalse(st["claude"])
        self.assertFalse(st["installed"])

    def test_install_runs_the_two_official_commands(self):
        calls = []
        with mock.patch.object(jev.shutil, "which", return_value="/bin/claude"), \
                mock.patch.object(jev, "_run", side_effect=lambda cmd, log: calls.append(cmd)):
            jev.install(lambda line: None)
        self.assertEqual(calls, [["/bin/claude", "plugin", "marketplace", "add", "typesafe-ai/skills"],
                                 ["/bin/claude", "plugin", "install", "typesafe@typesafe-ai"]])

    def test_install_continues_when_marketplace_already_added(self):
        calls = []

        def fake(cmd, log):
            calls.append(cmd)
            if "marketplace" in cmd:
                raise RuntimeError("Marketplace 'typesafe-ai' is already installed")
        with mock.patch.object(jev.shutil, "which", return_value="/bin/claude"), \
                mock.patch.object(jev, "_run", side_effect=fake):
            jev.install(lambda line: None)
        self.assertEqual(calls[-1][2], "install")

    def test_other_marketplace_errors_stop_the_install(self):
        with mock.patch.object(jev.shutil, "which", return_value="/bin/claude"), \
                mock.patch.object(jev, "_run", side_effect=RuntimeError("network down")):
            with self.assertRaises(RuntimeError):
                jev.install(lambda line: None)

    def test_uninstall(self):
        calls = []
        with mock.patch.object(jev.shutil, "which", return_value="/bin/claude"), \
                mock.patch.object(jev, "_run", side_effect=lambda cmd, log: calls.append(cmd)):
            jev.uninstall(lambda line: None)
        self.assertEqual(calls, [["/bin/claude", "plugin", "uninstall", "typesafe@typesafe-ai"]])

    def test_run_logs_output_and_carries_it_in_errors(self):
        seen = []
        jev._run([sys.executable, "-c", "print('one'); print('two')"], seen.append)
        self.assertEqual(seen, ["one", "two"])
        with self.assertRaisesRegex(RuntimeError, "already installed"):
            jev._run([sys.executable, "-c",
                      "import sys; print('Marketplace is already installed', file=sys.stderr); sys.exit(1)"],
                     lambda line: None)

    def test_missing_claude_is_a_plain_error(self):
        with mock.patch.object(jev.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "claude"):
                jev.install(lambda line: None)


if __name__ == "__main__":
    unittest.main()
