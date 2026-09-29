"""HTTP layer tests. Run: python3 -m unittest tests.test_api -v"""
import json, os, sqlite3, sys, tempfile, threading, unittest, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finops import api as finops_api
from finops import analytics as finops_analytics
from finops.analytics import Analytics
from finops.etl import SCHEMA


def make_db():
    path = tempfile.mktemp(suffix=".db", prefix="finops-test-")
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.execute("INSERT INTO meta (key, value) VALUES ('built_at', 'test')")
    db.execute("INSERT INTO meta (key, value) VALUES ('schema_version', '2')")
    db.execute("INSERT INTO projects (id, slug, path, name) VALUES (1, 'p', '/p', 'proj')")
    db.execute("INSERT INTO sessions (id, project_id, title) VALUES ('A', 1, 'title A')")
    for i, (seq, ctx, cost) in enumerate([(0, 50_000, 1.0), (1, 60_000, 2.0)]):
        db.execute("""INSERT INTO requests (uuid, session_id, project_id, prompt_id, ts, day, model,
                        priced_as, context_tokens, est_cost_usd, is_sidechain,
                        input_tokens, output_tokens, thinking_tokens, cache_read_tokens,
                        cache_write_tokens, billable_tokens)
                      VALUES (?, 'A', 1, 1, ?, '2026-01-01', 'm', 'm', ?, ?, 0, 10, 0, ?, 0, ?, ?)""",
                   (f"u{i}", f"2026-01-01T00:{seq:02d}:00Z", ctx, cost, ctx, ctx + 10, ctx + 10))
    db.commit()
    db.close()
    return path


class ServerFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_path = make_db()
        finops_api.A = Analytics(cls.db_path)
        # Never let settings POST tests touch the developer's real
        # ~/.claude-finops/settings.local.json — point both modules' copies of
        # the path at a throwaway file for the duration of this fixture.
        cls._orig_api_local = finops_api.LOCAL_SETTINGS_PATH
        cls._orig_analytics_local = finops_analytics.LOCAL_SETTINGS_PATH
        cls.local_settings_path = tempfile.mktemp(suffix=".json", prefix="finops-test-local-")
        finops_api.LOCAL_SETTINGS_PATH = cls.local_settings_path
        finops_analytics.LOCAL_SETTINGS_PATH = cls.local_settings_path
        cls.srv = finops_api.Server(("127.0.0.1", 0), finops_api.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        os.path.exists(cls.db_path) and os.remove(cls.db_path)
        os.path.exists(cls.local_settings_path) and os.remove(cls.local_settings_path)
        finops_api.LOCAL_SETTINGS_PATH = cls._orig_api_local
        finops_analytics.LOCAL_SETTINGS_PATH = cls._orig_analytics_local

    def get(self, path, headers=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers=headers or {})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    def post(self, path, body, headers=None):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        h = {"Content-Type": "application/json"}; h.update(headers or {})
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers=h, method="POST")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")


class TestRemovedRoutes(ServerFixture):
    def test_model_evidence_gone(self):
        self.assertEqual(self.get("/api/model_evidence")[0], 404)

    def test_trial_gone(self):
        self.assertEqual(self.get("/api/trial")[0], 404)
        self.assertEqual(self.post("/api/trial/run", {})[0], 404)


