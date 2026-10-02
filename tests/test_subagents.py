"""Subagent models: measured runs, suggestions, and the settings experiment.

Run: python3 -m unittest tests.test_subagents -v
"""
import json, os, sqlite3, sys, tempfile, unittest
from datetime import datetime, timezone
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finops import integrate, subagents as SA
from finops.analytics import Analytics
from finops.etl import SCHEMA

NOW = datetime(2026, 3, 1, tzinfo=timezone.utc)


def make_db(runs):
    """runs: (agent_id, type, [(model, cost, output, context, ts), ...]); plus a main-thread row."""
    path = tempfile.mktemp(suffix=".db", prefix="finops-sub-")
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.execute("INSERT INTO meta (key, value) VALUES ('built_at', 'test')")
    db.execute("INSERT INTO projects (id, slug, path, name) VALUES (1, 'p', '/nonexistent/p', 'proj')")
    db.execute("""INSERT INTO requests (uuid, session_id, project_id, ts, day, model, est_cost_usd,
                  output_tokens, context_tokens, billable_tokens, is_sidechain, agent)
                  VALUES ('main', 'S', 1, '2026-01-01T00:00:00Z', '2026-01-01', 'claude-opus-5',
                  100.0, 10, 10, 20, 0, 'claude')""")
    n = 0
    for aid, typ, reqs in runs:
        for model, cost, out, ctx, ts in reqs:
            n += 1
            db.execute("""INSERT INTO requests (uuid, session_id, project_id, ts, day, model, est_cost_usd,
                          output_tokens, context_tokens, billable_tokens, is_sidechain, agent_id,
                          agent_type, agent)
                          VALUES (?, 'S', 1, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 'claude')""",
                       (f"u{n}", ts, ts[:10], model, cost, out, ctx, ctx + out, aid, typ))
    db.commit()
    db.close()
    return path


def run(aid, typ, model, cost, turns=1, ts="2026-01-02T00:00:00Z", out=100, ctx=1000):
    return (aid, typ, [(model, cost / turns, out, ctx, ts)] * turns)


class Base(unittest.TestCase):
    def analytics(self, runs):
        a = Analytics(make_db(runs))
        a.settings = dict(a.settings, subagents={"min_runs": 3})
        return a


class TestRuns(Base):
    def test_run_is_one_agent_id_with_summed_cost_and_turns(self):
        a = self.analytics([run("r1", "Explore", "claude-opus-5", 3.0, turns=3)])
        [r] = SA.load_runs(a, {})
        self.assertEqual((r["type"], r["turns"]), ("Explore", 3))
        self.assertAlmostEqual(r["cost"], 3.0)

    def test_mixed_model_run_filed_under_costliest_model(self):
        a = self.analytics([("r1", "Explore", [("claude-sonnet-5", 0.5, 1, 1, "2026-01-02T00:00:00Z"),
                                               ("claude-opus-5", 2.0, 1, 1, "2026-01-02T00:01:00Z")])])
        [r] = SA.load_runs(a, {})
        self.assertEqual(r["model"], "claude-opus-5")
        self.assertAlmostEqual(r["cost"], 2.5)

    def test_kpis_share_of_claude_spend(self):
        a = self.analytics([run("r1", "Explore", "claude-opus-5", 25.0)])
        k = SA.report(a, {}, home="/nonexistent")["kpis"]
        self.assertAlmostEqual(k["share_pct"], 20.0)
        self.assertEqual((k["runs"], k["types"]), (1, 1))

    def test_other_agent_filter_returns_no_types(self):
        a = self.analytics([run("r1", "Explore", "claude-opus-5", 1.0)])
        r = SA.report(a, {"agents": ["codex"]}, home="/nonexistent")
        self.assertFalse(r["claude_selected"])
        self.assertEqual(r["types"], [])


class TestSuggest(Base):
    def suggestion(self, runs, typ="Explore", custom=None):
        a = self.analytics(runs)
        with mock.patch.object(SA, "custom_agents", return_value=custom or {}):
            r = SA.report(a, {}, home="/nonexistent")
        return next(t for t in r["types"] if t["type"] == typ)["suggestion"]

    def many(self, typ, model, cost, n, prefix):
        return [run(f"{prefix}{i}", typ, model, cost) for i in range(n)]

    def test_fork_never_gets_a_model(self):
        s = self.suggestion(self.many("fork", "claude-opus-5", 2.0, 5, "f"), typ="fork")
        self.assertEqual(s["kind"], "fork")

    def test_switch_when_cheaper_model_has_enough_runs_and_lower_median(self):
        s = self.suggestion(self.many("Explore", "claude-opus-5", 1.0, 3, "o")
                            + self.many("Explore", "claude-sonnet-5", 0.2, 3, "s"))
        self.assertEqual((s["kind"], s["alias"]), ("switch", "sonnet"))
        self.assertEqual(s["fix"]["kind"], "experiment")

    def test_not_enough_runs(self):
        s = self.suggestion(self.many("Explore", "claude-opus-5", 1.0, 3, "o")
                            + self.many("Explore", "claude-sonnet-5", 0.2, 2, "s"))
        self.assertEqual(s["kind"], "not_enough")

    def test_cheaper_model_that_cost_more_per_run_is_not_suggested(self):
        s = self.suggestion(self.many("Explore", "claude-opus-5", 1.0, 5, "o")
                            + self.many("Explore", "claude-sonnet-5", 1.5, 3, "s"))
        self.assertEqual(s["kind"], "not_enough")

    def test_already_cheapest(self):
        s = self.suggestion(self.many("Explore", "claude-haiku-4-5-20251001", 0.1, 3, "h"))
        self.assertEqual(s["kind"], "cheapest")

    def test_unpriced_only(self):
        s = self.suggestion(self.many("Explore", "claude-unknown-9", 0.0, 3, "u"))
        self.assertEqual(s["kind"], "unpriced")

    def test_custom_agent_gets_file_fix(self):
        custom = {"Explore": {"path": "/x/.claude/agents/explore.md", "model": "opus"}}
        s = self.suggestion(self.many("Explore", "claude-opus-5", 1.0, 3, "o")
                            + self.many("Explore", "claude-sonnet-5", 0.2, 3, "s"), custom=custom)
        self.assertEqual(s["fix"], {"kind": "file", "path": "/x/.claude/agents/explore.md",
                                    "line": "model: sonnet", "current": "opus"})


