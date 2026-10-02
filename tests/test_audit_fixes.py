"""Regression tests for the 2026-10 audit fixes: true waste counts, the duplicate-prompt
window, consistent excess, zero-filled timelines, literal search, and range-independent
burn and forecast.

Run: python3 -m unittest tests.test_audit_fixes -v
"""
import json, os, sqlite3, sys, tempfile, unittest
from datetime import date
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finops import localtime
from finops.analytics import Analytics, like
from finops.etl import SCHEMA


def make_db(prompts):
    """prompts: (id, session, ts, text, cost, char_len, tool_calls)"""
    path = tempfile.mktemp(suffix=".db", prefix="finops-audit-")
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.execute("INSERT INTO meta VALUES ('built_at','test')")
    db.execute("INSERT INTO projects (id, slug, path, name) VALUES (1,'p','/p','proj')")
    for pid, sid, ts, text, cost, chars, tools in prompts:
        db.execute("INSERT OR IGNORE INTO sessions (id, project_id, title) VALUES (?,1,?)", (sid, sid))
        db.execute("""INSERT INTO prompts (id, uuid, session_id, project_id, ts, day, text, char_len,
                      request_count, est_cost_usd, tool_calls, norm_hash, billable_tokens)
                      VALUES (?,?,?,1,?,?,?,?,1,?,?,?,100)""",
                   (pid, f"p{pid}", sid, ts, ts[:10], text, chars, cost, tools, "h" + text))
        db.execute("""INSERT INTO requests (uuid, session_id, project_id, prompt_id, ts, day, model,
                      est_cost_usd, billable_tokens, context_tokens, agent)
                      VALUES (?,?,1,?,?,?,'claude-sonnet-5',?,100,100,'claude')""",
                   (f"r{pid}", sid, pid, ts, ts[:10], cost))
    db.commit(); db.close()
    return Analytics(path)


class TestWaste(unittest.TestCase):
    def test_counts_are_not_capped_at_the_evidence_shown(self):
        rows = [(i, "s", f"2026-01-01T00:{i:02d}:00Z", f"long {i} " + "x" * 30, 1.0, 7000, 0)
                for i in range(1, 21)]
        f = next(x for x in make_db(rows).waste({})["findings"] if x["kind"] == "long_prompts")
        self.assertEqual(f["count"], 20)
        self.assertEqual(len(f["evidence"]), 15)
        self.assertTrue(f["title"].startswith("20 "))
        self.assertAlmostEqual(f["est_cost_usd"], 20.0)

    def test_duplicates_only_within_the_window_and_first_ask_excluded(self):
        text = "please run the full test suite again now"
        a = make_db([(1, "s", "2026-01-01T00:00:00Z", text, 1.0, 40, 0),
                     (2, "s", "2026-01-01T00:10:00Z", text, 3.0, 40, 0),     # repeat, 10 min later
                     (3, "s", "2026-01-01T09:00:00Z", text, 5.0, 40, 0)])    # hours later: new ask
        f = next(x for x in a.waste({})["findings"] if x["kind"] == "duplicate_prompts")
        self.assertEqual(f["evidence"][0]["affected_ids"], [1, 2])
        self.assertAlmostEqual(f["est_excess_usd"], 3.0)    # the repeat, not the first ask

    def test_headline_excess_matches_the_rule_that_claims_it(self):
        text = "please run the full test suite again now"
        w = make_db([(1, "s", "2026-01-01T00:00:00Z", text, 1.0, 40, 0),
                     (2, "s", "2026-01-01T00:05:00Z", text, 2.0, 40, 0)]).waste({})
        dup = next(x for x in w["findings"] if x["kind"] == "duplicate_prompts")
        self.assertAlmostEqual(w["estimated_excess_usd"], dup["est_excess_usd"])
        self.assertIn("high_severity_pct", w)


