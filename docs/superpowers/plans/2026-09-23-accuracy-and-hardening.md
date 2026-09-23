# Accuracy and Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every dollar figure in claude-finops correct, remove the remaining numbers the method cannot defend, and close the server, frontend and packaging gaps found in the 2026-09-23 end-to-end review.

**Architecture:** Fix the warehouse first (pricing table, request deduplication, prompt filtering), then force a rebuild so every user's numbers self-correct on the next launch. Then strip the model-switch/trial stack and the counterfactual "excess" and "saving" figures, repair the statistical methods (forecast band, anomaly baseline, hygiene segments, scorecard), and finish with server hardening, tooltip escaping and cleanup. Every task carries its own unittest and commit.

**Tech Stack:** Python 3 standard library (sqlite3, unittest, http.server), vanilla ES modules in `web/`, JSON config in `config/`. Tests run with `python3 -m unittest discover -s tests -v`.

**Spec:** The review delivered in this session (summarised per task below). Sample warehouse for sanity checks: `~/.claude-finops/data/finops.db`.

## Global Constraints

- Standard library only. No `pip install` for runtime or tests.
- Every dollar figure must be measured or carry an explicit "estimated" basis. No number may be derived by repricing work on a model or behaviour that did not run.
- Pricing lives only in `config/pricing.json`; never hard-code a rate in Python.
- Existing tests in `tests/` must keep passing after every task.
- Commit after every task with a message that says what changed and why.

## Review Focus

Inputs the spec implies but no existing test exercises. Each line has a test pinned to the task that owns the code.

1. A transcript where one API response is split across three JSONL lines with the same `requestId` must produce one `requests` row (Task 2).
2. A transcript whose assistant lines have no `requestId` but do have `message.id` must still deduplicate on `message.id` (Task 2).
3. A user line whose text begins `<task-notification>` or `<local-command-stdout>` must not become a prompt (Task 3).
4. Two prompts sharing a 600-character preamble but differing afterwards must not be reported as duplicates (Task 3).
5. A `POST /api/settings` with `{"budgets": "notadict"}` must be rejected with 400 and leave the settings file untouched (Task 10).

---

## Phase A: Make the money correct

### Task 1: Correct the pricing table and price unknown models as unpriced

**Files:**
- Modify: `config/pricing.json`
- Modify: `finops/pricing.py`
- Create: `tests/test_pricing.py`

**Interfaces:**
- Produces: `Pricing.normalize(model: str) -> str` (canonical id), `Pricing.rates(model)` returns the `UNPRICED` dict for unknown `claude-*` ids, `Pricing.effective_model(model, context_tokens, speed=None) -> (str, bool)` where `speed == "fast"` returns the `<model>[fast]` id when present.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pricing.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_pricing -v`
Expected: FAIL on `test_opus_5_list_price` (15.0 != 5.0) and AttributeError on `normalize`.

- [ ] **Step 3: Rewrite the Claude entries in `config/pricing.json`**

Replace the `claude-*` entries and remove `claude-opus-5[1m]`. Keep the non-Claude entries as they are. Set `"updated": "2026-09-23"` and `"source": "Anthropic list prices (Claude API reference, cached 2026-06-24)"`.

```json
"models": {
  "claude-opus-5": {
    "display_name": "Claude Opus 5", "tier": "frontier",
    "input": 5.0, "output": 25.0, "cache_write_5m": 6.25, "cache_write_1h": 10.0,
    "cache_read": 0.5, "context_window": 1000000, "provider": "anthropic"
  },
  "claude-opus-5[fast]": {
    "display_name": "Claude Opus 5 (fast mode)", "tier": "frontier",
    "input": 10.0, "output": 50.0, "cache_write_5m": 12.5, "cache_write_1h": 20.0,
    "cache_read": 1.0, "context_window": 1000000, "provider": "anthropic",
    "_note": "Research-preview fast mode, 2x standard. Selected when usage.speed == 'fast'."
  },
  "claude-sonnet-5": {
    "display_name": "Claude Sonnet 5", "tier": "balanced",
    "input": 2.0, "output": 10.0, "cache_write_5m": 2.5, "cache_write_1h": 4.0,
    "cache_read": 0.2, "context_window": 1000000, "provider": "anthropic"
  },
  "claude-fable-5": {
    "display_name": "Claude Fable 5", "tier": "frontier",
    "input": 10.0, "output": 50.0, "cache_write_5m": 12.5, "cache_write_1h": 20.0,
    "cache_read": 1.0, "context_window": 1000000, "provider": "anthropic"
  },
  "claude-fable-5-1": {
    "display_name": "Claude Fable 5.1", "tier": "frontier",
    "input": 10.0, "output": 50.0, "cache_write_5m": 12.5, "cache_write_1h": 20.0,
    "cache_read": 0.25, "context_window": 1000000, "provider": "anthropic"
  },
  "claude-haiku-4-5-20251001": {
    "display_name": "Claude Haiku 4.5", "tier": "economy",
    "input": 1.0, "output": 5.0, "cache_write_5m": 1.25, "cache_write_1h": 2.0,
    "cache_read": 0.1, "context_window": 200000, "provider": "anthropic"
  },
  ...existing <synthetic>, gpt-*, gemini-*, cursor entries unchanged...
},
"aliases": {
  "claude-haiku-4-5": "claude-haiku-4-5-20251001"
}
```

Also delete the `default_model_pricing` block: unknown Claude ids are now unpriced, not silently Sonnet-priced.

- [ ] **Step 4: Implement normalization, unpriced fallback and fast mode in `finops/pricing.py`**

Replace the `FREE` constant, `rates`, `is_known`, `tier` and `effective_model` with:

```python
import re

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
        if m and not m.startswith("claude") and m != "unknown":
            return FREE
        return UNPRICED

    def is_known(self, model):
        return self.normalize(model) in self.models

    def effective_model(self, model, context_tokens=0, speed=None):
        """The price list that applied. Fast mode is billed at its own rate; a
        `[1m]` premium applies only when the table lists one for this model."""
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
```

`estimate` and `uncached_baseline` need no change except that `self.default.get(k, d)` now resolves to `d` (0.0), which is the intended unpriced behaviour.

- [ ] **Step 5: Run the new tests and the whole suite**

Run: `python3 -m unittest tests.test_pricing -v && python3 -m unittest discover -s tests -q`
Expected: all PASS. If `test_context_fit.py` fails because it relied on `claude-opus-5[1m]`, update its fixture to use a synthetic model entry rather than reinstating the premium.

- [ ] **Step 6: Commit**

```bash
git add config/pricing.json finops/pricing.py tests/test_pricing.py
git commit -m "Correct Claude list prices; normalize model ids; unknown ids are unpriced

