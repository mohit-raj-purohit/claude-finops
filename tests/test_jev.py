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


class TestFit(unittest.TestCase):
    """Where Jev fits: short, tool-free prompts worded as a decision."""

    def make(self, prompts):
        path = tempfile.mktemp(suffix=".db", prefix="finops-jev-")
        db = sqlite3.connect(path)
        db.executescript(SCHEMA)
        db.execute("INSERT INTO meta VALUES ('built_at','test')")
        db.execute("INSERT INTO projects (id, slug, path, name) VALUES (1,'p','/p','proj')")
        db.execute("INSERT INTO sessions (id, project_id) VALUES ('s', 1)")
        for i, (text, reqs, tools, out, cost) in enumerate(prompts, 1):
            db.execute("""INSERT INTO prompts (id, uuid, session_id, project_id, text, request_count,
                          tool_calls, output_tokens, input_tokens, cache_read_tokens, cache_write_tokens,
                          est_cost_usd, agent, norm_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (i, f"u{i}", "s", 1, text, reqs, tools, out, 1000, 9000, 0, cost, "claude", f"h{i}"))
            db.execute("""INSERT INTO requests (uuid, session_id, project_id, prompt_id, ts, day, model,
                          est_cost_usd, billable_tokens, agent) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                       (f"r{i}", "s", 1, i, "2026-09-01T00:00:00Z", "2026-09-01", "m", cost, 10000, "claude"))
        db.commit(); db.close()
        return Analytics(path)

    def test_counts_decisions_only(self):
        a = self.make([
            ("Is this a bug or a feature request? yes or no", 1, 0, 5, 0.20),
            ("classify this ticket as billing, auth or other: /Users/me/x.txt", 1, 0, 3, 0.10),
            ("write a function that classifies tickets", 1, 0, 200, 0.50),     # a build request
            ("which one of these files is relevant?", 4, 6, 80, 0.90),          # used tools
            ("refactor the parser", 9, 20, 900, 1.30),
            ("This session is being continued from a previous conversation. Is it a bug? yes or no", 1, 0, 9, 0.0),
        ])
        f = jev.fit(a, {})
        self.assertEqual(f["prompts"], 2)
        self.assertAlmostEqual(f["claude_cost"], 0.30)
        self.assertAlmostEqual(f["jev_cost"], 2 * 10000 * jev.JEV_USD_PER_INPUT_TOKEN)
        self.assertAlmostEqual(f["share_pct"], 100 * 0.30 / 3.00, places=3)
        self.assertEqual(f["total_prompts"], 6)

    def test_examples_are_masked(self):
        a = self.make([("classify mail from bob@example.com in /Users/me/proj/file.py ticket 123456", 1, 0, 3, 0.1)])
        ex = jev.fit(a, {})["examples"][0]["text"]
        self.assertNotIn("bob@example.com", ex)
        self.assertNotIn("/Users/me", ex)
        self.assertNotIn("123456", ex)


if __name__ == "__main__":
    unittest.main()
