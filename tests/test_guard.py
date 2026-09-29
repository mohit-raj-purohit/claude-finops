"""Session guard: counting, decisions, overrides, fail-open. Run: python3 -m unittest tests.test_guard -v"""
import io, json, os, sqlite3, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finops import guard
from finops.etl import Loader, SCHEMA
from finops.analytics import Analytics


def usage(inp=10, out=90, cr=900, cw=0):
    return {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cr,
            "cache_creation_input_tokens": cw}


def asst(req, u, ts="2026-01-01T00:00:01Z", tool=None):
    content = [{"type": "tool_use", "id": tool, "name": "Bash", "input": {}}] if tool else \
        [{"type": "text", "text": "ok"}]
    return {"type": "assistant", "uuid": f"u-{req}-{ts}", "timestamp": ts, "requestId": req,
            "sessionId": "s1", "cwd": "/repo",
            "message": {"id": f"m-{req}", "model": "claude-opus-5", "role": "assistant",
                        "usage": u, "content": content}}


def prompt(text="do it", ts="2026-01-01T00:00:00Z"):
    return {"type": "user", "uuid": f"p-{ts}", "timestamp": ts, "sessionId": "s1", "cwd": "/repo",
            "message": {"role": "user", "content": text}}


def result(tool, rejected=False):
    r = {"type": "user", "uuid": f"r-{tool}", "sessionId": "s1",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": tool, "is_error": rejected,
              "content": "The user doesn't want to proceed with this tool use." if rejected else "done"}]},
         "toolUseResult": "User rejected tool use" if rejected else {"stdout": "done"}}
    return r


CFG = {"session_tokens": 1000, "warn_pct": [75, 80], "after_approval": "step", "step_pct": 25,
       "projects": {}}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="finops-guard-")
        self.proj = os.path.join(self.dir, "-repo")
        os.makedirs(self.proj)
        self.transcript = os.path.join(self.proj, "s1.jsonl")
        open(self.transcript, "w").close()
        self.p = mock.patch.object(guard, "STATE_DIR", os.path.join(self.dir, "state"))
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def append(self, *rows, path=None, newline=True):
        with open(path or self.transcript, "a") as fh:
            for r in rows:
                fh.write(json.dumps(r) + ("\n" if newline else ""))

    def call(self, cfg=CFG, tool="t-x", cwd="/repo"):
        return guard.run({"session_id": "s1", "transcript_path": self.transcript, "cwd": cwd,
                          "tool_use_id": tool, "hook_event_name": "PreToolUse"}, cfg)

    def total(self):
        state = {}
        return guard.count_session(self.transcript, "s1", state)[0]


class TestCounting(Base):
    def test_billable_includes_cache_reads_and_writes(self):
        self.assertEqual(guard.billable(usage(1, 2, 3, 4)), 10)
        self.assertEqual(guard.billable({"input_tokens": 1, "cache_creation": {
            "ephemeral_5m_input_tokens": 5, "ephemeral_1h_input_tokens": 7}}), 13)

    def test_same_request_counted_once_last_line_wins(self):
        self.append(prompt(), asst("r1", usage(out=1)), asst("r1", usage(out=90)), asst("r2", usage()))
        self.assertEqual(self.total(), 2000)

    def test_subagent_transcripts_count_toward_parent(self):
        sub = os.path.join(self.proj, "s1", "subagents")
        os.makedirs(sub)
        self.append(asst("r1", usage()))
        self.append(asst("a1", usage()), path=os.path.join(sub, "agent-1.jsonl"))
        self.assertEqual(self.total(), 2000)

    def test_incremental_read_and_partial_line(self):
        state = {}
        self.append(asst("r1", usage()))
        self.assertEqual(guard.count_session(self.transcript, "s1", state)[0], 1000)
        self.append(asst("r2", usage()), newline=False)          # still being written
        self.assertEqual(guard.count_session(self.transcript, "s1", state)[0], 1000)
        with open(self.transcript, "a") as fh:
            fh.write("\n")
        self.assertEqual(guard.count_session(self.transcript, "s1", state)[0], 2000)

    def test_rewritten_file_is_recounted(self):
        state = {}
        self.append(asst("r1", usage()), asst("r2", usage()))
        guard.count_session(self.transcript, "s1", state)
        with open(self.transcript, "w") as fh:
            fh.write(json.dumps(asst("r3", usage(out=0, cr=0))) + "\n")
        self.assertEqual(guard.count_session(self.transcript, "s1", state)[0], 10)

    def test_matches_the_warehouse(self):
        sub = os.path.join(self.proj, "s1", "subagents")
        os.makedirs(sub)
        self.append(prompt(), asst("r1", usage(1, 2, 3, 4), tool="t1"), result("t1"),
                    asst("r1", usage(5, 6, 7, 8), ts="2026-01-01T00:00:02Z"),
                    asst("r2", usage(), ts="2026-01-01T00:00:03Z"))
        self.append(asst("a1", usage(3, 3, 3, 3)), path=os.path.join(sub, "agent-1.jsonl"))
        db = os.path.join(self.dir, "w.db")
        Loader(db_path=db, source=self.dir, other_agents=False).build(verbose=False)
        want = sqlite3.connect(db).execute(
            "SELECT billable_tokens FROM sessions WHERE id='s1'").fetchone()[0]
        self.assertEqual(self.total(), want)