Opus 5 was billed at 3x list, Sonnet 5 at 1.5x, Fable 5/5.1 at a third.
The [1m] premium applied to models with a flat 1M window. Fast mode is
now priced from usage.speed."
```

---

### Task 2: Deduplicate streamed assistant lines into one request per API call

**Files:**
- Modify: `finops/etl.py:307-336` (`load_file` loop) and `finops/etl.py:379-440` (`insert_request`)
- Create: `tests/test_etl.py`

**Interfaces:**
- Consumes: `Pricing.effective_model(model, context, speed)` from Task 1.
- Produces: one `requests` row per `requestId` (fallback `message.id`, fallback line `uuid`); `tool_call_count` is the union of `tool_use` blocks across the merged lines; `latency_ms` is measured from `prev_time` to the first line of the group.

- [ ] **Step 1: Write the failing test with a three-line fixture**

```python
# tests/test_etl.py
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
    Loader(db_path=db, source=src).build(verbose=False)
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest tests.test_etl -v`
Expected: `test_three_lines_same_request_id_make_one_row` FAILS with `3 != 1`.

- [ ] **Step 3: Group assistant lines before inserting**

In `load_file`, replace the `elif typ == "assistant":` branch and add a flush helper. The loop becomes:

```python
        cur_prompt = None
        prev_time = None
        group = None          # {"key", "lines": [...], "prev_time", "prompt_id"}

        def flush():
            nonlocal group
            if group:
                self.insert_request(group["lines"], session_id, pid, group["prompt_id"],
                                    group["prev_time"])
                group = None

        for r in rows:
            typ = r.get("type")
            ts = r.get("timestamp")
            t = _ts(ts)

            if typ == "user":
                flush()
                ...existing user handling unchanged...

            elif typ == "assistant":
                if agent and cur_prompt is None:
                    cur_prompt = self.parent_prompt(session_id, r.get("timestamp"))
                key = (r.get("requestId") or (r.get("message") or {}).get("id") or r.get("uuid"))
                if group and group["key"] == key:
                    group["lines"].append(r)
                else:
                    flush()
                    group = {"key": key, "lines": [r], "prev_time": prev_time,
                             "prompt_id": cur_prompt}
                prev_time = t or prev_time

            elif typ in ("attachment", "system"):
                flush()
                prev_time = t or prev_time
        flush()
```

- [ ] **Step 4: Make `insert_request` take the group of lines**

Change the signature to `def insert_request(self, lines, session_id, pid, prompt_id, prev_time):` and start the body with:

```python
        first, last = lines[0], lines[-1]
        r = last                                  # stop_reason / usage from the final line
        msg = r.get("message") or {}
        u = msg.get("usage") or {}
        model = msg.get("model") or "unknown"
        speed = u.get("speed")
        ...token extraction unchanged...
        priced_as, unpriced_long = self.pricing.effective_model(model, context, speed=speed)
        ...cost unchanged...
        ts = first.get("timestamp")               # the request started at its first line
        t = _ts(ts)
        latency = None
        if t and prev_time:
            d = (t - prev_time).total_seconds() * 1000.0
            if 0 <= d <= 900_000:
                latency = d

        tools, seen = [], set()
        for ln in lines:
            for c in ((ln.get("message") or {}).get("content") or []):
                if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("id") not in seen:
                    seen.add(c.get("id"))
                    tools.append(c)
```

In the INSERT, use `first.get("uuid")`, `first.get("requestId") or msg.get("id")` for `request_id`, and `first.get("effort")`, `first.get("isSidechain")`. Everything after `rpk = cur.lastrowid` (tool_calls insertion) is unchanged because it iterates `tools`.

Skip the group entirely when `billable == 0 and not tools` (zero-usage placeholder lines).

- [ ] **Step 5: Run the ETL tests and the suite**

Run: `python3 -m unittest tests.test_etl tests.test_segments tests.test_hygiene -v`
Expected: all PASS.

- [ ] **Step 6: Sanity check against the real warehouse**

Run:
```bash
python3 -m finops.etl 2>&1 | tail -8
sqlite3 ~/.claude-finops/data/finops.db "select count(*), count(distinct request_id) from requests where agent='claude'"
```
Expected: the two counts are equal.

- [ ] **Step 7: Commit**

```bash
git add finops/etl.py tests/test_etl.py
git commit -m "Count one request per API call, not one per streamed content block

Claude Code writes one JSONL line per block with the same requestId and
the full usage object; 45% of rows were duplicates and cost read 1.9x."
```

---

### Task 3: Stop system-injected lines becoming prompts; hash the full prompt text

**Files:**
- Modify: `finops/etl.py:312-326` (user branch), `finops/etl.py:360-377` (`insert_prompt`)
- Modify: `tests/test_etl.py`

**Interfaces:**
- Produces: `prompts.norm_hash` is `sha1(full normalised text)[:16]`; a module constant `INJECTED_PREFIXES` lists tags that are never human prompts.

- [ ] **Step 1: Add failing tests to `tests/test_etl.py`**

```python
class TestPromptFiltering(unittest.TestCase):
    def test_injected_tags_are_not_prompts(self):
        rows = [user("2026-01-01T00:00:00Z", "real question"),
                assistant("2026-01-01T00:00:05Z", "r1", "m1", {"type": "text", "text": "a"}),
                user("2026-01-01T00:00:10Z", "<task-notification>agent done</task-notification>"),
                user("2026-01-01T00:00:11Z", "<local-command-stdout>ok</local-command-stdout>"),
                user("2026-01-01T00:00:12Z", "<bash-input>ls</bash-input>"),
                user("2026-01-01T00:00:13Z", "<bash-stdout>a b</bash-stdout>"),
                user("2026-01-01T00:00:14Z", "<local-command-caveat>x</local-command-caveat>")]
        con = build(rows)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM prompts").fetchone()[0], 1)

    def test_norm_hash_is_stable_and_uses_full_text(self):
        pre = "You are the Spec agent. " * 30          # 720-char shared preamble
        con = build([user("2026-01-01T00:00:00Z", pre + "task A"),
                     user("2026-01-01T00:00:10Z", pre + "task B"),
                     user("2026-01-01T00:00:20Z", pre + "task A")])
        hashes = [h for (h,) in con.execute("SELECT norm_hash FROM prompts ORDER BY ts")]
        self.assertEqual(hashes[0], hashes[2])
        self.assertNotEqual(hashes[0], hashes[1])
        self.assertEqual(len(hashes[0]), 16)
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest tests.test_etl.TestPromptFiltering -v`
Expected: first test FAILS with `6 != 1`; second FAILS on `assertNotEqual`.

- [ ] **Step 3: Implement**

Near the other regexes in `finops/etl.py` add:

```python
import hashlib

INJECTED_PREFIXES = ("<task-notification", "<local-command-stdout", "<local-command-caveat",
                     "<bash-input", "<bash-stdout", "<bash-stderr", "<system-reminder")
```

In the user branch, after `text = _text_of(msg.get("content"))`:

```python
                if text.lstrip().startswith(INJECTED_PREFIXES):
                    prev_time = t or prev_time
                    continue
```

In `insert_prompt` replace the hash line:

```python
        norm = WS.sub(" ", text.strip().lower())
        norm_hash = hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]
```
and pass `norm_hash` instead of `str(hash(norm))`.

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest tests.test_etl -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add finops/etl.py tests/test_etl.py
git commit -m "Skip system-injected user lines; hash full prompt text with sha1

2,862 of 10,010 'prompts' were task notifications and command output.
The salted 500-char hash made templated agent prompts look duplicated."
```

---

### Task 4: Schema version and forced rebuild so existing users' numbers self-correct

**Files:**
- Modify: `finops/etl.py` (SCHEMA meta write in `build`), `finops/api.py` (startup path that opens the DB; search for `built_at`)
- Modify: `tests/test_etl.py`

**Interfaces:**
- Produces: `finops.etl.SCHEMA_VERSION = 2` and `finops.etl.needs_rebuild(db_path) -> bool`.

- [ ] **Step 1: Failing test**

```python
class TestSchemaVersion(unittest.TestCase):
    def test_fresh_build_records_version_and_needs_no_rebuild(self):
        from finops.etl import SCHEMA_VERSION, needs_rebuild
        con = build([user("2026-01-01T00:00:00Z", "hi")])
        path = con.execute("PRAGMA database_list").fetchone()[2]
        self.assertEqual(con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0],
                         str(SCHEMA_VERSION))
        self.assertFalse(needs_rebuild(path))

    def test_old_db_needs_rebuild(self):
        from finops.etl import needs_rebuild
        con = build([user("2026-01-01T00:00:00Z", "hi")])
        path = con.execute("PRAGMA database_list").fetchone()[2]
        con.execute("DELETE FROM meta WHERE key='schema_version'"); con.commit(); con.close()
        self.assertTrue(needs_rebuild(path))
```

