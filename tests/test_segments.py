"""Unit tests for cache segmentation and TTL replay, on synthetic transcripts.

Run: python3 -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from finops.segments import multipliers, replay, split_segments, ttl_cost


class FakePricing:
    """Anthropic-shaped rates: read 0.1x, 5m write 1.25x, 1h write 2x, output 5x."""

    def __init__(self, base=10.0):
        self._r = {"input": base, "output": base * 5, "cache_read": base * 0.1,
                   "cache_write_5m": base * 1.25, "cache_write_1h": base * 2.0}

    def rates(self, model):
        return self._r


class GeminiShaped(FakePricing):
    """Reads at 0.25x and writes at 1x — the shape a hardcoded 0.1/1.25/2.0 would break."""

    def __init__(self, base=10.0):
        super().__init__(base)
        self._r["cache_read"] = base * 0.25
        self._r["cache_write_5m"] = base
        self._r["cache_write_1h"] = base


def turn(ts, session="s1", model="m1", read=0, w5=0, w1h=0, inp=0, out=0,
         sidechain=0, agent_id=None, cost=0.0):
    return {"ts": ts, "session_id": session, "model": model,
            "cache_read_tokens": read, "cache_write_5m": w5, "cache_write_1h": w1h,
            "input_tokens": inp, "output_tokens": out,
            "is_sidechain": sidechain, "agent_id": agent_id, "est_cost_usd": cost}


class TestSplitSegments(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(split_segments([]), [])

    def test_single_run_is_one_segment(self):
        ts = [turn(f"2026-01-01T00:0{i}:00Z") for i in range(4)]
        self.assertEqual(len(split_segments(ts)), 1)

    def test_new_session_starts_a_segment(self):
        ts = [turn("2026-01-01T00:00:00Z", session="a"),
              turn("2026-01-01T00:01:00Z", session="b")]
        self.assertEqual(len(split_segments(ts)), 2)

    def test_model_change_starts_a_segment(self):
        ts = [turn("2026-01-01T00:00:00Z", model="opus"),
              turn("2026-01-01T00:01:00Z", model="sonnet")]
        self.assertEqual(len(split_segments(ts)), 2)

    def test_entering_and_leaving_a_subagent_each_split(self):
        ts = [turn("2026-01-01T00:00:00Z"),
              turn("2026-01-01T00:01:00Z", sidechain=1, agent_id="a1"),
              turn("2026-01-01T00:02:00Z")]
        self.assertEqual(len(split_segments(ts)), 3)

    def test_two_different_subagents_split(self):
        ts = [turn("2026-01-01T00:00:00Z", sidechain=1, agent_id="a1"),
              turn("2026-01-01T00:01:00Z", sidechain=1, agent_id="a2")]
        self.assertEqual(len(split_segments(ts)), 2)

    def test_order_is_preserved(self):
        ts = [turn("2026-01-01T00:00:00Z", model="a"),
              turn("2026-01-01T00:01:00Z", model="b"),
              turn("2026-01-01T00:02:00Z", model="b")]
        segs = split_segments(ts)
        self.assertEqual([len(s) for s in segs], [1, 2])
        self.assertEqual(segs[1][0]["ts"], "2026-01-01T00:01:00Z")


class TestMultipliers(unittest.TestCase):
    def test_derived_from_price_table(self):
        r, w5, w1 = multipliers(FakePricing(), "m1")
        self.assertAlmostEqual(r, 0.1)
        self.assertAlmostEqual(w5, 1.25)
        self.assertAlmostEqual(w1, 2.0)

    def test_non_anthropic_shape_is_not_assumed(self):
        r, w5, w1 = multipliers(GeminiShaped(), "m1")
        self.assertAlmostEqual(r, 0.25)
        self.assertAlmostEqual(w5, 1.0)
        self.assertAlmostEqual(w1, 1.0)


class TestTtlCost(unittest.TestCase):
    def test_cold_first_turn_pays_the_write_rate(self):
        # nothing was read, so the delta is all there is to charge
        seg = [turn("2026-01-01T00:00:00Z", read=0, w1h=1_000_000)]
        # 1M tokens * 2.0 * $10/M = $20
        self.assertAlmostEqual(ttl_cost(seg, 60, 2.0, FakePricing()), 20.0)

    def test_within_ttl_reads_the_prefix(self):
        seg = [turn("2026-01-01T00:00:00Z", w1h=1_000_000),
               turn("2026-01-01T00:01:00Z", read=1_000_000)]
        # turn 1: 1M * 2.0 * 10/M = 20 ; turn 2 (1 min later): 1M * 0.1 * 10/M = 1
        self.assertAlmostEqual(ttl_cost(seg, 60, 2.0, FakePricing()), 21.0)

    def test_past_ttl_rewrites_the_whole_prefix(self):
        seg = [turn("2026-01-01T00:00:00Z", w1h=1_000_000),
               turn("2026-01-01T00:10:00Z", read=1_000_000)]
        # t1 writes 1M at 1.25x = 12.5; t2 is 10 min later so under a 5m TTL the
        # 1M prefix is rewritten at 1.25x = 12.5
        self.assertAlmostEqual(ttl_cost(seg, 5, 1.25, FakePricing()), 25.0)

    def test_a_read_refreshes_the_ttl(self):
        # three turns four minutes apart: under a 5m TTL nothing should expire
        seg = [turn("2026-01-01T00:00:00Z", w5=1_000_000),
               turn("2026-01-01T00:04:00Z", read=1_000_000),
               turn("2026-01-01T00:08:00Z", read=1_000_000)]
        cost = ttl_cost(seg, 5, 1.25, FakePricing())
        # write 12.5, then two reads at 1.0 each — each read refreshes the TTL
        self.assertAlmostEqual(cost, 14.5)

    def test_input_and_output_are_charged(self):
        seg = [turn("2026-01-01T00:00:00Z", inp=1_000_000, out=1_000_000)]
        # input 1M * 10/M = 10, output 1M * 50/M = 50
        self.assertAlmostEqual(ttl_cost(seg, 60, 2.0, FakePricing()), 60.0)

    def test_long_gaps_can_make_the_short_ttl_dearer(self):
        # one big prefix, re-read after 10 minutes: the 5m TTL pays to rewrite it
        seg = [turn("2026-01-01T00:00:00Z", w1h=1_000_000),
               turn("2026-01-01T00:10:00Z", read=1_000_000)]
        p = FakePricing()
        self.assertLess(ttl_cost(seg, 60, 2.0, p), ttl_cost(seg, 5, 1.25, p))


class TestReplay(unittest.TestCase):
    def _seg(self, logged):
        return [[turn("2026-01-01T00:00:00Z", w1h=1_000_000, cost=logged)]]

    def test_reconciles_when_logged_matches(self):
        out = replay(self._seg(20.0), FakePricing())
        self.assertTrue(out["reconciled"])
        self.assertEqual(out["segments"], 1)

    def test_fails_closed_when_logged_disagrees(self):
        out = replay(self._seg(5.0), FakePricing())
        self.assertFalse(out["reconciled"])
        self.assertGreater(out["reconciliation_drift_pct"], 5)

    def test_reports_which_ttl_is_cheaper(self):
        out = replay(self._seg(20.0), FakePricing())
        # a single cold turn: 5m writes at 1.25x beat 1h at 2.0x
        self.assertEqual(out["cheaper_ttl"], "5m")
        self.assertGreater(out["difference_usd"], 0)

    def test_empty_input_does_not_divide_by_zero(self):
        out = replay([], FakePricing())
        self.assertEqual(out["segments"], 0)
        self.assertEqual(out["logged_cost_usd"], 0)


class TestWarmSegmentStart(unittest.TestCase):
    """A segment can open warm: a resumed session reads a prefix it never wrote here.

    Assuming a cold start instead was what made the real-data replay overstate cost by
    5.8% and fail its own reconciliation gate, so it is pinned by a test.
    """

    def test_first_turn_with_a_read_is_charged_as_a_read(self):
        seg = [turn("2026-01-01T00:00:00Z", read=1_000_000)]
        # 1M * 0.1 * $10/M = $1, not a $20 rewrite
        self.assertAlmostEqual(ttl_cost(seg, 60, 2.0, FakePricing()), 1.0)

    def test_expiry_charges_the_prefix_on_top_of_the_delta(self):
        seg = [turn("2026-01-01T00:00:00Z", read=1_000_000),
               turn("2026-01-01T00:30:00Z", read=1_000_000, w5=100_000)]
        # t1 warm read = 1.0
        # t2 after 30min under a 5m TTL: prefix 1M*1.25*10/M = 12.5, delta 0.1M*1.25*10/M = 1.25
        self.assertAlmostEqual(ttl_cost(seg, 5, 1.25, FakePricing()), 14.75)

if __name__ == "__main__":
    unittest.main()