class TestDecisions(Base):
    def grow_to(self, tokens):
        have = self.total()
        self.append(asst(f"r{have}-{tokens}", usage(inp=tokens - have, out=0, cr=0)))

    def test_silent_below_warnings(self):
        self.grow_to(500)
        self.assertIsNone(self.call())

    def test_warns_once_per_threshold(self):
        self.grow_to(760)
        out = self.call()
        self.assertIn("76%", out["systemMessage"])
        self.assertNotIn("permissionDecision", out["hookSpecificOutput"])
        self.assertIsNone(self.call())
        self.grow_to(810)
        self.assertIn("81%", self.call()["systemMessage"])

    def test_crossing_several_warnings_gives_one_message(self):
        self.grow_to(900)
        self.assertIn("systemMessage", self.call())
        self.assertIsNone(self.call())

    def test_asks_at_budget_then_every_step_after_approval(self):
        self.grow_to(1000)
        out = self.call(tool="t1")["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "ask")
        self.assertIn("until 125%", out["permissionDecisionReason"])
        self.append(asst("rt1", usage(0, 0, 0, 0), tool="t1"), result("t1"))
        self.assertIsNone(self.call(tool="t2"))                  # approved: quiet until 125%
        self.grow_to(1250)
        self.assertEqual(self.call(tool="t3")["hookSpecificOutput"]["permissionDecision"], "ask")

    def test_once_mode_never_asks_again(self):
        cfg = dict(CFG, after_approval="once")
        self.grow_to(1000)
        self.assertIn("rest of this session",
                      self.call(cfg, tool="t1")["hookSpecificOutput"]["permissionDecisionReason"])
        self.append(result("t1"))
        self.grow_to(5000)
        self.assertIsNone(self.call(cfg, tool="t2"))

    def test_declined_ask_is_asked_again(self):
        self.grow_to(1000)
        self.call(tool="t1")
        self.append(result("t1", rejected=True), prompt("carry on anyway"))
        self.assertEqual(self.call(tool="t2")["hookSpecificOutput"]["permissionDecision"], "ask")

    def test_unanswered_ask_is_asked_again(self):
        self.grow_to(1000)
        self.call(tool="t1")
        self.assertEqual(self.call(tool="t2")["hookSpecificOutput"]["permissionDecision"], "ask")

    def test_no_budget_is_silent(self):
        self.grow_to(10**7)
        self.assertIsNone(self.call(dict(CFG, session_tokens=None)))


class TestOverrides(unittest.TestCase):
    def test_most_specific_path_wins(self):
        cfg = dict(CFG, projects={"/a": {"session_tokens": 5}, "/a/app": {"session_tokens": 7},
                                  "/a/off": {"off": True}})
        self.assertEqual(guard.budget_for(cfg, "/a/app/src"), 7)
        self.assertEqual(guard.budget_for(cfg, "/a/app2"), 5)
        self.assertIsNone(guard.budget_for(cfg, "/a/off"))
        self.assertEqual(guard.budget_for(cfg, "/elsewhere"), 1000)

    def test_validation_drops_bad_leaves(self):
        g, bad = guard.validate_guard({"session_tokens": "lots", "warn_pct": [75, 150],
                                       "after_approval": "sometimes", "step_pct": 0,
                                       "projects": {"/a": {"session_tokens": "x"}, "/b": {"off": True}}},
                                      CFG)
        self.assertEqual(g["session_tokens"], 1000)
        self.assertEqual(g["warn_pct"], [75, 80])
        self.assertEqual(g["after_approval"], "step")
        self.assertEqual(g["step_pct"], 25)
        self.assertEqual(g["projects"], {"/b": {"off": True}})
        self.assertEqual(len(bad), 5)


class TestFailOpen(Base):
    def run_main(self, stdin):
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(stdin)), mock.patch.object(sys, "stdout", out):
            code = guard.main()
        return code, out.getvalue()

    def test_garbage_stdin(self):
        self.assertEqual(self.run_main("not json"), (0, ""))

    def test_missing_transcript(self):
        with mock.patch.object(guard, "load_guard_settings", return_value=CFG):
            self.assertEqual(self.run_main(json.dumps(
                {"session_id": "s1", "transcript_path": "/nope/x.jsonl", "cwd": "/repo"})), (0, ""))

    def test_internal_error(self):
        with mock.patch.object(guard, "run", side_effect=RuntimeError):
            self.assertEqual(self.run_main("{}"), (0, ""))

    def test_corrupt_state_file(self):
        os.makedirs(guard.STATE_DIR, exist_ok=True)
        with open(guard._state_path("s1"), "w") as fh:
            fh.write("{broken")
        self.append(asst("r1", usage(inp=800, out=0, cr=0)))
        self.assertIn("systemMessage", self.call())



