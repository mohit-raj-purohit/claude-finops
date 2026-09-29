"""Overview "Act now" strip. Run: python3 -m unittest tests.test_act_now -v"""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finops.act_now import build


def live(name, ctx, sev, **kw):
    x = {"name": name, "context": ctx, "severity": sev, "pid": 1, "session_id": name,
         "project": "p", "hosts_dashboard": False, "status": "idle"}
    x.update(kw)
    return x


def skill(name, runs, kind="command", installed=False):
    return {"name": name, "runs": runs, "sessions": 3, "kind": kind, "installed": installed,
            "trigger": name.replace("-", " "), "examples": [], "what": "w", "projects": ["p"]}


class TestActNow(unittest.TestCase):
    def test_heavy_sessions_biggest_first_ok_ones_skipped(self):
        r = build([live("a", 160_000, "medium"), live("b", 90_000, "ok"),
                   live("c", 260_000, "high")], [], [], "ours")
        self.assertEqual([i["session_id"] for i in r["items"]], ["c", "a"])
        self.assertEqual(r["items"][0]["kind"], "session")
        self.assertIn("260K", r["items"][0]["title"])

    def test_only_uninstalled_command_skills_top_two(self):
        r = build([], [skill("for-the-code", 60, kind="prompt"), skill("npx-vitest", 234),
                       skill("npx-eslint", 209), skill("npm-run-lint", 27),
                       skill("npx-tsc", 500, installed=True)], [], "ours")
        self.assertEqual([i["skill"]["name"] for i in r["items"]], ["npx-vitest", "npx-eslint"])
        self.assertIn("234", r["items"][0]["title"])

    def test_memory_themes_not_saved(self):
        mem = [{"kind": "theme", "already_saved": True, "text": "Git rules", "sessions": 35, "target": "t"},
               {"kind": "theme", "already_saved": False, "text": "Answer length", "sessions": 42, "target": "t"},
               {"kind": "instruction", "text": "noise", "sessions": 99, "target": "t"}]
        r = build([], [], mem, "ours")
        self.assertEqual([i["kind"] for i in r["items"]], ["memory"])
        self.assertIn("Answer length", r["items"][0]["title"])

    def test_prompt_derived_text_marked_private_for_mask(self):
        mem = [{"kind": "theme", "already_saved": False, "text": "Answer length", "sessions": 4, "target": "t"}]
        r = build([live("Fix login bug", 260_000, "high")], [], mem, "ours")
        for it in r["items"]:
            self.assertIn(it["private"], it["title"])
        self.assertEqual([i["private"] for i in r["items"]], ["Fix login bug", "Answer length"])

    def test_statusline_offered_only_when_absent(self):
        self.assertEqual([i["kind"] for i in build([], [], [], None)["items"]], ["statusline"])
        self.assertEqual(build([], [], [], "ours")["items"], [])
        self.assertEqual(build([], [], [], "other")["items"], [])

    def test_order_sessions_then_skills_then_memory_then_statusline(self):
        r = build([live("a", 160_000, "medium")], [skill("npx-vitest", 234)],
                  [{"kind": "theme", "text": "x", "sessions": 5, "target": "t"}], None)
        self.assertEqual([i["kind"] for i in r["items"]], ["session", "skill", "memory", "statusline"])
