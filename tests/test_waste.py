"""Waste rules: exposed spend everywhere, excess only where it is measured.
Run: python3 -m unittest tests.test_waste -v"""
import os, sqlite3, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finops.analytics import Analytics
from finops.etl import SCHEMA


def make_db(sessions):
    """sessions: list of dicts {id, reads, writes5, writes1, cost, model,
    optional: billable, output, tool_calls}"""
    path = tempfile.mktemp(suffix=".db", prefix="finops-test-")
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.execute("INSERT INTO meta VALUES ('built_at','test')")
    db.execute("INSERT INTO projects (id, slug, path, name) VALUES (1,'p','/p','proj')")
    next_prompt_id = 1
    for s in sessions:
        w = s["writes5"] + s["writes1"]
        billable = s.get("billable", s["reads"] + w + 1000)
        output = s.get("output", 1000)
        db.execute("""INSERT INTO sessions (id, project_id, title, cache_read_tokens, cache_write_tokens,
                      billable_tokens, output_tokens, est_cost_usd, request_count, models)
                      VALUES (?,1,?,?,?,?,?,?,10,?)""",
                   (s["id"], s["id"], s["reads"], w, billable, output, s["cost"], s["model"]))
        pid = None
        if "tool_calls" in s:
            pid = next_prompt_id
            next_prompt_id += 1
            db.execute("""INSERT INTO prompts (id, uuid, session_id, project_id, text, char_len,
                          request_count, billable_tokens, output_tokens, est_cost_usd, tool_calls, norm_hash)
                          VALUES (?,?,?,1,?,?,1,?,?,?,?,?)""",
                       (pid, f"pu{s['id']}", s["id"], f"prompt for {s['id']}", 30,
                        billable, output, s["cost"], s["tool_calls"], f"hash{s['id']}"))
        db.execute("""INSERT INTO requests (uuid, session_id, project_id, prompt_id, ts, day, model, priced_as,
                      cache_read_tokens, cache_write_5m, cache_write_1h, cache_write_tokens,
                      billable_tokens, est_cost_usd, is_sidechain)
                      VALUES (?,?,1,?,'2026-01-01T00:00:00Z','2026-01-01',?,?,?,?,?,?,?,?,0)""",
                   (f"u{s['id']}", s["id"], pid, s["model"], s["model"], s["reads"], s["writes5"], s["writes1"], w,
                    billable, s["cost"]))
    db.commit(); db.close()
    return path


class TestPoorCacheReuse(unittest.TestCase):
    def test_net_positive_caching_is_not_flagged(self):
        # reads/writes = 1.0 on 5m writes: 0.9R saved vs 0.25W premium -> net positive
        a = Analytics(make_db([{"id": "s", "reads": 1_000_000, "writes5": 1_000_000, "writes1": 0,
                                "cost": 10.0, "model": "claude-sonnet-5"}]))
        kinds = [f["kind"] for f in a.waste({})["findings"]]
        self.assertNotIn("poor_cache_reuse", kinds)

    def test_net_negative_caching_is_flagged_with_breakeven_excess(self):
        # 1h writes with almost no reads: premium (4.0-2.0)*W dominates
        a = Analytics(make_db([{"id": "s", "reads": 10_000, "writes5": 0, "writes1": 1_000_000,
                                "cost": 10.0, "model": "claude-sonnet-5"}]))
        f = next(x for x in a.waste({})["findings"] if x["kind"] == "poor_cache_reuse")
        # excess = W1*(w1-in) - R*(in-read) = 1e6*(4-2)/1e6 - 1e4*(2-0.2)/1e6 = 2.0 - 0.018
        self.assertAlmostEqual(f["est_excess_usd"], 1.982, places=3)


class TestNoCounterfactualExcess(unittest.TestCase):
    def test_low_yield_and_frontier_rules_claim_no_excess(self):
        # Two sessions, both above huge_session_tokens (2,000,000 billable). Session A
        # has a normal output ratio; session B's output collapses to 1 token, putting
        # it under 0.4x the median ratio so low_yield_sessions fires, and B also carries
        # a tool-heavy prompt so tool_loops fires. Both must claim zero excess.
        a = Analytics(make_db([
            {"id": "sA", "reads": 100_000, "writes5": 1_000, "writes1": 0, "cost": 20.0,
             "model": "claude-sonnet-5", "billable": 2_100_000, "output": 1_000},
            {"id": "sB", "reads": 100_000, "writes5": 1_000, "writes1": 0, "cost": 20.0,
             "model": "claude-sonnet-5", "billable": 2_100_000, "output": 1, "tool_calls": 50},
        ]))
        findings = a.waste({})["findings"]
        present = {f["kind"]: f for f in findings
                   if f["kind"] in ("low_yield_sessions", "frontier_on_small_tasks",
                                    "long_prompts", "tool_loops")}
        self.assertTrue(present, "expected at least one of the four rules to fire")
        for kind, f in present.items():
            self.assertEqual(f["est_excess_usd"], 0.0, kind)
            self.assertIn("none claimed", f["excess_basis"], kind)


if __name__ == "__main__":
    unittest.main()
