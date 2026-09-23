"""ETL correctness against small synthetic transcripts.

Run: python3 -m unittest tests.test_etl -v
"""
import json, os, sqlite3, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finops.etl import Loader


def usage(**kw):
    u = {"input_tokens": 32, "output_tokens": 508, "cache_read_input_tokens": 39427,
         "cache_creation_input_tokens": 0,
         "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0}}
    u.update(kw)
    return u


def assistant(ts, req_id, msg_id, block, **kw):
    return {"type": "assistant", "uuid": f"u-{ts}-{block.get('type')}", "timestamp": ts,
            "requestId": req_id, "sessionId": "s1", "cwd": "/repo",
            "message": {"id": msg_id, "model": "claude-opus-5", "role": "assistant",
                        "stop_reason": kw.get("stop_reason"), "usage": usage(**kw.get("usage", {})),
                        "content": [block]}}


def user(ts, text, **extra):
    r = {"type": "user", "uuid": f"u-{ts}", "timestamp": ts, "sessionId": "s1", "cwd": "/repo",
         "message": {"role": "user", "content": text}}
    r.update(extra)
    return r


def build(rows):
    src = tempfile.mkdtemp(prefix="finops-src-")
    proj = os.path.join(src, "-repo")
    os.makedirs(proj)
    with open(os.path.join(proj, "s1.jsonl"), "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    db = tempfile.mktemp(suffix=".db", prefix="finops-test-")
    Loader(db_path=db, source=src, other_agents=False).build(verbose=False)
    return sqlite3.connect(db)


class TestRequestDedup(unittest.TestCase):
    def test_three_lines_same_request_id_make_one_row(self):
        rows = [
            user("2026-01-01T00:00:00Z", "fix the bug"),
            assistant("2026-01-01T00:00:05Z", "req-1", "msg-1", {"type": "thinking", "thinking": "..."}),
            assistant("2026-01-01T00:00:06Z", "req-1", "msg-1",
                      {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/a"}}),
            assistant("2026-01-01T00:00:07Z", "req-1", "msg-1",
                      {"type": "tool_use", "id": "t2", "name": "Grep", "input": {"pattern": "x"}},
                      stop_reason="tool_use"),
        ]
        con = build(rows)
        n, tools, out, stop = con.execute(
            "SELECT COUNT(*), SUM(tool_call_count), SUM(output_tokens), MAX(stop_reason) FROM requests").fetchone()
        self.assertEqual(n, 1)
        self.assertEqual(tools, 2)          # tool_use blocks merged across lines
        self.assertEqual(out, 508)          # usage counted once
        self.assertEqual(stop, "tool_use")  # last line's stop_reason wins
        self.assertEqual(con.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0], 2)

    def test_falls_back_to_message_id_when_request_id_missing(self):
        a1 = assistant("2026-01-01T00:00:05Z", None, "msg-9", {"type": "text", "text": "hi"})
        a2 = assistant("2026-01-01T00:00:06Z", None, "msg-9", {"type": "text", "text": "there"})
        con = build([user("2026-01-01T00:00:00Z", "hello"), a1, a2])
        self.assertEqual(con.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 1)

    def test_distinct_requests_stay_distinct(self):
        con = build([user("2026-01-01T00:00:00Z", "hello"),
                     assistant("2026-01-01T00:00:05Z", "req-1", "msg-1", {"type": "text", "text": "a"}),
                     assistant("2026-01-01T00:00:09Z", "req-2", "msg-2", {"type": "text", "text": "b"})])
        self.assertEqual(con.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 2)

    def test_tool_results_between_lines_do_not_split_the_request(self):
        rows = [user("2026-01-01T00:00:00Z", "run both"),
                assistant("2026-01-01T00:00:05Z", "req-1", "msg-1",
                          {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/a"}}),
                {"type": "user", "uuid": "tr1", "timestamp": "2026-01-01T00:00:06Z", "sessionId": "s1",
                 "toolUseResult": {"ok": True},
                 "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "x"}]}},
                assistant("2026-01-01T00:00:07Z", "req-1", "msg-1",
                          {"type": "tool_use", "id": "t2", "name": "Grep", "input": {"pattern": "y"}}),
                {"type": "user", "uuid": "tr2", "timestamp": "2026-01-01T00:00:08Z", "sessionId": "s1",
                 "toolUseResult": {"ok": True},
                 "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t2", "content": "z"}]}}]
        con = build(rows)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 1)
        self.assertEqual(con.execute("SELECT tool_call_count FROM requests").fetchone()[0], 2)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0], 2)

    def test_attachments_between_lines_do_not_split_the_request(self):
        tr = lambda ts, tid: {"type": "user", "uuid": "tr" + tid, "timestamp": ts, "sessionId": "s1",
                              "toolUseResult": {"ok": True},
                              "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": "x"}]}}
        att = lambda ts: {"type": "attachment", "uuid": "att" + ts, "timestamp": ts, "sessionId": "s1", "attachment": {}}
        rows = [user("2026-01-01T00:00:00Z", "go"),
                assistant("2026-01-01T00:00:05Z", "req-1", "msg-1", {"type": "tool_use", "id": "t1", "name": "Read", "input": {}}),
                tr("2026-01-01T00:00:06Z", "t1"), att("2026-01-01T00:00:06Z"),
                assistant("2026-01-01T00:00:07Z", "req-1", "msg-1", {"type": "tool_use", "id": "t2", "name": "Read", "input": {}}),
                tr("2026-01-01T00:00:08Z", "t2"), att("2026-01-01T00:00:08Z")]
        con = build(rows)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 1)
        self.assertEqual(con.execute("SELECT tool_call_count FROM requests").fetchone()[0], 2)

    def test_tool_result_sizes_are_recorded_for_deferred_rows(self):
        tr = lambda ts, tid, body: {"type": "user", "uuid": "tr" + tid, "timestamp": ts, "sessionId": "s1",
                                    "toolUseResult": {"ok": True},
                                    "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": body}]}}
        rows = [user("2026-01-01T00:00:00Z", "go"),
                assistant("2026-01-01T00:00:05Z", "req-1", "msg-1", {"type": "tool_use", "id": "t1", "name": "Read", "input": {}}),
                tr("2026-01-01T00:00:06Z", "t1", "x" * 120),
                assistant("2026-01-01T00:00:07Z", "req-1", "msg-1", {"type": "tool_use", "id": "t2", "name": "Read", "input": {}}),
                tr("2026-01-01T00:00:08Z", "t2", "y" * 30)]
        con = build(rows)
        sizes = dict(con.execute("SELECT tool_use_id, result_chars FROM tool_calls"))
        self.assertEqual(sizes, {"t1": 120, "t2": 30})

    def test_latency_measured_to_first_line_of_group(self):
        con = build([user("2026-01-01T00:00:00Z", "hello"),
                     assistant("2026-01-01T00:00:05Z", "req-1", "msg-1", {"type": "thinking", "thinking": ""}),
                     assistant("2026-01-01T00:00:25Z", "req-1", "msg-1", {"type": "text", "text": "a"})])
        self.assertEqual(con.execute("SELECT latency_ms FROM requests").fetchone()[0], 5000.0)


