"""Running-session start-time check. Run: python3 -m unittest tests.test_procs -v"""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json, tempfile
from unittest import mock
from finops import procs
from finops.procs import _same_start


class TestSameStart(unittest.TestCase):
    def test_single_digit_day_matches_despite_padding(self):
        # Claude Code's registry pads the day ("Oct  9"); ps output is re-joined ("Oct 9").
        self.assertTrue(_same_start({"procStart": "Fri Oct  9 16:49:11 2026"},
                                    {"lstart": "Fri Oct 9 16:49:11 2026"}))

    def test_two_digit_day_matches(self):
        self.assertTrue(_same_start({"procStart": "Sat Oct 10 08:00:00 2026"},
                                    {"lstart": "Sat Oct 10 08:00:00 2026"}))

    def test_reused_pid_rejected(self):
        self.assertFalse(_same_start({"procStart": "Fri Oct  9 16:49:11 2026"},
                                     {"lstart": "Fri Oct 9 16:49:12 2026"}))

    def test_no_start_in_registry_accepted(self):
        self.assertTrue(_same_start({}, {"lstart": "Fri Oct 9 16:49:11 2026"}))



class TestRename(unittest.TestCase):
    def test_empty_name_rejected(self):
        self.assertFalse(procs.rename(1, {"name": "  \n "})["ok"])

    def test_name_cleaned_and_typed(self):
        sent = []
        with mock.patch.object(procs, "REG", tempfile.mkdtemp()) as reg:
            with open(os.path.join(reg, "7.json"), "w") as fh:
                json.dump({"pid": 7}, fh)
            with mock.patch.object(procs, "_ps", return_value={7: {"cmd": "claude"}}), \
                 mock.patch.object(procs, "_is_claude", return_value=True), \
                 mock.patch.object(procs, "_tty_of", return_value="ttys001"), \
                 mock.patch.object(procs, "_send_tmux", lambda t, p, text: (sent.append(text), (True, "tmux"))[1]):
                r = procs.rename(7, {"name": "fix\nlogin   bug\x07"})
        self.assertTrue(r["ok"])
        self.assertEqual(sent, ["/rename fix login bug"])

    def test_herdr_sidebar_label_renamed_too(self):
        with mock.patch.object(procs, "REG", tempfile.mkdtemp()) as reg:
            with open(os.path.join(reg, "7.json"), "w") as fh:
                json.dump({"pid": 7}, fh)
            with mock.patch.object(procs, "_ps", return_value={7: {"cmd": "claude"}}), \
                 mock.patch.object(procs, "_is_claude", return_value=True), \
                 mock.patch.object(procs, "_tty_of", return_value=""), \
                 mock.patch.object(procs, "_send_tmux", return_value=(False, "")), \
                 mock.patch.object(procs, "_send_herdr", return_value=(True, "herdr")), \
                 mock.patch.object(procs, "_herdr_pane", return_value={"pane_id": "w1:p1"}), \
                 mock.patch.object(procs, "_herdr", return_value={}) as h:
                self.assertTrue(procs.rename(7, {"name": "finops"})["ok"])
        h.assert_called_once_with("agent", "rename", "w1:p1", "finops")


class TestSendHerdr(unittest.TestCase):
    def test_types_into_the_pane_running_the_session(self):
        out = {("pane", "list"): {"panes": [{"pane_id": "w1:p1"}, {"pane_id": "w2:p1"}]},
               ("pane", "process-info", "--pane", "w1:p1"): {"process_info": {"shell_pid": 10, "foreground_processes": [{"pid": 11}]}},
               ("pane", "process-info", "--pane", "w2:p1"): {"process_info": {"shell_pid": 20, "foreground_processes": [{"pid": 21}]}}}
        sent = []
        def run(cmd, **k):
            if cmd[1:3] == ["pane", "run"]:
                sent.append(cmd[3:])
                return mock.Mock(returncode=0, stderr="")
            return mock.Mock(returncode=0, stdout=json.dumps({"result": out[tuple(cmd[1:])]}))
        with mock.patch.object(procs.shutil, "which", return_value="/bin/herdr"), \
             mock.patch.object(procs, "_ps", return_value={21: {"ppid": 20}, 20: {"ppid": 1}}), \
             mock.patch.object(procs.subprocess, "run", run):
            self.assertEqual(procs._send_herdr(21, "/rename x"), (True, "herdr"))
        self.assertEqual(sent, [["w2:p1", "/rename x"]])