class TestHardening(ServerFixture):
    def test_settings_rejects_cross_origin(self):
        code, _ = self.post("/api/settings", {"budgets": {"monthly_usd": 5}},
                            headers={"Origin": "http://evil.example"})
        self.assertEqual(code, 403)

    def test_settings_rejects_wrong_type(self):
        code, body = self.post("/api/settings", {"budgets": "notadict"}, headers={"X-FinOps-Action": "1"})
        self.assertEqual(code, 400)

    def test_bad_json_is_400(self):
        code, _ = self.post("/api/settings", b"{bad", headers={"X-FinOps-Action": "1"})
        self.assertEqual(code, 400)

    def test_bad_int_param_is_400(self):
        self.assertEqual(self.get("/api/sessions?limit=abc")[0], 400)

    def test_traceback_not_leaked(self):
        code, body = self.get("/api/prompt/abc")
        self.assertEqual(code, 400)
        self.assertNotIn("Traceback", json.dumps(body))

    def test_host_header_checked(self):
        self.assertEqual(self.get("/api/overview", headers={"Host": "evil.example"})[0], 403)

    def test_nosniff_header(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/overview")
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.headers.get("X-Content-Type-Options"), "nosniff")

    def test_bad_projects_filter_is_400(self):
        self.assertEqual(self.get("/api/overview?projects=x")[0], 400)

    def test_settings_rejects_non_numeric_budget_leaf(self):
        code, _ = self.post("/api/settings", {"budgets": {"monthly_usd": "x"}},
                            headers={"X-FinOps-Action": "1"})
        self.assertEqual(code, 400)

    def test_settings_rejects_non_numeric_per_project_usd(self):
        code, _ = self.post("/api/settings", {"budgets": {"per_project_usd": {"a": "x"}}},
                            headers={"X-FinOps-Action": "1"})
        self.assertEqual(code, 400)

    def test_settings_rejects_bad_alert_thresholds(self):
        code, _ = self.post("/api/settings", {"alert_thresholds_pct": [50, "x"]},
                            headers={"X-FinOps-Action": "1"})
        self.assertEqual(code, 400)

    def test_settings_accepts_valid_budget(self):
        code, body = self.post("/api/settings", {"budgets": {"monthly_usd": 500}},
                               headers={"X-FinOps-Action": "1"})
        self.assertEqual(code, 200)
        self.assertEqual(body["settings"]["budgets"]["monthly_usd"], 500)
        # written to the isolated temp settings file, never the user's real one
        with open(self.local_settings_path) as fh:
            on_disk = json.load(fh)
        self.assertEqual(on_disk["budgets"]["monthly_usd"], 500)

    def test_settings_accepts_and_persists_guard(self):
        g = {"session_tokens": 2000000, "warn_pct": [80, 75], "after_approval": "once",
             "step_pct": 50, "projects": {"/repo": {"off": True}}}
        code, body = self.post("/api/settings", {"guard": g}, headers={"X-FinOps-Action": "1"})
        self.assertEqual(code, 200)
        self.assertEqual(body["settings"]["guard"]["warn_pct"], [75, 80])
        self.assertEqual(body["settings"]["guard"]["projects"], {"/repo": {"off": True}})
        with open(self.local_settings_path) as fh:
            self.assertEqual(json.load(fh)["guard"]["session_tokens"], 2000000)

    def test_settings_rejects_bad_guard(self):
        for bad in ({"warn_pct": [120]}, {"after_approval": "sometimes"}, {"step_pct": 0},
                    {"session_tokens": "x"}, {"projects": {"/a": {"session_tokens": "x"}}}):
            code, _ = self.post("/api/settings", {"guard": bad}, headers={"X-FinOps-Action": "1"})
            self.assertEqual(code, 400, bad)

    def test_malformed_start_date_is_400(self):
        self.assertEqual(self.get("/api/overview?start=not-a-date")[0], 400)

    def test_non_numeric_min_cost_is_400(self):
        self.assertEqual(self.get("/api/sessions?min_cost=abc")[0], 400)

    def test_unexpected_internal_error_is_500_not_400(self):
        orig = finops_api.A.overview
        finops_api.A.overview = lambda *a, **k: (_ for _ in ()).throw(ValueError("boom"))
        try:
            code, body = self.get("/api/overview")
            self.assertEqual(code, 500)
            self.assertNotIn("boom", json.dumps(body))
        finally:
            finops_api.A.overview = orig


class TestPagination(ServerFixture):
    def test_sessions_reports_total_and_honours_offset(self):
        code, body = self.get("/api/sessions?limit=1&offset=0")
        self.assertEqual(code, 200)
        self.assertIn("total", body)
        self.assertLessEqual(len(body["rows"]), 1)

    def test_prompts_reports_total_and_honours_offset(self):
        code, body = self.get("/api/prompts?limit=1&offset=0")
        self.assertEqual(code, 200)
        self.assertIn("total", body)
        self.assertLessEqual(len(body["rows"]), 1)


class TestParityRoutes(ServerFixture):
    def test_heatmap_route(self):
        code, body = self.get("/api/heatmap")
        self.assertEqual(code, 200)
        self.assertEqual(len(body["cells"]), 168)

    def test_plan_history_route_never_errors(self):
        from finops import plan_history
        orig = plan_history.PLAN_HISTORY_PATH
        plan_history.PLAN_HISTORY_PATH = "/nonexistent/plan.json"
        try:
            code, body = self.get("/api/plan_history")
        finally:
            plan_history.PLAN_HISTORY_PATH = orig
        self.assertEqual(code, 200)
        self.assertFalse(body["ok"])

    def test_act_now_route(self):
        code, body = self.get("/api/act_now")
        self.assertEqual(code, 200)
        self.assertIsInstance(body["items"], list)

    def test_statusline_install_needs_action_header(self):
        self.assertEqual(self.post("/api/do/statusline", {})[0], 403)