- [ ] **Step 2: Run to verify failure** — `ImportError: SCHEMA_VERSION`.

- [ ] **Step 3: Implement**

In `finops/etl.py`:

```python
SCHEMA_VERSION = 2   # 2: one row per request, list prices corrected, injected lines skipped


def needs_rebuild(db_path):
    if not os.path.exists(db_path):
        return True
    try:
        con = sqlite3.connect(db_path)
        row = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        con.close()
    except sqlite3.Error:
        return True
    return not row or row[0] != str(SCHEMA_VERSION)
```

In `build()`, add `("schema_version", str(SCHEMA_VERSION))` to the meta tuples.

In `finops/api.py` `serve()` (where the warehouse is opened or synced on startup), before constructing `Analytics`:

```python
    from .etl import needs_rebuild, Loader, DEFAULT_SOURCE
    if needs_rebuild(DB_PATH):
        print("Warehouse schema changed (pricing and request counting were corrected); rebuilding…",
              file=sys.stderr)
        Loader(db_path=DB_PATH, source=DEFAULT_SOURCE).build()
```

- [ ] **Step 4: Run tests, then start the server once and confirm the rebuild message and new totals**

```bash
python3 -m unittest tests.test_etl -v
PORT=18799 python3 run.py & sleep 20; curl -s localhost:18799/api/overview | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('total_cost_usd') or d)"; python3 run.py --stop
```
Expected: total near $2.8K on the sample, not $17K.

- [ ] **Step 5: Commit**

```bash
git add finops/etl.py finops/api.py tests/test_etl.py
git commit -m "Version the warehouse schema and rebuild automatically when it changes"
```

---

## Phase B: Remove numbers the method cannot support

### Task 5: Delete the model-switch back-test, trial and live advice stack

**Files:**
- Delete: `finops/trial.py`, `finops/advisor.py`
- Modify: `finops/analytics.py` (remove `model_evidence`, `_cheapest`, `_RANK_SHAPE`, `TIER_RANK`, `prompt_advisor`), `finops/api.py:118-126, 316-322, 352-360`, `run.py` (`advise_now`, `--advise`), `finops/integrate.py:55-95`, `web/app.js:1470-1615, 1833-1837`
- Modify: `tests/test_hygiene.py` (no change expected; used as regression)

- [ ] **Step 1: Write a regression test that the routes are gone**

Add to a new `tests/test_api.py` (created fully in Task 10; for now create the file with only this):

```python
"""HTTP layer tests. Run: python3 -m unittest tests.test_api -v"""
import json, os, sys, threading, unittest, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finops import api as finops_api


class ServerFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = finops_api.Server(("127.0.0.1", 0), finops_api.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def get(self, path, headers=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers=headers or {})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    def post(self, path, body, headers=None):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        h = {"Content-Type": "application/json"}; h.update(headers or {})
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers=h, method="POST")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")


class TestRemovedRoutes(ServerFixture):
    def test_model_evidence_gone(self):
        self.assertEqual(self.get("/api/model_evidence")[0], 404)

    def test_trial_gone(self):
        self.assertEqual(self.get("/api/trial")[0], 404)
        self.assertEqual(self.post("/api/trial/run", {})[0], 404)
```

If `finops_api.Server` / `Handler` are named differently, use the real class names from `api.py` (grep `class .*HTTPServer` and `BaseHTTPRequestHandler`).

- [ ] **Step 2: Run to verify failure** — routes currently return 200.

- [ ] **Step 3: Remove the code**

```bash
git rm finops/trial.py finops/advisor.py
```
- `finops/api.py`: delete the `/api/trial/run` block (lines 118-126), the `trial` and `model_evidence` routes (316-322), and the `try: from .advisor import ...` block in the live sessions route (352-360); replace it with `for r in rows: r["advice"] = None` removed entirely, i.e. no `advice` key.
- `finops/analytics.py`: delete `model_evidence`, `_cheapest`, `_RANK_SHAPE`, `TIER_RANK`, `prompt_advisor` and the "model switch advisor" comment block. Run `grep -n "model_evidence\|_cheapest\|prompt_advisor" finops/` to confirm no callers remain.
- `run.py`: delete `advise_now()`, the `--advise` branch and its help line.
- `finops/integrate.py`: in `hook()`, remove the import and `advise` call so the hook prints nothing (keep the function so existing installed hooks do not error). In `statusline()`, remove the import and `advise` call; it prints `name · NN% ctx`.
- `web/app.js`: delete lines 1470-1615 (`VERDICT` through the end of `wireTrials`) and the `${x.advice ? ... : ''}` template at 1833-1837.

- [ ] **Step 4: Verify**

```bash
python3 -m unittest discover -s tests -q
node --check web/app.js
grep -rn "advisor\|trial" finops run.py web/app.js | grep -v "^web/app.js.*Advisor\b" || echo clean
```
Expected: tests PASS, `node --check` silent, grep shows only the unrelated "AI FinOps Advisor" view name.

- [ ] **Step 5: Commit**

```bash
git add -A finops run.py web/app.js tests/test_api.py
git commit -m "Remove the model-switch back-test, trial runner and live model advice

model_evidence() repriced every prompt at another model's average cost;
the trial re-ran cold prompts with no control arm. CHANGES.md already
said these numbers were gone; this makes it true."
```

---

### Task 6: Waste rules report exposed spend only, except two measured excesses

**Files:**
- Modify: `finops/analytics.py:915-1190` (`waste`), `config/settings.json` (`waste_rules`)
- Create: `tests/test_waste.py`

**Interfaces:**
- Produces: `waste()` findings keep `est_cost_usd` (exposed). `est_excess_usd` is non-zero only for `duplicate_prompts` (repeat cost within one session) and `poor_cache_reuse` (break-even formula). All other rules set `excess` to 0 and `excess_basis` to `"none claimed — flagged for review only"`. Summary gains `"excess_note"`.

- [ ] **Step 1: Failing tests**

