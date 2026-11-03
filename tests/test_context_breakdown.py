"""Running-session context breakdown. Run: python3 -m unittest tests.test_context_breakdown -v"""
import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finops.procs import context_breakdown


class FakePricing:
    def context_window(self, model):
        return 200_000


def write(records):
    fd, p = tempfile.mkstemp(suffix=".jsonl", prefix="finops-ctx-")
    with os.fdopen(fd, "w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return p


def user(uid, content, **kw):
    return {"type": "user", "uuid": uid, "message": {"role": "user", "content": content}, **kw}


def asst(uid, mid, content, read, write_=0, inp=0):
    return {"type": "assistant", "uuid": uid, "message": {"id": mid, "model": "claude-x", "content": content,
            "usage": {"cache_read_input_tokens": read, "cache_creation_input_tokens": write_,
                      "input_tokens": inp, "output_tokens": 5}}}


SHAPE = {"type": "api-request-shape", "shape": {"system": [{"type": "text", "text": "s" * 1000}],
                                                "tools": [{"name": "Bash"}, {"name": "Read"}]}}


class TestContextBreakdown(unittest.TestCase):
    def test_totals_cache_and_parts(self):
        tool_res = "r" * 3000
        d = context_breakdown(write([
            SHAPE,
            user("u1", "p" * 500),
            asst("a1", "m1", [{"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}], 900, 100),
            user("u2", [{"type": "tool_result", "content": tool_res}]),
            asst("a2", "m2", [{"type": "text", "text": "ok"}], 1800, 150, 50),
        ]), FakePricing())
        self.assertTrue(d["ok"])
        self.assertEqual(d["context"], 2000)
        self.assertEqual((d["window"], d["free"], d["used_pct"]), (200_000, 198_000, 1.0))
        self.assertEqual(d["cache"], {"read": 1800, "write": 150, "uncached": 50, "read_pct": 90.0})
        self.assertEqual(d["lifetime"]["read"], 2700)
        keys = {p["key"] for p in d["parts"]}
        self.assertEqual(keys, {"system", "tool_defs", "tools", "prompts", "replies"})
        self.assertAlmostEqual(sum(p["tokens"] for p in d["parts"]), 2000, delta=5)
        self.assertEqual(d["tool_count"], 2)

    def test_split_message_counted_once_for_lifetime(self):
        d = context_breakdown(write([
            user("u1", "hi"),
            asst("a1", "m1", [{"type": "thinking", "thinking": "x"}], 500),
            asst("a2", "m1", [{"type": "text", "text": "y"}], 500),
        ]), FakePricing())
        self.assertEqual(d["lifetime"]["read"], 500)

    def test_compaction_drops_old_and_keeps_preserved(self):
        d = context_breakdown(write([
            user("u1", "old" * 1000),
            asst("a1", "m1", [{"type": "text", "text": "a"}], 5000),
            user("u2", "kept" * 10),
            {"type": "system", "subtype": "compact_boundary", "uuid": "b",
             "compactMetadata": {"postTokens": 1234, "preservedSegment": {"headUuid": "u2", "tailUuid": "u2"}}},
            user("u3", "summary", isCompactSummary=True),
        ]), FakePricing())
        self.assertTrue(d["compacted_since_reply"])
        self.assertEqual(d["context"], 1234)
        self.assertIsNone(d["cache"])
        parts = {p["key"]: p for p in d["parts"]}
        self.assertEqual(set(parts), {"prompts", "summary"})   # the old prompt and reply are gone
        self.assertEqual(d["compactions"], 1)

    def test_reply_after_compaction_measures_again(self):
        d = context_breakdown(write([
            user("u1", "x"),
            asst("a1", "m1", [{"type": "text", "text": "a"}], 5000),
            {"type": "system", "subtype": "compact_boundary", "compactMetadata": {"postTokens": 900}},
            user("u2", "y"),
            asst("a2", "m2", [{"type": "text", "text": "b"}], 1000),
        ]), FakePricing())
        self.assertFalse(d["compacted_since_reply"])
        self.assertEqual(d["context"], 1000)

    def test_sidechain_and_ui_attachments_ignored(self):
        d = context_breakdown(write([
            user("u1", "x" * 100),
            {"type": "attachment", "uuid": "t1", "attachment": {"type": "queued_command", "prompt": "q" * 9000}},
            {"type": "attachment", "uuid": "t2", "attachment": {"type": "nested_memory", "content": "m" * 100}},
            {**user("s1", "z" * 9000), "isSidechain": True},
            asst("a1", "m1", [{"type": "text", "text": "a"}], 1000),
        ]), FakePricing())
        parts = {p["key"]: p["pct"] for p in d["parts"]}
        self.assertEqual(set(parts), {"prompts", "replies", "memory"})
        self.assertGreater(parts["prompts"], 30)

    def test_tools_and_skills_named(self):
        d = context_breakdown(write([
            {"type": "api-request-shape", "shape": {"system": [], "tools": [
                {"name": "Bash", "description": "d" * 400}, {"name": "Skill", "description": "s" * 100},
                {"name": "WebFetch", "description": "w" * 300}]}},
            user("u0", "<command-name>/claude-finops</command-name>"),
            user("u0m", [{"type": "text", "text": "f" * 500}], isMeta=True),
            asst("a1", "m1", [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}},
                              {"type": "tool_use", "id": "t2", "name": "Skill", "input": {"skill": "brainstorming"}}], 100),
            user("u1", [{"type": "tool_result", "tool_use_id": "t1", "content": "x" * 2000},
                        {"type": "tool_result", "tool_use_id": "t2", "content": "Launching"}]),
            user("u2", [{"type": "text", "text": "b" * 800}], isMeta=True, sourceToolUseID="t2"),
            {"type": "attachment", "uuid": "t3", "attachment": {"type": "invoked_skills",
             "skills": [{"name": "npm-run-build", "content": "n" * 300}]}},
            asst("a2", "m2", [{"type": "text", "text": "ok"}], 5000),
        ]), FakePricing())
        tools = {t["name"]: t for t in d["tools_used"]}
        self.assertEqual(list(tools)[0], "Bash")                  # biggest share first
        self.assertEqual(tools["Bash"]["calls"], 1)
        self.assertGreater(tools["Bash"]["def_tokens"], 0)
        self.assertEqual(d["tools_unused"]["tools"], 1)           # WebFetch: loaded, never called
        skills = {s["name"]: s for s in d["skills_used"]}
        self.assertEqual(set(skills), {"claude-finops", "brainstorming", "npm-run-build"})
        self.assertGreater(skills["brainstorming"]["tokens"], skills["npm-run-build"]["tokens"])
        parts = {p["key"] for p in d["parts"]}
        self.assertIn("skills", parts)
        self.assertNotIn("memory", parts)                         # skill text is not memory

    def test_no_replies(self):
        self.assertFalse(context_breakdown(write([user("u1", "x")]), FakePricing())["ok"])


if __name__ == "__main__":
    unittest.main()
