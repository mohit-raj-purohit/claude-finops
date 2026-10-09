"""Running-session start-time check. Run: python3 -m unittest tests.test_procs -v"""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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


if __name__ == "__main__":
    unittest.main()