class TestCustomAgents(unittest.TestCase):
    def test_reads_name_and_model_project_wins(self):
        home, proj = tempfile.mkdtemp(), tempfile.mkdtemp()
        for base, model in ((home, "opus"), (proj, "haiku")):
            d = os.path.join(base, ".claude", "agents")
            os.makedirs(d)
            with open(os.path.join(d, "x.md"), "w") as fh:
                fh.write(f"---\nname: searcher\nmodel: {model}\n---\nbody\n")
        found = SA.custom_agents([proj], home=home)
        self.assertEqual(found["searcher"]["model"], "haiku")


class TestExperiment(Base):
    def setUp(self):
        d = tempfile.mkdtemp(prefix="finops-exp-")
        self.settings = os.path.join(d, "settings.json")
        self.local = os.path.join(d, "settings.local.json")
        self.p1 = mock.patch.object(integrate, "SETTINGS", self.settings)
        self.p2 = mock.patch.object(SA, "LOCAL_SETTINGS_PATH", self.local)
        self.p1.start(); self.p2.start()

    def tearDown(self):
        self.p1.stop(); self.p2.stop()

    def write(self, data):
        with open(self.settings, "w") as fh:
            json.dump(data, fh) if not isinstance(data, str) else fh.write(data)

    def read(self):
        with open(self.settings) as fh:
            return json.load(fh)

    def test_start_merges_env_keeps_other_keys_and_backs_up(self):
        self.write({"model": "opus", "env": {"FOO": "1"}})
        self.assertTrue(SA.start_experiment("haiku")["ok"])
        s = self.read()
        self.assertEqual(s["env"], {"FOO": "1", SA.ENV_VAR: "haiku"})
        self.assertEqual(s["model"], "opus")
        self.assertTrue(os.path.exists(self.settings + ".finops-backup"))

    def test_start_refuses_a_value_finops_did_not_set(self):
        self.write({"env": {SA.ENV_VAR: "opus"}})
        self.assertFalse(SA.start_experiment("haiku")["ok"])
        self.assertEqual(self.read()["env"][SA.ENV_VAR], "opus")

    def test_invalid_settings_refused_untouched(self):
        self.write("{not json")
        with self.assertRaises(SA.SettingsError):
            SA.start_experiment("haiku")
        with open(self.settings) as fh:
            self.assertEqual(fh.read(), "{not json")

    def test_bad_model_rejected(self):
        with self.assertRaises(ValueError):
            SA.start_experiment("gpt")

    def test_stop_removes_ours_and_empty_env(self):
        self.write({"x": 1})
        SA.start_experiment("haiku")
        SA.stop_experiment()
        self.assertEqual(self.read(), {"x": 1})
        st = SA._state()
        self.assertIsNone(st["active"])
        self.assertEqual(st["history"][0]["model"], "haiku")

    def test_stop_leaves_a_value_the_user_changed(self):
        self.write({})
        SA.start_experiment("haiku")
        self.write({"env": {SA.ENV_VAR: "opus"}})
        r = SA.stop_experiment()
        self.assertIn("left as it is", r["message"])
        self.assertEqual(self.read()["env"][SA.ENV_VAR], "opus")
        self.assertIsNone(SA._state()["active"])

    def test_status_warns_when_value_disappears(self):
        self.write({})
        SA.start_experiment("haiku")
        self.write({})
        a = self.analytics([])
        st = SA.experiment_status(a, 3)
        self.assertIn("no longer set", st["warning"])

    def test_windows_same_length_before_capped(self):
        self.assertEqual(SA.windows("2026-02-20T00:00:00Z", "2026-02-25T00:00:00Z"),
                         ("2026-02-15T00:00:00Z", "2026-02-20T00:00:00Z", "2026-02-25T00:00:00Z"))
        b0, _, _ = SA.windows("2026-01-01T00:00:00Z", "2026-03-01T00:00:00Z")
        self.assertEqual(b0, "2025-12-02T00:00:00Z")

    def test_compare_splits_before_after_and_counts_ignored(self):
        runs = ([run(f"b{i}", "Explore", "claude-opus-5", 1.0, ts=f"2026-02-1{i + 2}T00:00:00Z") for i in range(3)]
                + [run(f"a{i}", "Explore", "claude-haiku-4-5-20251001", 0.1, ts=f"2026-02-2{i}T00:00:00Z")
                   for i in range(3)]
                + [run("x", "Explore", "claude-opus-5", 1.0, ts="2026-02-25T00:00:00Z")])
        a = self.analytics(runs)
        r = SA.compare_experiment(a, {"model": "haiku", "started_at": "2026-02-20T00:00:00Z",
                                      "stopped_at": "2026-03-01T00:00:00Z"}, 3)
        [t] = r["types"]
        self.assertEqual((t["before"]["runs"], t["after"]["runs"], t["ignored"]), (3, 3, 1))
        self.assertTrue(t["ready"])
        self.assertAlmostEqual(t["change"], -0.9)


if __name__ == "__main__":
    unittest.main()
