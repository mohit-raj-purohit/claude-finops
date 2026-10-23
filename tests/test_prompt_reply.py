"""Claude's reply to a prompt, read from the transcript. Run: python3 -m unittest tests.test_prompt_reply -v"""
import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finops.etl import reply_from_transcript


def user(uuid, text, **kw):
    return {"type": "user", "uuid": uuid, "message": {"role": "user", "content": text}, **kw}


def asst(*blocks, **kw):
    return {"type": "assistant", "message": {"role": "assistant", "content": list(blocks)}, **kw}


class TestReplyFromTranscript(unittest.TestCase):
    def write(self, rows):
        fd, path = tempfile.mkstemp(suffix=".jsonl", prefix="finops-reply-")
        with os.fdopen(fd, "w") as fh:
            fh.write("\n".join(json.dumps(r) for r in rows) + "\n")
        self.addCleanup(os.remove, path)
        return path

    def test_collects_text_until_next_prompt(self):
        path = self.write([
            user("p1", "first"),
            asst({"type": "text", "text": "old reply"}),
            user("p2", "second"),
            asst({"type": "thinking", "thinking": "secret"}, {"type": "text", "text": "Looking."}),
            asst({"type": "tool_use", "id": "t1", "name": "Read", "input": {}}),
            user("r1", [{"type": "tool_result", "tool_use_id": "t1", "content": "x"}], toolUseResult={}),
            user("m1", "<system-reminder>ignore</system-reminder>"),
            asst({"type": "text", "text": "Done."}),
            asst({"type": "text", "text": "sidechain"}, isSidechain=True),
            user("p3", "third"),
            asst({"type": "text", "text": "later reply"}),
        ])
        r = reply_from_transcript(path, "p2")
        self.assertTrue(r["found"])
        self.assertEqual(r["text"], "Looking.\n\nDone.")
        self.assertEqual(r["parts"], 2)
        self.assertFalse(r["truncated"])

    def test_tools_only_and_missing_prompt(self):
        path = self.write([user("p1", "go"), asst({"type": "tool_use", "id": "t", "name": "Bash", "input": {}})])
        self.assertEqual(reply_from_transcript(path, "p1"), {"found": True, "parts": 0, "text": "", "truncated": False})
        self.assertFalse(reply_from_transcript(path, "nope")["found"])


if __name__ == "__main__":
    unittest.main()