class TestPromptFiltering(unittest.TestCase):
    def test_injected_tags_are_not_prompts(self):
        rows = [user("2026-01-01T00:00:00Z", "real question"),
                assistant("2026-01-01T00:00:05Z", "r1", "m1", {"type": "text", "text": "a"}),
                user("2026-01-01T00:00:10Z", "<task-notification>agent done</task-notification>"),
                user("2026-01-01T00:00:11Z", "<local-command-stdout>ok</local-command-stdout>"),
                user("2026-01-01T00:00:12Z", "<bash-input>ls</bash-input>"),
                user("2026-01-01T00:00:13Z", "<bash-stdout>a b</bash-stdout>"),
                user("2026-01-01T00:00:14Z", "<local-command-caveat>x</local-command-caveat>")]
        con = build(rows)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM prompts").fetchone()[0], 1)

    def test_norm_hash_is_stable_and_uses_full_text(self):
        pre = "You are the Spec agent. " * 30          # 720-char shared preamble
        con = build([user("2026-01-01T00:00:00Z", pre + "task A"),
                     user("2026-01-01T00:00:10Z", pre + "task B"),
                     user("2026-01-01T00:00:20Z", pre + "task A")])
        hashes = [h for (h,) in con.execute("SELECT norm_hash FROM prompts ORDER BY ts")]
        self.assertEqual(hashes[0], hashes[2])
        self.assertNotEqual(hashes[0], hashes[1])
        self.assertEqual(len(hashes[0]), 16)


if __name__ == "__main__":
    unittest.main()