```python
# tests/test_waste.py
"""Waste rules: exposed spend everywhere, excess only where it is measured.
Run: python3 -m unittest tests.test_waste -v"""
import os, sqlite3, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finops.analytics import Analytics
from finops.etl import SCHEMA


def make_db(sessions):
    """sessions: list of dicts {id, reads, writes5, writes1, cost, model}"""
    path = tempfile.mktemp(suffix=".db", prefix="finops-test-")
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.execute("INSERT INTO meta VALUES ('built_at','test')")
    db.execute("INSERT INTO projects (id, slug, path, name) VALUES (1,'p','/p','proj')")
    for s in sessions:
        w = s["writes5"] + s["writes1"]
        db.execute("""INSERT INTO sessions (id, project_id, title, cache_read_tokens, cache_write_tokens,
                      billable_tokens, output_tokens, est_cost_usd, request_count, models)
                      VALUES (?,1,?,?,?,?,1000,?,10,?)""",
                   (s["id"], s["id"], s["reads"], w, s["reads"] + w + 1000, s["cost"], s["model"]))
        db.execute("""INSERT INTO requests (uuid, session_id, project_id, prompt_id, ts, day, model, priced_as,
                      cache_read_tokens, cache_write_5m, cache_write_1h, cache_write_tokens,
                      billable_tokens, est_cost_usd, is_sidechain)
                      VALUES (?,?,1,1,'2026-01-01T00:00:00Z','2026-01-01',?,?,?,?,?,?,?,?,0)""",
                   (f"u{s['id']}", s["id"], s["model"], s["model"], s["reads"], s["writes5"], s["writes1"], w,
                    s["reads"] + w + 1000, s["cost"]))
    db.commit(); db.close()
    return path


class TestPoorCacheReuse(unittest.TestCase):
    def test_net_positive_caching_is_not_flagged(self):
        # reads/writes = 1.0 on 5m writes: 0.9R saved vs 0.25W premium -> net positive
        a = Analytics(make_db([{"id": "s", "reads": 1_000_000, "writes5": 1_000_000, "writes1": 0,
                                "cost": 10.0, "model": "claude-sonnet-5"}]))
        kinds = [f["kind"] for f in a.waste({})["findings"]]
        self.assertNotIn("poor_cache_reuse", kinds)

    def test_net_negative_caching_is_flagged_with_breakeven_excess(self):
        # 1h writes with almost no reads: premium (4.0-2.0)*W dominates
        a = Analytics(make_db([{"id": "s", "reads": 10_000, "writes5": 0, "writes1": 1_000_000,
                                "cost": 10.0, "model": "claude-sonnet-5"}]))
        f = next(x for x in a.waste({})["findings"] if x["kind"] == "poor_cache_reuse")
        # excess = W1*(w1-in) - R*(in-read) = 1e6*(4-2)/1e6 - 1e4*(2-0.2)/1e6 = 2.0 - 0.018
        self.assertAlmostEqual(f["est_excess_usd"], 1.982, places=3)


class TestNoCounterfactualExcess(unittest.TestCase):
    def test_low_yield_and_frontier_rules_claim_no_excess(self):
        a = Analytics(make_db([{"id": "s", "reads": 5_000_000, "writes5": 100_000, "writes1": 0,
                                "cost": 50.0, "model": "claude-opus-5"}]))
        for f in a.waste({})["findings"]:
            if f["kind"] in ("low_yield_sessions", "frontier_on_small_tasks", "long_prompts", "tool_loops"):
                self.assertEqual(f["est_excess_usd"], 0.0, f["kind"])
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest tests.test_waste -v`
Expected: `test_net_positive_caching_is_not_flagged` FAILS (rule flags reads < 3×writes).

- [ ] **Step 3: Implement**

In `waste()`:

Rule 1 (`long_prompts`): replace the excess loop with `for r in rows: r["excess"] = 0.0` and `excess_basis="none claimed — flagged for review only"`.

Rule 2 (`duplicate_prompts`): change `GROUP BY pr.norm_hash` to `GROUP BY pr.norm_hash, pr.session_id` and add `pr.session_id` to the SELECT so repeats count only within one session. Keep `excess = cost - first_cost`.

Rule 3 (`low_yield_sessions`): `r["excess"] = 0.0`; basis "none claimed — the same output at another ratio is a counterfactual". Change severity to `"medium"`.

Rule 4 (`frontier_on_small_tasks`): delete the `alt = ...` reprice; `r["excess"] = 0.0`; basis "none claimed". Raise the trigger to `pr.output_tokens < ? AND pr.tool_calls = 0` so tool-confirmation turns are not "simple tasks".

Rule 5 (`tool_loops`): `r["excess"] = 0.0`; move the hard-coded `40` to `rules.get("tool_loop_calls", 40)` and add `"tool_loop_calls": 40` to `config/settings.json` `waste_rules`.

Rule 6 (`poor_cache_reuse`): replace the SQL condition and the excess:

```python
        poor = self.q(f"""SELECT s.id session_id, s.title, proj.name project,
                          s.cache_read_tokens reads, s.cache_write_tokens writes,
                          (SELECT COALESCE(SUM(r.cache_write_5m),0) FROM requests r WHERE r.session_id=s.id) w5,
                          (SELECT COALESCE(SUM(r.cache_write_1h),0) FROM requests r WHERE r.session_id=s.id) w1,
                          s.est_cost_usd cost, s.request_count requests, s.models
                          FROM sessions s JOIN projects proj ON proj.id=s.project_id
                          WHERE {sfilter} AND s.cache_write_tokens > ?
                          ORDER BY cost DESC""", p + [rules.get("poor_cache_min_writes", 500_000)])
        flagged = []
        for r in poor:
            m = (r["models"] or "").split(",")[0]
            rt = self.pricing.rates(m)
            g = lambda k: float(rt.get(k, 0.0))
            # break-even: premium paid on writes minus discount earned on reads
            net = (r["w5"] * (g("cache_write_5m") - g("input"))
                   + r["w1"] * (g("cache_write_1h") - g("input"))
                   - r["reads"] * (g("input") - g("cache_read"))) / 1_000_000.0
            if net > 0:
                r["excess"] = net
                flagged.append(r)
        poor = flagged[:15]
```
Basis: `"cache-write premium minus the read discount actually earned, at this model's rates"`.

Add `"poor_cache_min_writes": 500000` to `waste_rules` in settings.

In the summary dict, add:
```python
"excess_note": "Estimated excess is claimed only where the baseline is measured: the cost of "
               "repeating an identical prompt in the same session, and cache writes that were "
               "never read back enough to pay for themselves. Everything else is exposed spend "
               "to review, not waste.",
```

- [ ] **Step 4: Run tests** — `python3 -m unittest tests.test_waste -v && python3 -m unittest discover -s tests -q`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add finops/analytics.py config/settings.json tests/test_waste.py
git commit -m "Waste: claim excess only where the baseline is measured

Low-yield repricing at your own median and the frontier reprice were the
same counterfactual method removed elsewhere. Poor-cache-reuse now uses
the break-even at the session's actual 5m/1h split."
```

---

### Task 7: Remove the remaining "saving" fields and stale UI columns

**Files:**
- Modify: `finops/diagnose.py:682-706` (`session_health`), `finops/diagnose.py:192`
- Modify: `finops/report.py:143-151`, `web/app.js:575-585`, `web/app.js:733-740`, and the tour strings at `web/app.js` matching `estimated saving`
- Modify: `README.md` accuracy-contract Recommendation row

- [ ] **Step 1: Failing test**

Add to `tests/test_waste.py`:

```python
class TestSessionHealthClaimsNoSaving(unittest.TestCase):
    def test_no_avoidable_cost_field(self):
        from finops.diagnose import Diagnose
        a = Analytics(make_db([{"id": "s", "reads": 300_000, "writes5": 0, "writes1": 0,
                                "cost": 5.0, "model": "claude-opus-5"}]))
        rows = Diagnose(a).session_health({})
        for r in rows:
            self.assertNotIn("avoidable_cost", r)
            self.assertIn("tokens_above_100k", r)
```

Use the real class name if `Diagnose` differs (`grep -n "^class" finops/diagnose.py`).

- [ ] **Step 2: Run** — FAILS on `assertNotIn("avoidable_cost")`.

- [ ] **Step 3: Implement**

`diagnose.py` `session_health`: delete `rate = ...`, replace
```python
            r["avoidable_tokens"] = over
            r["avoidable_cost"] = over * rate
```
with
```python
            r["tokens_above_100k"] = over     # re-read above a 100K baseline; not a saving
```
and update the `fixes.append(...)` string that mentions "$X above a 100K baseline" to "{over:,} tokens re-read above 100K". Delete the unused `cr_rate_cost` at line 192. Update `web/app.js:1977` to render `tokens_above_100k` with `fmtNum` and the label "re-read above 100K (not a saving)".

`report.py:143-151`: replace the recommendations table with:
```python
{table(["Recommendation","Spend involved","Basis"],
  [(html.escape(r['title']), _f(r.get('actual_cost_usd')), html.escape(r.get('basis','')))
   for r in rc['recommendations']]) if rc['recommendations'] else '<p>No recommendation met the evidence threshold.</p>'}