class TestBudgetLine(unittest.TestCase):
    """The Budgets page line and the Sessions table's per-row budget."""

    def make(self, guard_cfg):
        path = tempfile.mktemp(suffix=".db", prefix="finops-test-")
        db = sqlite3.connect(path)
        db.executescript(SCHEMA)
        db.execute("INSERT INTO meta VALUES ('built_at','test')")
        for pid, ppath in ((1, "/work/big"), (2, "/work/quiet"), (3, "/work/other")):
            db.execute("INSERT INTO projects (id, slug, path, name) VALUES (?,?,?,?)",
                       (pid, f"p{pid}", ppath, os.path.basename(ppath)))
        a = Analytics(path)
        day = a.billing_period()["today"]
        for sid, pid, tokens, agent in (("s1", 1, 4_000_000, "claude"), ("s2", 2, 9_000_000, "claude"),
                                        ("s3", 3, 2_500_000, "claude"), ("s4", 3, 900_000, "claude"),
                                        ("s5", 3, 50_000_000, "cursor")):
            db.execute("INSERT INTO sessions (id, project_id, title, billable_tokens, agent) "
                       "VALUES (?,?,?,?,?)", (sid, pid, sid, tokens, agent))
            db.execute("INSERT INTO requests (uuid, session_id, project_id, ts, day, model, "
                       "billable_tokens, est_cost_usd, agent) VALUES (?,?,?,?,?,'m',?,1.0,?)",
                       (f"u{sid}", sid, pid, day + "T00:00:00Z", day, tokens, agent))
        db.commit(); db.close()
        a.settings["guard"] = dict(CFG, **guard_cfg)
        return a

    def line(self, a):
        return next(l for l in a.budgets({})["lines"] if l["name"] == "Per-session tokens")

    def test_not_configured_without_a_budget(self):
        self.assertFalse(self.line(self.make({"session_tokens": None}))["configured"])

    def test_counts_sessions_over_their_own_budget(self):
        a = self.make({"session_tokens": 2_000_000, "projects": {
            "/work/big": {"session_tokens": 5_000_000}, "/work/quiet": {"off": True}}})
        ln = self.line(a)
        # s1 80% of its 5M override, s2 skipped (off), s3 125% of 2M, s4 45%, s5 not Claude
        self.assertEqual(ln["sessions_over"], 1)
        self.assertEqual(ln["top_over"][0]["session_id"], "s3")
        self.assertEqual((ln["actual"], ln["budget"], ln["used_pct"]), (2_500_000, 2_000_000, 125.0))
        rows = {r["session_id"]: r["budget_tokens"] for r in a.sessions({})}
        self.assertEqual(rows, {"s1": 5_000_000, "s2": None, "s3": 2_000_000, "s4": 2_000_000,
                                "s5": None})


if __name__ == "__main__":
    unittest.main()