class TestKeys(ServerFixture):
    """API keys set from the Budgets page: stored 0600, never sent back."""
    KEY = "sk-ant-admin01-" + "x" * 30 + "WXYZ"

    def setUp(self):
        from unittest import mock
        from finops import cloud
        self.secrets = tempfile.mktemp(suffix=".json", prefix="finops-test-secrets-")
        self.p = [mock.patch.object(cloud, "SECRETS_PATH", self.secrets),
                  mock.patch.dict(os.environ, {"ANTHROPIC_ADMIN_KEY": "", "CURSOR_API_KEY": ""})]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()
        os.path.exists(self.secrets) and os.remove(self.secrets)

    def save(self, provider, body, headers=None):
        return self.post(f"/api/do/key/{provider}", body, headers=headers or {"X-FinOps-Action": "1"})

    def test_store_reports_status_without_the_key(self):
        code, body = self.save("anthropic", {"value": self.KEY})
        self.assertEqual(code, 200)
        self.assertNotIn(self.KEY, json.dumps(body))
        self.assertEqual(body["keys"]["anthropic"]["source"], "stored")
        self.assertEqual(body["keys"]["anthropic"]["last4"], "WXYZ")
        code, status = self.get("/api/keys")
        self.assertNotIn(self.KEY, json.dumps(status))
        self.assertEqual(os.stat(self.secrets).st_mode & 0o777, 0o600)
        with open(self.secrets) as fh:
            self.assertEqual(json.load(fh)["anthropic_admin_key"], self.KEY)

    def test_remove(self):
        self.save("anthropic", {"value": self.KEY})
        code, body = self.save("anthropic", {"remove": True})
        self.assertEqual(code, 200)
        self.assertIsNone(body["keys"]["anthropic"]["source"])

    def test_rejects_wrong_shape_empty_and_unknown(self):
        self.assertEqual(self.save("anthropic", {"value": "sk-ant-api03-regular-key-000000"})[0], 400)
        self.assertEqual(self.save("anthropic", {"value": "  "})[0], 400)
        self.assertEqual(self.save("nope", {"value": self.KEY})[0], 400)
        self.assertFalse(os.path.exists(self.secrets))

    def test_rejects_cross_origin(self):
        code, _ = self.save("anthropic", {"value": self.KEY},
                            headers={"X-FinOps-Action": "1", "Origin": "http://evil.example"})
        self.assertEqual(code, 403)
        self.assertFalse(os.path.exists(self.secrets))

    def test_env_var_wins_and_is_reported(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {"CURSOR_API_KEY": "key_" + "e" * 30}):
            self.assertEqual(self.get("/api/keys")[1]["cursor"]["source"], "env")


class TestBudgetSuggestions(ServerFixture):
    def test_budgets_carry_suggestions(self):
        code, body = self.get("/api/budgets")
        self.assertEqual(code, 200)
        sg = body["suggest"]
        for k in ("spend_30d", "tokens_30d", "daily_avg", "daily_p90", "session_tokens"):
            self.assertIn(k, sg)
        self.assertEqual(sg["session_tokens"], sorted(sg["session_tokens"]))