<div class="note">Observations only. No saving is estimated: what an alternative would have cost is a counterfactual.</div>
```
and add a `Context hygiene` section after section 1 using `A.hygiene(f)`: a table of `threshold, requests, cost_usd, sessions, cost_after_first_cross_usd` from `hy["above"]`.

`web/app.js:575-585`: drop the `Est. alternative` and `Est. saving` spans and the `${r.confidence} confidence` badge; keep `Actual`. `web/app.js:733-740`: delete the `r.estimated_savings_usd != null ? ... : ''` branch. Search `grep -n "estimated saving\|Est. saving\|by estimated" web/app.js` and rewrite each tour string to "ranked by spend involved".

`README.md` Recommendation badge row: "A recommendation grounded in an observed share of spend. No saving is estimated."

- [ ] **Step 4: Verify** — `python3 -m unittest discover -s tests -q && node --check web/app.js`.

- [ ] **Step 5: Commit**

```bash
git add finops/diagnose.py finops/report.py web/app.js README.md tests/test_waste.py
git commit -m "Drop the last compaction-saving figure and empty saving columns; add hygiene to the report"
```

---

## Phase C: Fix the statistics

### Task 8: Forecast, burn and anomaly baselines

**Files:**
- Modify: `finops/analytics.py:192-230` (`daily_series`, `billing_period`), `:286-340` (`burn`), `:1457-1520` (`forecast`), `:1570-1600` (`anomalies`)
- Create: `tests/test_forecast.py`

**Interfaces:**
- Produces: `Analytics.daily_series(f, days, end)` anchors `end` on `date.today()` by default; `forecast()` excludes today from the rate, bands scale by `sqrt(left)`, returns `insufficient_history: True` and no bands when fewer than 7 priced days; `anomalies()` uses a median/MAD score on zero-filled priced days; `end_of_day_cost` and `end_of_week_cost` are removed; `burn()` returns `days_until_limit=None, exceeded=True` when over the limit.

- [ ] **Step 1: Failing tests**

```python
# tests/test_forecast.py
"""Forecast and anomaly statistics. Run: python3 -m unittest tests.test_forecast -v"""
import os, sqlite3, sys, tempfile, unittest
from datetime import date, timedelta
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finops.analytics import Analytics
from finops.etl import SCHEMA

TODAY = date(2026, 9, 23)


def make_db(day_costs):
    """day_costs: {iso_day: cost}. One request per day, agent 'claude'."""
    path = tempfile.mktemp(suffix=".db", prefix="finops-test-")
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.execute("INSERT INTO meta VALUES ('built_at','test')")
    db.execute("INSERT INTO projects (id, slug, path, name) VALUES (1,'p','/p','proj')")
    db.execute("INSERT INTO sessions (id, project_id, title) VALUES ('s',1,'s')")
    for i, (d, c) in enumerate(sorted(day_costs.items())):
        db.execute("""INSERT INTO requests (uuid, session_id, project_id, prompt_id, ts, day, model, priced_as,
                      billable_tokens, est_cost_usd, is_sidechain, agent)
                      VALUES (?, 's', 1, 1, ?, ?, 'claude-opus-5', 'claude-opus-5', 1000, ?, 0, 'claude')""",
                   (f"u{i}", d + "T12:00:00Z", d, c))
    db.commit(); db.close()
    return path


def days_back(n, cost):
    return {(TODAY - timedelta(days=i)).isoformat(): cost for i in range(1, n + 1)}


class TestForecast(unittest.TestCase):
    def analytics(self, day_costs):
        a = Analytics(make_db(day_costs))
        a._today = TODAY          # test seam added in Step 3
        return a

    def test_partial_today_excluded_from_rate(self):
        dc = days_back(14, 100.0); dc[TODAY.isoformat()] = 1.0
        fc = self.analytics(dc).forecast({})
        self.assertAlmostEqual(fc["scenarios"]["expected"]["daily_rate"], 100.0)

    def test_band_scales_with_sqrt_of_days_left(self):
        dc = days_back(14, 100.0)
        for i in range(1, 15, 2):
            dc[(TODAY - timedelta(days=i)).isoformat()] = 200.0   # sd = 50
        a = self.analytics(dc); fc = a.forecast({})
        left = a.billing_period()["remaining_days"]
        exp, hi = fc["scenarios"]["expected"]["end_of_period_cost"], fc["scenarios"]["high"]["end_of_period_cost"]
        self.assertAlmostEqual(hi - exp, 50.0 * left ** 0.5, places=3)

    def test_insufficient_history_has_no_bands(self):
        fc = self.analytics(days_back(3, 100.0)).forecast({})
        self.assertTrue(fc["insufficient_history"])
        self.assertNotIn("high", fc["scenarios"])

    def test_no_end_of_day_extrapolation(self):
        fc = self.analytics(days_back(14, 100.0)).forecast({})
        self.assertNotIn("end_of_day_cost", fc)
        self.assertNotIn("end_of_week_cost", fc)


class TestAnomalies(unittest.TestCase):
    def test_zero_cost_days_do_not_make_normal_days_spikes(self):
        dc = days_back(30, 400.0)
        a = Analytics(make_db(dc)); a._today = TODAY
        # add 60 $0 Cursor days: they are unpriced and must not drag the baseline down
        con = sqlite3.connect(a.db_path)
        for i in range(31, 91):
            d = (TODAY - timedelta(days=i)).isoformat()
            con.execute("""INSERT INTO requests (uuid, session_id, project_id, prompt_id, ts, day, model, priced_as,
                           billable_tokens, est_cost_usd, is_sidechain, agent)
                           VALUES (?, 's', 1, 1, ?, ?, 'cursor', 'cursor', 0, 0, 0, 'cursor')""",
                        (f"c{i}", d + "T12:00:00Z", d))
        con.commit(); con.close()
        types = [x["type"] for x in a.anomalies({})["anomalies"]]
        self.assertNotIn("daily_spike", types)

    def test_a_real_spike_is_found_with_robust_score(self):
        dc = days_back(30, 100.0); dc[(TODAY - timedelta(days=3)).isoformat()] = 1500.0
        a = Analytics(make_db(dc)); a._today = TODAY
        spikes = [x for x in a.anomalies({})["anomalies"] if x["type"] == "daily_spike"]
        self.assertEqual(len(spikes), 1)
        self.assertEqual(spikes[0]["date"], (TODAY - timedelta(days=3)).isoformat())
```

If `Analytics` does not expose `db_path`, read it from the constructor argument you passed (keep the path in the test).

- [ ] **Step 2: Run** — expected failures on `daily_rate`, `insufficient_history` KeyError, `end_of_day_cost` present, spike tests.

- [ ] **Step 3: Implement**

Add a today seam and use it everywhere `date.today()` or `self.last_day` stood in for today:

```python
    _today = None                      # tests set this; production uses the clock

    def today(self):
        return self._today or date.today()
