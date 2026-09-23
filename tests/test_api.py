"""HTTP layer tests. Run: python3 -m unittest tests.test_api -v"""
import json, os, sqlite3, sys, tempfile, threading, unittest, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finops import api as finops_api
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
        cls.srv = finops_api.Server(("127.0.0.1", 0), finops_api.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        os.path.exists(cls.db_path) and os.remove(cls.db_path)

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
