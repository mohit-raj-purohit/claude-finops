"""Cost engine. Pricing lives in config/pricing.json and is never mixed into usage data.

Claude Code transcripts contain token counts but no billed amount, so every figure
produced here is an ESTIMATE. Callers must label it as such.
"""
import json
import os
import re

from .paths import ROOT, PRICING_PATH

M = 1_000_000.0


FREE = {"display_name": None, "tier": "free", "input": 0.0, "output": 0.0, "cache_read": 0.0,
        "cache_write_5m": 0.0, "cache_write_1h": 0.0}
UNPRICED = dict(FREE, tier="unpriced")

# provider prefixes (bedrock/vertex regions), dated and @version suffixes, bedrock ":0"
_PREFIX = re.compile(r"^(?:[a-z]{2}(?:-[a-z]+)?\.)?anthropic\.")
_SUFFIX = re.compile(r"(?:-\d{8}|@\d{8}|-v\d+:\d+|:\d+)+$")


class Pricing:
    def __init__(self, path=PRICING_PATH):
        with open(path) as fh:
            self.raw = json.load(fh)
        self.models = self.raw.get("models", {})
        self.aliases = self.raw.get("aliases", {})
        self.default = {}          # kept for callers; unknown ids are UNPRICED now
        self.updated = self.raw.get("updated")
        self.source = self.raw.get("source")

    def normalize(self, model):
        """Canonical price-table key for any id Claude Code may record."""
        if not model:
            return "unknown"
        if model in self.models:
            return model
        m = _PREFIX.sub("", model)
        m = _SUFFIX.sub("", m)
        m = self.aliases.get(m, m)
        if m in self.models:
            return m
        # a dated key in the table for an undated id: claude-haiku-4-5 -> ...-20251001
        for k in self.models:
            if k.startswith(m + "-") and re.fullmatch(r"\d{8}", k[len(m) + 1:]):
                return k
        return m

    def rates(self, model):
        m = self.normalize(model)
        if m in self.models:
            return self.models[m]
        # An unlisted non-Claude model reached through Claude Code (e.g. a local Ollama or
        # free OpenRouter model via a claude-qwen launcher) has no Anthropic price: $0.
        if m and not m.startswith("claude") and m != "unknown":
            return FREE
        return UNPRICED

    def is_known(self, model):
        return self.normalize(model) in self.models

    def display_name(self, model):
        return self.rates(model).get("display_name") or model

    def tier(self, model):
        return self.rates(model).get("tier", "unknown")

    def context_window(self, model):
        return self.rates(model).get("context_window")

    def effective_model(self, model, context_tokens=0, speed=None):
        """The price list that actually applied, given how much context was sent.

        A request whose prompt side exceeds the model's standard context window cannot
        have been served by the standard variant — it was the long-context one, which
        is billed at a premium. The transcript records only the base model name, so
        pricing off that name alone understates every long-context request. Fast mode
        is billed at its own rate; a `[1m]` premium applies only when the table lists
        one for this model.

        Returns (model_id_to_price_with, unpriced_long_context). The flag is set when
        the context clearly exceeded the window but no `[1m]` entry exists to price it
        with, so callers can surface it rather than quietly bill it at the low rate.
        """
        m = self.normalize(model)
        if speed == "fast" and f"{m}[fast]" in self.models:
            return f"{m}[fast]", False
        r = self.models.get(m)
        win = (r or {}).get("context_window") or 0
        if not r or not win or not context_tokens or context_tokens <= win:
            return m, False
        alt = f"{m}[1m]"
        if alt in self.models:
            return alt, False
        return m, True

    def estimate(self, model, input_tokens=0, output_tokens=0, cache_read=0,
                 cache_write_5m=0, cache_write_1h=0):
        """Return estimated USD for one request."""
        r = self.rates(model)
        g = lambda k, d=0.0: float(r.get(k, self.default.get(k, d)))
        return (
            input_tokens * g("input")
            + output_tokens * g("output")
            + cache_read * g("cache_read")
            + cache_write_5m * g("cache_write_5m")
            + cache_write_1h * g("cache_write_1h")
        ) / M

    def uncached_baseline(self, model, cache_read, cache_write_5m, cache_write_1h):
        """What the cached tokens would have cost as plain input tokens.

        Used to estimate caching savings: cache reads are billed at a discount and
        writes at a premium, so savings = (reads+writes at input rate) - (actual).
        """
        r = self.rates(model)
        g = lambda k, d=0.0: float(r.get(k, self.default.get(k, d)))
        total = cache_read + cache_write_5m + cache_write_1h
        no_cache = total * g("input")
        with_cache = (cache_read * g("cache_read")
                      + cache_write_5m * g("cache_write_5m")
                      + cache_write_1h * g("cache_write_1h"))
        return no_cache / M, with_cache / M
