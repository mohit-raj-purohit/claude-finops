"""Pricing lookups: list prices, id normalization, fast mode, unknown ids.

Run: python3 -m unittest tests.test_pricing -v
"""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finops.pricing import Pricing


class TestListPrices(unittest.TestCase):
    def setUp(self):
        self.p = Pricing()

    def test_opus_5_list_price(self):
        r = self.p.rates("claude-opus-5")
        self.assertEqual((r["input"], r["output"], r["cache_read"]), (5.0, 25.0, 0.5))
        self.assertEqual(r["context_window"], 1_000_000)

    def test_sonnet_5_list_price(self):
        r = self.p.rates("claude-sonnet-5")
        self.assertEqual((r["input"], r["output"], r["cache_read"]), (2.0, 10.0, 0.2))

    def test_fable_5_1_list_price(self):
        r = self.p.rates("claude-fable-5-1")
        self.assertEqual((r["input"], r["output"], r["cache_read"]), (10.0, 50.0, 0.25))

    def test_opus_5_5_list_price(self):
        r = self.p.rates("claude-opus-5-5")
        self.assertEqual((r["input"], r["output"], r["cache_read"]), (4.0, 20.0, 0.20))
        self.assertEqual(r["context_window"], 1_000_000)
        self.assertEqual(r["tier"], "frontier")

    def test_opus_5_5_fast_list_price(self):
        r = self.p.rates("claude-opus-5-5[fast]")
        self.assertEqual((r["input"], r["output"], r["cache_read"]), (8.0, 40.0, 0.40))

    def test_no_1m_premium_entry(self):
        self.assertNotIn("claude-opus-5[1m]", self.p.models)
        self.assertEqual(self.p.effective_model("claude-opus-5", 400_000), ("claude-opus-5", False))


class TestNormalize(unittest.TestCase):
    def setUp(self):
        self.p = Pricing()

    def test_dated_suffix_stripped(self):
        self.assertEqual(self.p.normalize("claude-opus-5-20260401"), "claude-opus-5")

    def test_bedrock_prefix_stripped(self):
        self.assertEqual(self.p.normalize("us.anthropic.claude-sonnet-5-v1:0"), "claude-sonnet-5")

    def test_vertex_at_suffix_stripped(self):
        self.assertEqual(self.p.normalize("claude-opus-5@20260401"), "claude-opus-5")

    def test_haiku_alias_resolves(self):
        self.assertEqual(self.p.normalize("claude-haiku-4-5"), "claude-haiku-4-5-20251001")


class TestUnknownAndFast(unittest.TestCase):
    def setUp(self):
        self.p = Pricing()

    def test_unknown_claude_id_is_unpriced_not_sonnet(self):
        self.assertEqual(self.p.estimate("claude-future-9", input_tokens=1_000_000), 0.0)
        self.assertFalse(self.p.is_known("claude-future-9"))
        self.assertEqual(self.p.tier("claude-future-9"), "unpriced")

    def test_fast_mode_uses_fast_rates(self):
        priced_as, flag = self.p.effective_model("claude-opus-5", 10_000, speed="fast")
        self.assertEqual(priced_as, "claude-opus-5[fast]")
        self.assertEqual(self.p.rates(priced_as)["input"], 10.0)


if __name__ == "__main__":
    unittest.main()