class TestHerdrActions(unittest.TestCase):
    def test_interrupt_presses_esc_in_the_herdr_pane(self):
        sent = []
        with mock.patch.object(procs, "REG", tempfile.mkdtemp()) as reg:
            with open(os.path.join(reg, "7.json"), "w") as fh:
                json.dump({"pid": 7}, fh)
            with mock.patch.object(procs, "_ps", return_value={7: {"cmd": "claude"}}), \
                 mock.patch.object(procs, "_is_claude", return_value=True), \
                 mock.patch.object(procs, "_herdr_pane", return_value={"pane_id": "w1:p1"}), \
                 mock.patch.object(procs.subprocess, "run", lambda cmd, **k: (sent.append(cmd), mock.Mock(returncode=0))[1]), \
                 mock.patch.object(procs.os, "kill") as kill:
                r = procs.act(7, "interrupt")
        self.assertTrue(r["ok"])
        self.assertEqual(sent, [["herdr", "pane", "send-keys", "w1:p1", "esc"]])
        kill.assert_not_called()

    def test_handover_opens_a_tab_in_the_same_workspace(self):
        with mock.patch.object(procs, "_herdr", return_value={"root_pane": {"pane_id": "w1:p2"}}) as h, \
             mock.patch.object(procs.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
            ok, err = procs._launch_terminal("/proj", "/b.md", {"pane_id": "w1:p1", "workspace_id": "w1"})
        self.assertEqual((ok, err), (True, ""))
        self.assertEqual(h.call_args[0][:4], ("tab", "create", "--workspace", "w1"))
        self.assertEqual(run.call_args[0][0][:4], ["herdr", "pane", "run", "w1:p2"])
        self.assertIn("claude", run.call_args[0][0][4])


class TestSessionPrompts(unittest.TestCase):
    def _transcript(self, prompts):
        fd, fp = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w") as fh:
            for t in prompts:
                fh.write(json.dumps({"type": "user", "message": {"content": t}}) + "\n")
        return fp

    def test_first_and_latest_without_repeating_first(self):
        d = procs.session_prompts(self._transcript(["a", "b", "c"]))
        self.assertEqual((d["first"], d["recent"], d["turns"]), ("a", ["b", "c"], 3))

    def test_long_session_keeps_last_five(self):
        d = procs.session_prompts(self._transcript([str(i) for i in range(10)]))
        self.assertEqual((d["first"], d["recent"]), ("0", ["5", "6", "7", "8", "9"]))


class TestCursorLive(unittest.TestCase):
    SID = "5aec5f49-9f24-473f-8197-25d3dd95221a"

    def _fixture(self, composer, lines):
        import sqlite3
        d = tempfile.mkdtemp()
        db = os.path.join(d, "state.vscdb")
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE cursorDiskKV (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)")
        if composer is not None:
            con.execute("INSERT INTO cursorDiskKV VALUES (?, ?)",
                        ("composerData:" + self.SID, json.dumps(composer)))
        con.commit(); con.close()
        # The slug drops spaces, so it can't be turned back into the real path.
        tdir = os.path.join(d, "projects", "Users-me-Documents-CK-Repos", "agent-transcripts", self.SID)
        os.makedirs(tdir)
        fp = os.path.join(tdir, self.SID + ".jsonl")
        with open(fp, "w") as fh:
            fh.write("\n".join(json.dumps(l) for l in lines) + "\n")
        return db, fp

    def _live(self, composer, lines):
        db, fp = self._fixture(composer, lines)
        with mock.patch.object(procs, "_cursor_db", return_value=db):
            return procs._cursor_live(fp, None)

    COMPOSER = {"name": "Production environment switch", "status": "completed",
                "modelConfig": {"modelName": "grok-4.7"}, "contextTokensUsed": 52245,
                "workspaceIdentifier": {"uri": {"fsPath": "/Users/me/Documents/CK Repos"}}}
    TURN = [{"role": "user", "message": {"content": [{"type": "text", "text": "hi"}]}},
            {"role": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read"}]}}]

    def test_reads_name_model_context_and_real_path_from_ide_store(self):
        st = self._live(self.COMPOSER, self.TURN + [{"type": "turn_ended", "status": "success"}])
        self.assertEqual(st["name"], "Production environment switch")
        self.assertEqual(st["model"], "grok-4.7")
        self.assertEqual(st["context"], 52245)
        self.assertEqual(st["cwd"], "/Users/me/Documents/CK Repos")
        self.assertEqual(st["project_hint"], "CK Repos")
        self.assertEqual(st["steps"], 1)

    def test_finished_turn_is_idle_even_if_just_written(self):
        st = self._live(self.COMPOSER, self.TURN + [{"type": "turn_ended", "status": "success"}])
        self.assertTrue(st["turn_ended"])
        st = self._live(self.COMPOSER, self.TURN)
        self.assertFalse(st["turn_ended"])

    def test_generating_chat_is_busy(self):
        st = self._live(dict(self.COMPOSER, status="generating"), self.TURN)
        self.assertTrue(st["generating"])

    def test_missing_ide_store_falls_back_to_transcript(self):
        db, fp = self._fixture(None, self.TURN)
        with mock.patch.object(procs, "_cursor_db", return_value=db + ".missing"):
            st = procs._cursor_live(fp, None)
        self.assertEqual((st["model"], st["context"], st["steps"]), (None, None, 1))

    def test_status_uses_turn_end_not_only_file_age(self):
        db, fp = self._fixture(self.COMPOSER, self.TURN + [{"type": "turn_ended", "status": "success"}])
        with mock.patch.object(procs, "_cursor_db", return_value=db), \
             mock.patch.dict(procs.LIVE_SOURCES, {"cursor": (fp, procs._cursor_live)}, clear=True):
            rows = procs.list_agent_sessions(procs._NoPricing(), ["cursor"], procs={})
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["status"], rows[0]["name"], rows[0]["project"]),
                         ("idle", "Production environment switch", "CK Repos"))


if __name__ == "__main__":
    unittest.main()