class TestTimelineAndSearch(unittest.TestCase):
    def test_idle_days_are_zero_filled(self):
        a = make_db([(1, "s", "2026-01-01T12:00:00Z", "a b c d e f", 1.0, 20, 0),
                     (2, "s", "2026-01-04T12:00:00Z", "g h i j k l", 1.0, 20, 0)])
        rows = a.timeline({"start": "2026-01-01", "end": "2026-01-04"})
        self.assertEqual([r["bucket"] for r in rows], ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"])
        self.assertEqual(sum(r["prompts"] for r in rows), 2)

    def test_search_treats_wildcards_literally(self):
        self.assertEqual(like("a_b%"), "%a\\_b\\%%")
        a = make_db([(1, "s", "2026-01-01T12:00:00Z", "nothing special here", 1.0, 20, 0)])
        self.assertEqual(a.search("_")["prompts"], [])


class TestRanges(unittest.TestCase):
    def test_burn_ignores_the_page_date_range(self):
        a = make_db([(1, "s", "2026-01-05T12:00:00Z", "a b c d e f", 4.0, 20, 0)])
        a._today = date(2026, 1, 10)
        full, narrow = a.burn({}), a.burn({"start": "2026-01-09", "end": "2026-01-09"})
        self.assertAlmostEqual(full["projected_period_cost"], narrow["projected_period_cost"])
        self.assertAlmostEqual(full["used"]["cost_usd"], 4.0)

    def test_local_day_follows_the_configured_zone(self):
        with mock.patch.dict(os.environ, {"CLAUDE_FINOPS_TZ": "Asia/Kolkata"}):
            self.assertEqual(localtime.day("2026-01-01T20:00:00Z"), "2026-01-02")
            self.assertEqual(localtime.hour("2026-01-01T20:00:00Z"), 1)
        with mock.patch.dict(os.environ, {"CLAUDE_FINOPS_TZ": "UTC"}):
            self.assertEqual(localtime.day("2026-01-01T20:00:00Z"), "2026-01-01")


class TestLaterFixes(unittest.TestCase):
    def test_new_claude_model_priced_as_its_family_and_flagged(self):
        from finops.pricing import Pricing
        p = Pricing()
        r = p.rates("claude-sonnet-99")
        self.assertEqual(r["fallback_from"], "claude-sonnet-5-5")
        self.assertFalse(p.is_known("claude-sonnet-99"))
        self.assertEqual(p.rates("claude-unknownfamily-1").get("tier"), "unpriced")

    def test_short_steering_prompts_are_follow_ups(self):
        from finops.classify import classify
        self.assertEqual(classify("do that")[0], "follow_up")
        self.assertEqual(classify("commit and push")[0], "version_control")
        self.assertEqual(classify("z" * 400)[0], "other")

    def test_drawer_reports_the_selected_dates(self):
        a = make_db([(1, "s", "2026-01-01T12:00:00Z", "a b c d e f", 1.0, 20, 0),
                     (2, "s", "2026-01-05T12:00:00Z", "g h i j k l", 2.0, 20, 0)])
        d = a.session_detail("s", {"start": "2026-01-05", "end": "2026-01-05"})
        self.assertAlmostEqual(d["in_range"]["cost"], 2.0)
        self.assertIsNone(a.session_detail("s", {})["in_range"])

    def test_overview_counts_requests_without_a_price(self):
        a = make_db([(1, "s", "2026-01-01T12:00:00Z", "a b c d e f", 0.0, 20, 0),
                     (2, "s", "2026-01-02T12:00:00Z", "g h i j k l", 2.0, 20, 0)])
        self.assertEqual(a.overview({})["unpriced_requests"], 1)


class TestKeepDeletedHistory(unittest.TestCase):
    def write(self, src, name, sid, req):
        from tests.test_etl import user, assistant
        os.makedirs(os.path.join(src, "-repo"), exist_ok=True)
        rows = [dict(user("2026-01-01T00:00:00Z", "fix the bug in the parser"), sessionId=sid),
                dict(assistant("2026-01-01T00:00:05Z", req, "m-" + req, {"type": "text", "text": "ok"}),
                     sessionId=sid)]
        path = os.path.join(src, "-repo", name)
        with open(path, "w") as fh:
            fh.writelines(json.dumps(r) + "\n" for r in rows)
        return path

    def test_a_deleted_transcript_stays_in_the_warehouse(self):
        from finops.etl import Loader
        src = tempfile.mkdtemp(prefix="finops-src-")
        db = tempfile.mktemp(suffix=".db", prefix="finops-keep-")
        self.write(src, "a.jsonl", "sa", "req-a")
        gone = self.write(src, "b.jsonl", "sb", "req-b")
        Loader(db_path=db, source=src, other_agents=False).build(verbose=False)
        os.remove(gone)
        with mock.patch("finops.etl.keep_deleted_history", return_value=True):
            Loader(db_path=db, source=src, other_agents=False).build(verbose=False)
        con = sqlite3.connect(db)
        self.assertEqual(sorted(r[0] for r in con.execute("SELECT id FROM sessions")), ["a", "b"])
        self.assertEqual(con.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 2)
        self.assertGreater(con.execute("SELECT est_cost_usd FROM sessions WHERE id='b'").fetchone()[0], 0)
        self.assertEqual(con.execute("SELECT value FROM meta WHERE key='kept_deleted_sessions'").fetchone()[0], "1")

    def test_turned_off_a_deleted_transcript_goes(self):
        from finops.etl import Loader
        src = tempfile.mkdtemp(prefix="finops-src-")
        db = tempfile.mktemp(suffix=".db", prefix="finops-keep-")
        self.write(src, "a.jsonl", "sa", "req-a")
        gone = self.write(src, "b.jsonl", "sb", "req-b")
        Loader(db_path=db, source=src, other_agents=False).build(verbose=False)
        os.remove(gone)
        with mock.patch("finops.etl.keep_deleted_history", return_value=False):
            Loader(db_path=db, source=src, other_agents=False).build(verbose=False)
        con = sqlite3.connect(db)
        self.assertEqual([r[0] for r in con.execute("SELECT id FROM sessions")], ["a"])


if __name__ == "__main__":
    unittest.main()