```

`daily_series`: change `last = _d(end or rows[-1]["day"])` to `last = _d(end) if end else max(_d(rows[-1]["day"]), self.today())`.

`billing_period`: `today = today or self.today()`.

`forecast`: replace the rate and band section with

```python
        yesterday = (self.today() - timedelta(days=1)).isoformat()
        recent = [r for r in self.daily_series(f, days=15, end=yesterday)]   # complete days only
        priced = [r["cost"] for r in recent]
        sample_days = sum(1 for c in priced if c > 0)
        mean = statistics.fmean(priced) if priced else 0.0
        sd = statistics.pstdev(priced) if len(priced) > 1 else 0.0
        in_period = [r for r in rows if bp["start"] <= r["day"] <= bp["end"]]
        used = sum(r["cost"] for r in in_period)
        used_tok = sum(r["tokens"] for r in in_period)
        left = bp["remaining_days"]
        insufficient = sample_days < 7

        def band(rate, spread=0.0):
            # spend on different days is treated as independent, so the spread of a
            # sum over `left` days grows with sqrt(left), not left
            return {"daily_rate": rate,
                    "end_of_period_cost": used + rate * left + spread * (left ** 0.5)}

        scenarios = {"expected": band(mean)}
        if not insufficient:
            scenarios["conservative"] = band(mean, -sd)
            scenarios["high"] = band(mean, sd)
            scenarios["conservative"]["end_of_period_cost"] = max(
                scenarios["conservative"]["end_of_period_cost"], used)
```

Delete the `hours`/`eod` lines and the `wk_*` lines, and remove `end_of_day_cost`, `end_of_week_cost` from `out`. Add to `out`: `"insufficient_history": insufficient, "sample_days": sample_days`, and change `method` to `"mean of the last 14 complete calendar days (idle days as zero); bands are ±1 sd × sqrt(days remaining)"`.

`anomalies` daily section: replace with

```python
        yesterday = (self.today() - timedelta(days=1)).isoformat()
        series = [d for d in self.daily_series(dict(f or {}, agents=["claude"]), end=yesterday)]
        priced = [d for d in series if d["cost"] > 0]
        if len(priced) >= 14:
            vals = [d["cost"] for d in priced]
            med = statistics.median(vals)
            mad = statistics.median(abs(v - med) for v in vals) * 1.4826 or 1e-9
            for d in priced:
                score = (d["cost"] - med) / mad
                ratio = d["cost"] / med if med else 0
                if score >= cfg.get("daily_robust_z", 3.5) and ratio >= cfg["daily_ratio"]:
                    found.append({... same dict, "baseline": med,
                                  "detail": f"${d['cost']:,.2f} vs a ${med:,.2f} median priced day (robust z={score:.1f})."})
```

Check that `where()` honours an `agents` filter key; if it does not, add `if f.get("agents"): cl.append(f"r.agent IN ({','.join('?'*len(f['agents']))})"); p.extend(f["agents"])`. Rename the session block's `"type": "session_outlier"` title to `"Among your largest sessions: {ratio:.1f}x the median"` and severity `"low"`. Add `"daily_robust_z": 3.5` to `config/settings.json` `anomaly` and update its `_comment`.

`burn`: where `days_until_limit = remaining / rate`, wrap:
```python
            if remaining <= 0:
                out["days_until_limit"], out["limit_date"], out["exceeded"] = None, None, True
```
and make the 7/14-day windows call `daily_series(f, days=n, end=yesterday)`.

- [ ] **Step 4: Run** — `python3 -m unittest tests.test_forecast -v && python3 -m unittest discover -s tests -q`. Fix `web/app.js` references to `end_of_day_cost` / `end_of_week_cost` (grep) by deleting those KPI tiles, and render `insufficient_history` as "Fewer than 7 priced days: no bands shown".

- [ ] **Step 5: Commit**

```bash
git add finops/analytics.py config/settings.json web/app.js tests/test_forecast.py
git commit -m "Forecast and anomalies: complete days only, sqrt(n) bands, robust baseline on priced days"
```

---

### Task 9: Hygiene resets on compaction; scorecard collapses to measured dimensions

**Files:**
- Modify: `finops/analytics.py:628-700` (`hygiene`), `finops/segments.py:47-70` (`split_segments`), `finops/analytics.py:1648-1745` (`scorecard`), `config/settings.json` (`scorecard`)
- Modify: `tests/test_hygiene.py`, `tests/test_segments.py`
- Create: `tests/test_scorecard.py`

**Interfaces:**
- Produces: `hygiene()` per-session dict gains `"compactions": int`; `cost_after` resets at a compaction boundary; a module function `is_compaction(prev_ctx, ctx, threshold) -> bool` in `finops/segments.py` shared by both. `scorecard()` returns dimensions `Context share`, `Cache break-even`, `Budget adherence` only, no `grade`.

- [ ] **Step 1: Failing tests**

Add to `tests/test_hygiene.py` (its `make_db(rows)` takes `(session_id, seq, ctx, cost, is_sidechain)`):

```python
    def test_cost_after_cross_resets_when_context_drops(self):
        rows = [("s", 1, 120_000, 1.0, 0), ("s", 2, 160_000, 2.0, 0),   # crosses 150K here
                ("s", 3, 30_000, 0.5, 0), ("s", 4, 40_000, 0.5, 0)]    # compaction: drop >50%
        a = self.analytics(rows)
        hy = a.hygiene({})
        self.assertAlmostEqual(hy["above"][150_000]["cost_after_first_cross_usd"], 2.0)
        s = next(x for x in hy["sessions"] if x["session_id"] == "s")
        self.assertEqual(s["compactions"], 1)
```

Add to `tests/test_segments.py`:

```python
class TestCompactionBoundary(unittest.TestCase):
    def test_context_drop_starts_a_segment(self):
        from finops.segments import split_segments
        turns = [dict(session_id="s", agent_id=None, model="m", ctx=120_000),
                 dict(session_id="s", agent_id=None, model="m", ctx=160_000),
                 dict(session_id="s", agent_id=None, model="m", ctx=30_000)]
        self.assertEqual(len(split_segments(turns)), 2)
```
Adapt the turn dict keys to whatever `split_segments` already reads (check the existing tests in the file for the field names) and add a `ctx` key.

```python
# tests/test_scorecard.py
"""Scorecard dimensions are measured and non-overlapping. Run: python3 -m unittest tests.test_scorecard -v"""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finops.analytics import Analytics
from tests.test_waste import make_db


class TestScorecard(unittest.TestCase):
    def test_dimensions(self):
        a = Analytics(make_db([{"id": "s", "reads": 1_000_000, "writes5": 100_000, "writes1": 0,
                                "cost": 10.0, "model": "claude-sonnet-5"}]))
        sc = a.scorecard({})
        self.assertEqual([d["name"] for d in sc["dimensions"]],
                         ["Context share", "Cache break-even", "Budget adherence"])
        self.assertNotIn("grade", sc)
        cbe = sc["dimensions"][1]
        # margin = (0.9*1e6*2.0/... ) : reads discount 1e6*(2.0-0.2) = 1.8 ; premium 1e5*(2.5-2.0)=0.05
        self.assertGreater(cbe["score"], 90)
```

- [ ] **Step 2: Run** — hygiene test FAILS with `3.0 != 2.0`; segments test FAILS `1 != 2`; scorecard FAILS on dimension names.

- [ ] **Step 3: Implement**

`finops/segments.py`, add at module level:

```python
def is_compaction(prev_ctx, ctx, threshold=100_000):
    """A context drop of more than half from above `threshold` is a compaction or a
    /clear. Claude Code does not record auto-compaction; this is the only trace."""
    return bool(prev_ctx) and prev_ctx >= threshold and ctx < prev_ctx * 0.5
```
In `split_segments`, alongside the existing "new session / model change / subagent" boundary conditions, add `or is_compaction(prev.get("ctx"), t.get("ctx"))` where `prev` is the previous turn of the same session.

`hygiene()`: inside the per-row loop, before the threshold checks:

```python
            prev_ctx = s["traj"][-1][0] if s["traj"] else 0
            if is_compaction(prev_ctx, ctx, thresholds[0]):
                s["compactions"] += 1
                s["first_cross"] = {t: None for t in thresholds}
