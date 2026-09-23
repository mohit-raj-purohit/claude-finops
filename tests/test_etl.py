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

    def test_latency_measured_to_first_line_of_group(self):
        con = build([user("2026-01-01T00:00:00Z", "hello"),
                     assistant("2026-01-01T00:00:05Z", "req-1", "msg-1", {"type": "thinking", "thinking": ""}),
                     assistant("2026-01-01T00:00:25Z", "req-1", "msg-1", {"type": "text", "text": "a"})])
        self.assertEqual(con.execute("SELECT latency_ms FROM requests").fetchone()[0], 5000.0)


if __name__ == "__main__":
    unittest.main()
