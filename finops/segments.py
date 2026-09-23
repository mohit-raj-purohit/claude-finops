"""Cache segments and cache-TTL replay.

A prompt cache belongs to one model and one continuous run of turns. The moment the
run breaks — a new session, a subagent, a model change — the next turn pays to write
the whole prefix again. Those breaks are the only places a model switch is free, so
they are the unit this module produces.

Everything here is arithmetic over token counts and timestamps that the transcripts
already carry. No behavioural assumption is made about what a different model would
have done, which is what separates these numbers from the model-switch estimates.

Boundaries this can see:
  * session start
  * subagent (sidechain) start
  * a model change between consecutive turns
  * a compaction: context dropping by more than half (auto-compaction is not
    recorded directly, but this is its trace; a typed /compact is recorded too)
"""
from datetime import datetime

# Multipliers are derived per model from the price table rather than hardcoded: the
# 0.1x read / 1.25x 5m / 2.0x 1h shape holds for Anthropic, but Gemini reads at 0.25x
# and OpenAI writes at 1.0x, so a fixed constant would quietly corrupt those agents.
READ = "cache_read"
W5M = "cache_write_5m"
W1H = "cache_write_1h"


def parse_ts(ts):
    try:
        return datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def multipliers(pricing, model):
    """(read, write_5m, write_1h) as multiples of the model's base input price."""
    r = pricing.rates(model) or {}
    base = float(r.get("input") or 0)
    if not base:
        return 0.0, 0.0, 0.0
    g = lambda k: float(r.get(k) or 0) / base
    return g(READ), g(W5M), g(W1H)


def is_compaction(prev_ctx, ctx, threshold=100_000):
    """A context drop of more than half from above `threshold` is a compaction or a
    /clear. Claude Code does not record auto-compaction; this is the only trace."""
    return bool(prev_ctx) and prev_ctx >= threshold and ctx < prev_ctx * 0.5


def split_segments(turns):
    """Split one session's turns into cache segments.

    `turns` must be ordered by timestamp and carry: session_id, model, is_sidechain,
    agent_id. Returns a list of lists, preserving order.
    """
    segments, current, prev = [], [], None
    for t in turns:
        boundary = (
            prev is None
            or t.get("session_id") != prev.get("session_id")
            or t.get("model") != prev.get("model")
            # a sidechain turn runs against its own prefix; entering or leaving one,
            # or moving between two different subagents, rebuilds the cache
            or (t.get("agent_id") or None) != (prev.get("agent_id") or None)
            or bool(t.get("is_sidechain")) != bool(prev.get("is_sidechain"))
            or is_compaction(prev.get("ctx") if prev else None, t.get("ctx"))
        )
        if boundary and current:
            segments.append(current)
            current = []
        current.append(t)
        prev = t
    if current:
        segments.append(current)
    return segments


def ttl_cost(segment, ttl_minutes, write_mult, pricing):
    """Replay one segment's cost under a given cache TTL.

    Within the TTL the prefix is still resident: the turn pays the read rate for it and
    the write rate only for the delta it adds. Past the TTL the prefix is gone and the
    whole thing is written again. A turn refreshes the TTL whether it read or wrote.
    """
    total = 0.0
    last_cache_time = None
    for t in segment:
        # Price with the list that actually applied (a long-context variant bills at a
        # premium), while segmentation keys off the plain model name, since that is what
        # identifies the cache.
        model = t.get("priced_as") or t.get("model")
        rates = pricing.rates(model) or {}
        base = float(rates.get("input") or 0) / 1e6
        out_rate = float(rates.get("output") or 0) / 1e6
        read_m, _, _ = multipliers(pricing, model)

        prefix = t.get("cache_read_tokens") or 0
        delta = (t.get("cache_write_5m") or 0) + (t.get("cache_write_1h") or 0)
        when = parse_ts(t.get("ts"))
        gap = None
        if last_cache_time and when:
            gap = (when - last_cache_time).total_seconds() / 60.0

        if gap is not None and gap > ttl_minutes:
            # The prefix expired between turns, so it has to be written again before it
            # can be read. Charged on top of the delta this turn added.
            total += prefix * write_mult * base
            total += delta * write_mult * base
        else:
            # Either still inside the TTL, or the first turn we can see — where the
            # tokens themselves say what happened, so they are charged as recorded
            # rather than assumed cold. A segment often opens warm (a resumed session
            # reads a prefix it did not pay to write inside this segment).
            total += prefix * read_m * base
            total += delta * write_mult * base

        total += (t.get("input_tokens") or 0) * base
        total += (t.get("output_tokens") or 0) * out_rate
        if when:
            last_cache_time = when
    return total


def replay(segments, pricing, tolerance=0.05):
    """Compare the actual 1h TTL against a 5m TTL across every segment.

    Reconciles the 1h replay against the cost actually logged first. A replay that
    cannot reproduce the real bill is not evidence about a counterfactual one, so the
    caller is handed `reconciled=False` and should show nothing.
    """
    actual = at_1h = at_5m = 0.0
    for seg in segments:
        actual += sum((t.get("est_cost_usd") or 0) for t in seg)
        at_1h += ttl_cost(seg, 60, 2.0, pricing)
        at_5m += ttl_cost(seg, 5, 1.25, pricing)
    drift = abs(at_1h - actual) / actual if actual else 0.0
    return {
        "segments": len(segments),
        "logged_cost_usd": actual,
        "replay_1h_usd": at_1h,
        "replay_5m_usd": at_5m,
        "difference_usd": at_1h - at_5m,   # positive => 5m is cheaper
        "cheaper_ttl": "5m" if at_5m < at_1h else "1h",
        "reconciliation_drift_pct": round(100 * drift, 2),
        "reconciled": drift <= tolerance,
        "basis": "arithmetic",
    }