```
Initialise `"compactions": 0` in the session dict, and where `cost_after` is summed, count the new crossing: the existing `if s["first_cross"][t] is not None: s["cost_after"][t] += cost` already restarts once `first_cross` is reset. Also add `slash:/compact` prompts as boundaries: query `SELECT session_id, ts FROM prompts WHERE source='slash:/compact'` up front and reset `first_cross` when a row's `ts` passes one. Update the view footnote in `web/app.js` (grep `not recorded by Claude Code`) to "Auto-compaction is not recorded; it is detected as the context dropping by more than half. Typed /compact is recorded."

`scorecard()`: replace the body with three dims:

```python
        hy = self.hygiene(f)
        thr = max(hy["above"])
        share = 100.0 * hy["above"][thr]["cost_usd"] / (hy["total_cost_usd"] or 1)
        dim("Context share", 100 - share,
            f"{share:.0f}% of spend ran above {thr//1000}K context.", 1.0)

        cs = eff["cache"]["cost_split"]
        # margin = (read discount earned − write premium paid) / total cache cost
        rt = self.pricing.rates(self._main_model(f)) if hasattr(self, "_main_model") else None
        margin = eff["cache"].get("breakeven_margin")
        if margin is None:
            dim("Cache break-even", 50, "No cache activity in range", 1.0)
        else:
            dim("Cache break-even", max(0, min(100, 50 + margin * 50)),
                f"Caching returned {margin*100:.0f}% of its cost as read discount net of write premium.", 1.0)

        ...existing Budget adherence block unchanged...
```
Compute `breakeven_margin` in `efficiency()`: `(cr*(in-read) - w5*(w5r-in) - w1*(w1r-in)) / max(cache_cost_total, 1e-9)` using per-model rates via a SQL sum grouped by model, clamped to [-1, 1]. Delete the `Token efficiency`, `Cost efficiency`, `Model selection`, `Waste control` dims, the `grade` key, and `target_output_ratio`, `target_cost_per_1k_output_usd`, `frontier_cost_share_allowance_pct` from `config/settings.json`. Remove the grade letter rendering in `web/app.js` (grep `grade`).

- [ ] **Step 4: Run** — `python3 -m unittest discover -s tests -q && node --check web/app.js`.

- [ ] **Step 5: Commit**

```bash
git add finops/analytics.py finops/segments.py config/settings.json web/app.js tests/
git commit -m "Hygiene resets at compactions; scorecard keeps only measured, non-overlapping dimensions"
```

---

## Phase D: Server, frontend and packaging hardening

### Task 10: Harden the HTTP layer

**Files:**
- Modify: `finops/api.py:110-160` (`do_POST`, `_same_origin`), `:243-263` (`do_GET`), `:290-330` (`api` int parsing)
- Modify: `web/app.js` (settings save `fetch` call: grep `/api/settings`)
- Modify: `tests/test_api.py`

- [ ] **Step 1: Failing tests** (append to `tests/test_api.py`)

```python
class TestHardening(ServerFixture):
    def test_settings_rejects_cross_origin(self):
        code, _ = self.post("/api/settings", {"budgets": {"monthly_usd": 5}},
                            headers={"Origin": "http://evil.example"})
        self.assertEqual(code, 403)

    def test_settings_rejects_wrong_type(self):
        code, body = self.post("/api/settings", {"budgets": "notadict"}, headers={"X-FinOps-Action": "1"})
        self.assertEqual(code, 400)

    def test_bad_json_is_400(self):
        code, _ = self.post("/api/settings", b"{bad", headers={"X-FinOps-Action": "1"})
        self.assertEqual(code, 400)

    def test_bad_int_param_is_400(self):
        self.assertEqual(self.get("/api/sessions?limit=abc")[0], 400)

    def test_traceback_not_leaked(self):
        code, body = self.get("/api/prompt/abc")
        self.assertEqual(code, 400)
        self.assertNotIn("Traceback", json.dumps(body))

    def test_host_header_checked(self):
        self.assertEqual(self.get("/api/overview", headers={"Host": "evil.example"})[0], 403)

    def test_nosniff_header(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/overview")
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.headers.get("X-Content-Type-Options"), "nosniff")
```

- [ ] **Step 2: Run** — all FAIL (200/500 where 403/400 expected).

- [ ] **Step 3: Implement**

In `api.py`:

```python
class BadRequest(Exception):
    pass


def _int(qs, key, default, lo=0, hi=1_000_000):
    raw = qs.get(key, [None])[0]
    if raw in (None, ""):
        return default
    try:
        v = int(raw)
    except ValueError:
        raise BadRequest(f"{key} must be an integer")
    return max(lo, min(hi, v))


SETTINGS_SHAPE = {
    "budgets": dict, "limits": dict, "alert_thresholds_pct": list, "waste_rules": dict,
    "anomaly": dict, "scorecard": dict, "account": dict, "billing_period": dict,
}
```

`send_text`/`send_json`: add `self.send_header("X-Content-Type-Options", "nosniff")` and `self.send_header("Referrer-Policy", "no-referrer")`.

Add a host check used by both GET and POST at the top of each handler:

```python
    def _host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost", "[::1]", "::1")
```
If it fails, `return self.send_json({"error": "forbidden host"}, 403)`.

`do_POST`:

```python
    def do_POST(self):
        if not self._host_ok():
            return self.send_json({"error": "forbidden host"}, 403)
        path = urlparse(self.path).path
        try:
            try:
                n = max(0, min(int(self.headers.get("Content-Length") or 0), 5_000_000))
                payload = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError
            except ValueError:
                return self.send_json({"error": "body must be a JSON object"}, 400)
            if not self._same_origin():
                return self.send_json({"ok": False, "error": "forbidden"}, 403)
            if path.startswith("/api/live/"):
                ...
            if path.startswith("/api/do/"):
                ...
            if path == "/api/settings":
                for k, v in payload.items():
                    if k not in SETTINGS_SHAPE:
                        return self.send_json({"error": f"unknown key {k}"}, 400)
                    if not isinstance(v, SETTINGS_SHAPE[k]):
                        return self.send_json({"error": f"{k} must be a {SETTINGS_SHAPE[k].__name__}"}, 400)
                bp = payload.get("billing_period") or {}
                if "anchor_day" in bp and not (isinstance(bp["anchor_day"], int) and 1 <= bp["anchor_day"] <= 28):
                    return self.send_json({"error": "anchor_day must be 1..28"}, 400)
                ...existing merge/write unchanged...
            self.send_json({"error": "unknown endpoint"}, 404)
        except BadRequest as e:
            self.send_json({"error": str(e)}, 400)
        except Exception:
            log.exception("POST %s failed", path)          # full trace to server.log only
            self.send_json({"error": "internal error; see data/server.log"}, 500)