class TestSessionLimitApi(ServerFixture):
    """Set limit on the Running sessions page: one session's own token limit."""

    def setUp(self):
        from unittest import mock
        from finops import guard
        self.src = tempfile.mkdtemp(prefix="finops-src-")
        os.makedirs(os.path.join(self.src, "-repo"))
        with open(os.path.join(self.src, "-repo", "sess-1.jsonl"), "w") as fh:
            for i in range(3):
                fh.write(json.dumps({"type": "assistant", "requestId": f"r{i}", "message": {
                    "id": f"m{i}", "usage": {"input_tokens": 100, "output_tokens": 0}}}) + "\n")
        self.p = [mock.patch.dict(os.environ, {"CLAUDE_PROJECTS": self.src}),
                  mock.patch.object(guard, "STATE_DIR", tempfile.mkdtemp(prefix="finops-state-"))]
        for p in self.p:
            p.start()
        if os.path.exists(self.local_settings_path):
            os.remove(self.local_settings_path)

    def tearDown(self):
        for p in self.p:
            p.stop()

    def set(self, sid, body, headers=None):
        return self.post(f"/api/do/session_limit/{sid}", body, headers=headers or {"X-FinOps-Action": "1"})

    def saved(self):
        with open(self.local_settings_path) as fh:
            return json.load(fh)["guard"]["sessions"]

    def test_get_reports_usage_and_limit(self):
        code, body = self.get("/api/guard/session/sess-1")
        self.assertEqual(code, 200)
        self.assertEqual(body["tokens"], 300)
        self.assertIsNone(body["limit"])
        self.assertIn("installed", body)

    def test_unknown_session_is_404_and_bad_id_is_400(self):
        self.assertEqual(self.get("/api/guard/session/nope")[0], 404)
        self.assertEqual(self.get("/api/guard/session/..%2Fx")[0], 400)

    def test_set_off_and_remove(self):
        code, body = self.set("sess-1", {"session_tokens": 2500})
        self.assertEqual(code, 200)
        self.assertEqual(self.saved()["sess-1"]["session_tokens"], 2500)
        self.assertIn("set_at", self.saved()["sess-1"])
        self.assertEqual(self.get("/api/guard/session/sess-1")[1]["limit"]["session_tokens"], 2500)
        self.set("sess-1", {"off": True})
        self.assertTrue(self.saved()["sess-1"]["off"])
        self.set("sess-1", {"remove": True})
        self.assertNotIn("sess-1", self.saved())

    def test_keeps_the_rest_of_the_guard_settings(self):
        self.post("/api/settings", {"guard": {"session_tokens": 9000}}, headers={"X-FinOps-Action": "1"})
        self.set("sess-1", {"session_tokens": 2500})
        with open(self.local_settings_path) as fh:
            self.assertEqual(json.load(fh)["guard"]["session_tokens"], 9000)
        # and a later Budgets save must not wipe the session limit
        self.post("/api/settings", {"guard": {"session_tokens": 8000}}, headers={"X-FinOps-Action": "1"})
        self.assertEqual(self.saved()["sess-1"]["session_tokens"], 2500)

    def test_rejects_bad_values_and_cross_origin(self):
        for bad in ({"session_tokens": 0}, {"session_tokens": "x"}, {}):
            self.assertEqual(self.set("sess-1", bad)[0], 400, bad)
        self.assertEqual(self.set("bad%20id!", {"session_tokens": 5})[0], 400)
        code, _ = self.set("sess-1", {"session_tokens": 5},
                           headers={"X-FinOps-Action": "1", "Origin": "http://evil.example"})
        self.assertEqual(code, 403)


class TestJevApi(ServerFixture):
    def setUp(self):
        from unittest import mock
        from finops import integrate, jev
        self.settings = os.path.join(tempfile.mkdtemp(prefix="finops-jevapi-"), "settings.json")
        self.p = [mock.patch.object(integrate, "SETTINGS", self.settings),
                  mock.patch.object(jev, "status", return_value={"claude": True, "installed": False,
                                                                 "version": None, "enabled": None}),
                  mock.patch.object(jev, "install", return_value={"installed": True}),
                  mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": ""})]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()

    def test_page_data(self):
        code, body = self.get("/api/jev")
        self.assertEqual(code, 200)
        self.assertEqual(set(body), {"status", "key", "fit"})
        self.assertFalse(body["status"]["installed"])
        self.assertIsNone(body["key"]["source"])
        self.assertIn("prompts", body["fit"])

    def test_key_save_remove_and_never_returned(self):
        key = "ts-" + "q" * 30 + "LAST"
        code, body = self.post("/api/do/jev/key", {"value": key}, headers={"X-FinOps-Action": "1"})
        self.assertEqual(code, 200)
        self.assertEqual(body["key"]["last4"], "LAST")
        self.assertNotIn(key, json.dumps(body))
        self.assertNotIn(key, json.dumps(self.get("/api/jev")[1]))
        with open(self.settings) as fh:
            self.assertEqual(json.load(fh)["env"]["TYPESAFE_API_KEY"], key)
        code, body = self.post("/api/do/jev/key", {"remove": True}, headers={"X-FinOps-Action": "1"})
        self.assertIsNone(body["key"]["source"])

    def test_bad_key_and_cross_origin(self):
        self.assertEqual(self.post("/api/do/jev/key", {"value": "short"},
                                   headers={"X-FinOps-Action": "1"})[0], 400)
        code, _ = self.post("/api/do/jev/key", {"value": "ts-" + "q" * 30},
                            headers={"X-FinOps-Action": "1", "Origin": "http://evil.example"})
        self.assertEqual(code, 403)
        self.assertFalse(os.path.exists(self.settings))

    def test_install_starts_a_job(self):
        code, body = self.post("/api/do/jev/install", {}, headers={"X-FinOps-Action": "1"})
        self.assertEqual(code, 200)
        self.assertIn("job", body)