```

`_same_origin` stays as is (all POSTs now require `X-FinOps-Action: 1`). In `web/app.js`, find the `fetch('/api/settings', {method: 'POST', ...})` call and add `'X-FinOps-Action': '1'` to its headers.

`do_GET`: add the host check, replace `if not fp.startswith(WEB)` with `if os.path.commonpath([fp, WEB]) != WEB`, and mirror the `BadRequest` / generic-exception handling. In `api()`, replace every `int(g(...))` with `_int(qs, ...)` and the `int(route.split("/")[1])` prompt/session id parse with a `try/except ValueError: raise BadRequest("id must be an integer")`. In `Analytics.where()`, wrap `int(x) for x in f["projects"]` in the same try and raise `ValueError` that `filters_from` converts to `BadRequest`.

Set up `log = logging.getLogger("finops")` with a `FileHandler` on `data/server.log` in `serve()` if not already present.

- [ ] **Step 4: Run** — `python3 -m unittest tests.test_api -v`. Expected: PASS. Manually: `curl -s -X POST -H 'Origin: http://evil.example' -d '{}' localhost:8787/api/settings` returns 403.

- [ ] **Step 5: Commit**

```bash
git add finops/api.py finops/analytics.py web/app.js tests/test_api.py
git commit -m "API: same-origin on settings, typed settings validation, 400s instead of tracebacks, host check, nosniff"
```

---

### Task 11: Escape prompt text in chart tooltips

**Files:**
- Modify: `web/charts.js:14` (`showTip`), `:144`, `:172`, `:207`
- Create: `tests/test_charts_escape.py` (runs node)

- [ ] **Step 1: Failing test**

```python
# tests/test_charts_escape.py
"""Tooltip labels must be escaped. Run: python3 -m unittest tests.test_charts_escape -v"""
import os, shutil, subprocess, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@unittest.skipUnless(shutil.which("node"), "node not installed")
class TestEsc(unittest.TestCase):
    def test_esc_exported_and_escapes(self):
        js = ("import {esc} from '" + os.path.join(ROOT, "web", "charts.js").replace("\\", "/") + "';"
              "const s = esc('<img src=x onerror=alert(1)>&\"');"
              "if (s !== '&lt;img src=x onerror=alert(1)&gt;&amp;&quot;') { console.error(s); process.exit(1) }")
        # charts.js touches document lazily, so importing it under node is safe
        r = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
```

- [ ] **Step 2: Run** — FAILS: `esc` is not exported.

- [ ] **Step 3: Implement** in `web/charts.js`

```js
export const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
```
Then wrap every interpolated label in the three `showTip(...)` template strings: `${esc(xLabel(r[xk]))}`, `${esc(s.label)}`, `${esc(label(r))}`, `${esc(label(row))}`. `sub(r)` at line 175 returns HTML built by callers; audit its callers in `app.js` (grep `sub:`) and ensure each passes its data through `esc`.

- [ ] **Step 4: Run** — `python3 -m unittest tests.test_charts_escape -v && node --check web/charts.js`.

- [ ] **Step 5: Commit**

```bash
git add web/charts.js tests/test_charts_escape.py
git commit -m "Escape transcript text in chart tooltips"
```

---

### Task 12: Launcher fixes, pagination, honest README, cleanup

**Files:**
- Modify: `run.cmd`, `run.py:94-124, 225, 359-382`, `finops/api.py:450` (pidfile), `finops/procs.py:22`
- Modify: `web/app.js:1052, 1129` (limits), `README.md:11-12, 100`, `CHANGES.md`
- Delete: `share.sh`, `claude-finops-1.0.0.tgz` (untracked), in-tree `data/*` and `config/secrets.local.json` (untracked)

- [ ] **Step 1: Failing test** (append to `tests/test_api.py`)

```python
class TestPagination(ServerFixture):
    def test_sessions_reports_total_and_honours_offset(self):
        code, body = self.get("/api/sessions?limit=1&offset=0")
        self.assertEqual(code, 200)
        self.assertIn("total", body)
        self.assertLessEqual(len(body["rows"]), 1)
```
If `sessions` currently returns a bare list, the implementation below changes it to `{"rows": [...], "total": n}`; update `web/app.js` callers (`grep -n "api/sessions" web/app.js`) to read `.rows`.

- [ ] **Step 2: Run** — FAILS (`total` missing or list returned).

- [ ] **Step 3: Implement**

`analytics.sessions(f, limit, order, offset=0)`: add `OFFSET ?` and a `COUNT(*)` query; return `{"rows": rows, "total": total}`. `api.py` sessions route: `a.sessions(f, _int(qs,"limit",200,1,2000), g("order","cost"), _int(qs,"offset",0))`. In `web/app.js` sessions and prompts views, replace the `limit=400` literal with a `S.page` state object `{limit: 200, offset: 0}`, render "Showing X–Y of total" and a "Load more" button that bumps `offset` and re-renders.

`run.cmd`:
```bat
@echo off
where py >nul 2>nul
if %errorlevel%==0 (py -3 "%~dp0run.py" %*) else (python "%~dp0run.py" %*)
```

`api.py` `serve()`: write the pidfile unconditionally (move the `PIDFILE` write out of `detach()`), remove it in a `finally`. `run.py` Windows process lookup: replace `wmic` with `powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"ProcessId=<pid>\" | Select-Object -ExpandProperty CommandLine"`. `run.py --version`: print the local version first, then attempt the update check with the existing 3 s timeout. `procs.py`: on Windows, drop `interrupt` from `ACTIONS` (leave `terminate`), because `CTRL_C_EVENT` signals the whole console group.

`README.md`: change "no network calls / makes no outbound calls" to "No outbound calls except a once-a-day version check against the npm registry (disable with `--no-update-check`), and provider APIs only if you add a key." Add the `--no-update-check` flag in `run.py` that skips the `update` thread.

`CHANGES.md`: add a dated entry at the top titled "Cost figures corrected: one row per request, list prices fixed" that explains, in the file's plain-language style, that previous totals were about 6x too high on Opus-heavy usage and that the warehouse rebuilds itself on first launch.

Cleanup:
```bash
git rm share.sh
rm -f claude-finops-1.0.0.tgz config/secrets.local.json
rm -rf data __pycache__
```
Remove `share()` and `--share` from `run.py`.

- [ ] **Step 4: Verify**

```bash
python3 -m unittest discover -s tests -q
node --check web/app.js
python3 run.py --version
npm pack --dry-run 2>&1 | tail -5
```
Expected: tests PASS, version prints immediately, pack lists 30-40 files with no tgz/db/log/secrets.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Launcher, pagination and cleanup: fix run.cmd double run, always write pidfile, page sessions, honest network claim, drop share.sh"
```

---

## Self-review

**Spec coverage.** Pricing (T1), dedup (T2), injected prompts and hash (T3), rebuild for existing users (T4), model-switch removal including advisor/hook/statusline/dead JS (T5), waste counterfactuals and poor-cache break-even (T6), compaction saving and stale saving columns and report hygiene section (T7), forecast band, partial day, today anchor, end-of-day, anomaly baseline and robust score, burn exceeded (T8), hygiene reset and segments boundary, scorecard collapse (T9), CSRF, validation, bad JSON, int params, tracebacks, host header, nosniff, commonpath (T10), tooltip XSS (T11), run.cmd, pidfile, wmic, --version network, procs CTRL_C, pagination, README claim, CHANGES, tgz/data/secrets/share cleanup (T12). Not planned, deliberately: incremental (per-file) ingestion, subagent model-mix card, MCP schema-overhead card, thinking-share metric. Those are additions rather than corrections and belong in a follow-up plan once the numbers are right.

**Type consistency.** `Pricing.effective_model(model, context_tokens, speed=None)` is used identically in T1 and T2. `insert_request(lines, ...)` takes a list in T2 only. `is_compaction(prev_ctx, ctx, threshold)` is defined in T9 and used in the same task. `ServerFixture` from T5 is reused in T10 and T12. `make_db` from `tests/test_waste.py` is imported by `tests/test_scorecard.py`.

**Review Focus coverage.** Items 1 and 2 are pinned in T2 (`test_three_lines_same_request_id_make_one_row`, `test_falls_back_to_message_id_when_request_id_missing`), 3 and 4 in T3, 5 in T10 (`test_settings_rejects_wrong_type`).
