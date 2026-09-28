# Claude Parity Features Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a peak-hours heatmap, a vs-yesterday figure, resume-from-history, Claude desktop Cowork transcripts, plan-limit history and older Claude prices to claude-finops.

**Architecture:** Backend additions live where their siblings already are (`Analytics` methods, `Loader.build`, a new small `finops/plan_history.py`), exposed through `finops/api.py` GET routes and rendered by the existing vanilla-JS views in `web/app.js` with one new SVG chart in `web/charts.js`.

**Tech Stack:** Python 3 standard library (sqlite3, unittest), vanilla ES modules + SVG, JSON config.

**Spec:** `docs/superpowers/specs/2026-09-28-claude-parity-design.md`

## Global Constraints

- Python standard library only; no new dependencies (README promise).
- Nothing leaves the machine: new sources are local files only.
- Every figure carries its badge: cost = Estimated, tokens/plan % = Actual.
- Prices only from Anthropic's published pricing page (fetched 2026-09-28): https://platform.claude.com/docs/en/about-claude/pricing
- Tests: `python3 -m unittest discover tests` must stay green (107 tests before this work).
- Work on branch `feat/claude-parity`, not `main`.

## Review Focus

1. Warehouse built on a machine **without** the Claude desktop app → build and every page still work (Task 4 test: missing root).
2. `plan-usage-history.json` with samples missing `fh`/`sd` or non-numeric values → those points are gaps, not a crash (Task 5 test).
3. Heatmap with a filter that matches nothing → 168 zero cells, chart shows an empty state (Task 1 test).
4. Yesterday cost 0 while today > 0 → no divide-by-zero, arrow omitted (Task 2 test).
5. Test suite run on a developer machine that *has* Cowork data must not pick it up in fixtures built from temp dirs (Task 4 test: explicit source ⇒ no desktop roots).

---

### Task 0: Branch

- [ ] **Step 1:** `cd "/Users/mohitrajpurohit/Documents/CK Repos/claude-finops" && git checkout -b feat/claude-parity`

---

### Task 1: Peak-hours heatmap

**Files:**
- Modify: `finops/analytics.py` (new method after `timeline`, ~line 446)
- Modify: `finops/api.py` (route after `f = filters_from(qs)` block, near `timeline`)
- Modify: `web/charts.js` (new export `heatmap`)
- Modify: `web/app.js` (`VIEWS.usage`, ~line 764)
- Test: `tests/test_parity.py` (new)

**Interfaces:**
- Produces: `Analytics.heatmap(f) -> {"cells": [{"dow": int 0-6 (0=Mon), "hour": int 0-23, "cost": float, "tokens": int, "requests": int}] (168 items, row-major dow then hour), "tz": str, "cost_basis": "estimated"}`; `GET /api/heatmap`; `C.heatmap(host, {cells, metric, fmt})`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_parity.py`:

```python
"""Claude parity features: heatmap, vs-yesterday, resume, plan history.

Run: python3 -m unittest tests.test_parity -v
"""
import json, os, sqlite3, sys, tempfile, time, unittest
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finops.analytics import Analytics
from finops.etl import SCHEMA


def make_db(requests, sessions=(("A", "/x/.claude/projects/-p/A.jsonl"),)):
    """requests: iterable of (ts, day, cost, tokens, session_id)."""
    path = tempfile.mktemp(suffix=".db", prefix="finops-parity-")
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.execute("INSERT INTO meta VALUES ('built_at','test')")
    db.execute("INSERT INTO projects (id, slug, path, name) VALUES (1,'p','/p','proj')")
    for sid, src in sessions:
        db.execute("INSERT INTO sessions (id, project_id, source_file) VALUES (?,1,?)", (sid, src))
    for i, (ts, day, cost, tokens, sid) in enumerate(requests):
        db.execute("""INSERT INTO requests (uuid, session_id, project_id, ts, day, model,
                        est_cost_usd, billable_tokens) VALUES (?,?,1,?,?,'claude-opus-5',?,?)""",
                   (f"u{i}", sid, ts, day, cost, tokens))
    db.commit(); db.close()
    return path


class LocalTZ:
    def __init__(self, tz): self.tz = tz
    def __enter__(self):
        self.old = os.environ.get("TZ"); os.environ["TZ"] = self.tz; time.tzset()
    def __exit__(self, *a):
        if self.old is None: os.environ.pop("TZ", None)
        else: os.environ["TZ"] = self.old
        time.tzset()


class TestHeatmap(unittest.TestCase):
    def test_168_cells_monday_first_local_time(self):
        # 2026-01-04 is a Sunday; 23:00 UTC is Monday 04:30 in Asia/Kolkata
        a = Analytics(make_db([("2026-01-04T23:00:00Z", "2026-01-04", 2.0, 100, "A")]))
        with LocalTZ("Asia/Kolkata"):
            h = a.heatmap({})
        self.assertEqual(len(h["cells"]), 168)
        hot = [c for c in h["cells"] if c["requests"]]
        self.assertEqual(hot, [{"dow": 0, "hour": 4, "cost": 2.0, "tokens": 100, "requests": 1}])
        self.assertEqual(h["cost_basis"], "estimated")

    def test_utc_zone_keeps_utc_hour(self):
        a = Analytics(make_db([("2026-01-04T23:00:00Z", "2026-01-04", 1.0, 5, "A")]))
        with LocalTZ("UTC"):
            hot = [c for c in a.heatmap({})["cells"] if c["requests"]]
        self.assertEqual((hot[0]["dow"], hot[0]["hour"]), (6, 23))   # Sunday 23:00

    def test_filter_matching_nothing_gives_zero_grid(self):
        a = Analytics(make_db([("2026-01-04T23:00:00Z", "2026-01-04", 1.0, 5, "A")]))
        h = a.heatmap({"start": "2030-01-01"})
        self.assertEqual(len(h["cells"]), 168)
        self.assertEqual(sum(c["requests"] for c in h["cells"]), 0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest tests.test_parity -v`
Expected: FAIL — `AttributeError: 'Analytics' object has no attribute 'heatmap'`

- [ ] **Step 3: Implement** — in `finops/analytics.py`, add `import time` to the imports if absent, and after `timeline()`:

```python
    def heatmap(self, f=None):
        """Spend by weekday x hour, in this machine's local time (Monday first).

        Transcripts stamp UTC; bucketing on that would put a 10am IST session at
        4am. SQLite's 'localtime' modifier applies the local offset, half-hour
        zones included. Date filters still apply to UTC days like every view.
        """
        w, p = self.where(f)
        rows = self.q(f"""
          SELECT CAST(strftime('%w', r.ts, 'localtime') AS INTEGER) wd,
                 CAST(strftime('%H', r.ts, 'localtime') AS INTEGER) hr,
                 COALESCE(SUM(r.est_cost_usd),0) cost, COALESCE(SUM(r.billable_tokens),0) tokens,
                 COUNT(*) requests
          FROM requests r WHERE {w} AND r.ts <> '' GROUP BY 1, 2""", p)
        grid = {(d, h): {"dow": d, "hour": h, "cost": 0.0, "tokens": 0, "requests": 0}
                for d in range(7) for h in range(24)}
        for r in rows:
            if r["wd"] is None or r["hr"] is None:
                continue
            c = grid[((r["wd"] + 6) % 7, r["hr"])]
            c["cost"], c["tokens"], c["requests"] = r["cost"], r["tokens"], r["requests"]
        return {"cells": [grid[(d, h)] for d in range(7) for h in range(24)],
                "tz": time.strftime("%Z"), "cost_basis": "estimated"}
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m unittest tests.test_parity -v` → 3 tests OK.

- [ ] **Step 5: API route + test.** In `finops/api.py` `api()`, next to the `timeline` route (after `a = A` is bound), add:

```python
        if route == "heatmap":
            return self.send_json(a.heatmap(f))
```

Append to `tests/test_api.py`:

```python
class TestParityRoutes(ServerFixture):
    def test_heatmap_route(self):
        code, body = self.get("/api/heatmap")
        self.assertEqual(code, 200)
        self.assertEqual(len(body["cells"]), 168)
```

Run: `python3 -m unittest tests.test_api -v` → all OK.

- [ ] **Step 6: Chart.** Append to `web/charts.js`:

```js
/* ---------- heatmap: weekday x hour ---------- */
const DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
export function heatmap(host, {cells, metric = 'cost', fmt = fmtNum}) {
  host.innerHTML = '';
  const max = Math.max(0, ...cells.map(c => +c[metric] || 0));
  if (!max) { host.innerHTML = '<div class="empty">No activity in range</div>'; return; }
  const W = Math.max(host.clientWidth || 640, 320);
  const m = {t: 4, r: 4, b: 18, l: 34};
  const cw = (W - m.l - m.r) / 24, ch = Math.min(22, Math.max(12, cw * 0.8));
  const H = m.t + ch * 7 + m.b;
  const svg = el('svg', {viewBox: `0 0 ${W} ${H}`, height: H, role: 'img'});
  DOW.forEach((d, i) => svg.appendChild(el('text', {x: m.l - 6, y: m.t + i * ch + ch * 0.68,
    'text-anchor': 'end'}, d)));
  for (let h = 0; h < 24; h += 3) svg.appendChild(el('text', {x: m.l + h * cw + cw / 2,
    y: H - 5, 'text-anchor': 'middle'}, String(h).padStart(2, '0')));
  cells.forEach(c => {
    const v = +c[metric] || 0;
    const r = el('rect', {x: m.l + c.hour * cw + 1, y: m.t + c.dow * ch + 1,
      width: Math.max(1, cw - 2), height: Math.max(1, ch - 2), rx: 2,
      fill: v ? 'var(--s1)' : 'var(--grid)',
      'fill-opacity': v ? (0.15 + 0.85 * Math.sqrt(v / max)).toFixed(3) : 1});
    r.addEventListener('mousemove', ev => showTip(
      `<div class="t">${DOW[c.dow]} ${String(c.hour).padStart(2, '0')}:00</div>
       <div class="row"><span class="k">Est. cost</span><span class="v">${fmtUSD(c.cost)}</span></div>
       <div class="row"><span class="k">Tokens</span><span class="v">${fmtNum(c.tokens)}</span></div>
       <div class="row"><span class="k">Requests</span><span class="v">${fmtInt(c.requests)}</span></div>`,
      ev.clientX, ev.clientY));
    r.addEventListener('mouseleave', hideTip);
    svg.appendChild(r);
  });
  host.appendChild(svg);
}
```

(`fmt` is accepted for API symmetry with other charts; the tooltip always shows all three measures.)

- [ ] **Step 7: Usage page card.** In `web/app.js` `VIEWS.usage`:
  - Change the fetch to `const [tl, models, ov, hm] = await Promise.all([api('timeline', \`&grain=${S.grain}\`), api('models'), api('overview'), api('heatmap')]);`
  - Insert after the `mlabel + ' over time'` card (before `'Model mix over time'`):

```js
    ${card('Peak hours', '<div class="chart" id="heat"></div>',
      {badge: hmMetric === 'cost' ? BADGE.estimated : BADGE.actual,
       hint: `your local time (${esc(hm.tz || 'local')}) · ${hmMetric === 'cost' ? 'estimated cost' : hmMetric}`})}
```

  - Before `page.innerHTML = ...` add: `const hmMetric = ['cost', 'tokens', 'requests'].includes(S.metric) ? S.metric : 'cost';`
  - After the `C.timeSeries($('#tl', page), …)` call add: `C.heatmap($('#heat', page), {cells: hm.cells, metric: hmMetric});`

- [ ] **Step 8: Commit**

```bash
git add finops/analytics.py finops/api.py web/charts.js web/app.js tests/test_parity.py tests/test_api.py
git commit -m "Add peak-hours heatmap in local time"
```

---

### Task 2: "Vs yesterday" on the overview

**Files:** Modify `finops/analytics.py` (`overview`, ~line 337), `web/app.js` (~line 503). Test: `tests/test_parity.py`.

**Interfaces:** Produces `overview()["cost_yesterday"] = {"c": float, "t": int, "n": int}`.

- [ ] **Step 1: Failing tests** — append to `tests/test_parity.py`:

```python
class TestYesterday(unittest.TestCase):
    def test_cost_yesterday(self):
        a = Analytics(make_db([("2026-03-09T10:00:00Z", "2026-03-09", 4.0, 10, "A"),
                               ("2026-03-10T10:00:00Z", "2026-03-10", 6.0, 10, "A")]))
        a._today = date(2026, 3, 10)
        o = a.overview({})
        self.assertAlmostEqual(o["cost_yesterday"]["c"], 4.0)
        self.assertAlmostEqual(o["cost_today"]["c"], 6.0)

    def test_no_spend_yesterday_is_zero_not_error(self):
        a = Analytics(make_db([("2026-03-10T10:00:00Z", "2026-03-10", 6.0, 10, "A")]))
        a._today = date(2026, 3, 10)
        self.assertEqual(a.overview({})["cost_yesterday"]["c"], 0)
```

- [ ] **Step 2:** Run `python3 -m unittest tests.test_parity.TestYesterday -v` → FAIL `KeyError: 'cost_yesterday'`.

- [ ] **Step 3: Implement** — in `overview()`, after `tot["cost_today"] = spend(...)`:

```python
        tot["cost_yesterday"] = spend("r.day = ?", [(_d(today) - timedelta(days=1)).isoformat()])
```

- [ ] **Step 4:** Run again → PASS.

- [ ] **Step 5: UI** — in `web/app.js` overview, replace the spend KPI detail
`` `${fmtUSD(o.cost_today.c)} today · ${fmtUSD(o.cost_week.c)} last 7d` `` with
`` `${fmtUSD(o.cost_today.c)} today${vsYesterday(o)} · ${fmtUSD(o.cost_week.c)} last 7d` `` and add near `kpi()`:

```js
// "(▲12% vs yesterday)"; nothing when yesterday had no spend to compare against.
const vsYesterday = o => {
  const y = o.cost_yesterday?.c, t = o.cost_today?.c;
  if (!y || t == null) return '';
  const d = (t - y) / y * 100;
  return ` (${d >= 0 ? '▲' : '▼'}${Math.abs(d).toFixed(0)}% vs yesterday)`;
};
```

- [ ] **Step 6: Commit** — `git add finops/analytics.py web/app.js tests/test_parity.py && git commit -m "Show today's spend against yesterday on the overview"`

---

### Task 3: `claude --resume` in the session drawer

**Files:** Modify `finops/analytics.py` (`session_detail`, ~line 657), `web/app.js` (`openSession`, ~line 418). Test: `tests/test_parity.py`.

**Interfaces:** Produces `session_detail(sid)["resume"]: str | None`.

- [ ] **Step 1: Failing tests** — append:

```python
class TestResume(unittest.TestCase):
    def test_resume_for_cli_session_not_cowork(self):
        cowork = "/L/Claude/local-agent-mode-sessions/o/u/local_1/.claude/projects/-x/B.jsonl"
        a = Analytics(make_db([("2026-03-10T10:00:00Z", "2026-03-10", 1.0, 1, "A"),
                               ("2026-03-10T11:00:00Z", "2026-03-10", 1.0, 1, "B")],
                              sessions=(("A", "/h/.claude/projects/-p/A.jsonl"), ("B", cowork))))
        self.assertEqual(a.session_detail("A")["resume"], "claude --resume A")
        self.assertIsNone(a.session_detail("B")["resume"])
```

- [ ] **Step 2:** Run → FAIL `KeyError: 'resume'`.

- [ ] **Step 3: Implement** — in `session_detail`, before `return s`:

```python
        # Cowork transcripts live under the desktop app's own config dir, where
        # `claude --resume` would not find them.
        cowork = "local-agent-mode-sessions" in (s.get("source_file") or "")
        s["resume"] = (f"claude --resume {sid}"
                       if (s.get("agent") or "claude") == "claude" and not cowork else None)
```

- [ ] **Step 4:** Run → PASS.

- [ ] **Step 5: UI** — in `openSession`, add as the first rows of the `Session metadata` `<dl>`:

```js
      ${s.resume ? `<dt>Resume</dt><dd><span class="mono">${esc(s.resume)}</span>
        <button class="btn pb-copy" id="sess-resume" data-copy="${esc(s.resume)}">Copy</button></dd>` : ''}
```

and after `wireTable(cont, s.prompts, …)`:

```js
  const rb = cont.querySelector('#sess-resume');
  if (rb) rb.onclick = async () => {
    try { await navigator.clipboard.writeText(rb.dataset.copy); rb.textContent = 'Copied'; }
    catch { rb.textContent = 'Copy failed'; } };
```

- [ ] **Step 6: Commit** — `git commit -am "Offer claude --resume from the session drawer"` (after `git add tests/test_parity.py`).

---

### Task 4: Claude desktop app Cowork transcripts

**Files:** Modify `finops/paths.py`, `finops/etl.py` (`Loader.__init__`, `build`, `project_id`), `finops/agents.py` (Claude note). Test: `tests/test_etl.py`.

**Interfaces:**
- Produces: `paths.DESKTOP_DIR: str`, `paths.DESKTOP_SESSIONS: str`, `paths.PLAN_HISTORY_PATH: str` (used by Task 5); `Loader(..., desktop_roots=None)`; meta key `desktop_transcript_files`.

- [ ] **Step 1: Failing tests** — append to `tests/test_etl.py`:

```python
class TestCoworkTranscripts(unittest.TestCase):
    def _desktop(self):
        root = tempfile.mkdtemp(prefix="finops-desktop-")
        proj = os.path.join(root, "org", "user", "local_1", ".claude", "projects", "-cowork-x")
        os.makedirs(proj)
        rows = [user("2026-01-01T00:00:00Z", "plan my week", cwd="/tmp/outputs"),
                assistant("2026-01-01T00:00:05Z", "req-c", "msg-c", {"type": "text", "text": "ok"})]
        with open(os.path.join(proj, "c1.jsonl"), "w") as fh:
            fh.write("\n".join(json.dumps(r) for r in rows) + "\n")
        with open(os.path.join(root, "org", "user", "local_1", "audit.jsonl"), "w") as fh:
            fh.write(json.dumps({"type": "user", "message": {"content": "x"}}) + "\n")
        return root

    def _build(self, **kw):
        src = tempfile.mkdtemp(prefix="finops-src-")
        db = tempfile.mktemp(suffix=".db", prefix="finops-test-")
        Loader(db_path=db, source=src, other_agents=False, **kw).build(verbose=False)
        return sqlite3.connect(db)

    def test_cowork_loaded_with_readable_name_and_audit_skipped(self):
        con = self._build(desktop_roots=[self._desktop()])
        self.assertEqual(con.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 1)
        self.assertEqual(con.execute("SELECT name FROM projects").fetchone()[0], "Cowork · outputs")
        self.assertEqual(con.execute(
            "SELECT value FROM meta WHERE key='desktop_transcript_files'").fetchone()[0], "1")

    def test_explicit_source_does_not_scan_desktop(self):
        con = self._build()
        self.assertEqual(con.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 0)

    def test_missing_desktop_root_is_fine(self):
        con = self._build(desktop_roots=["/nonexistent/finops-desktop"])
        self.assertEqual(con.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 0)
```

(`user(ts, text, **extra)` already merges `cwd=` into the row.)

- [ ] **Step 2:** Run `python3 -m unittest tests.test_etl.TestCoworkTranscripts -v` → FAIL `unexpected keyword argument 'desktop_roots'`.

- [ ] **Step 3: Paths** — append to `finops/paths.py`:

```python
# The Claude desktop app's data dir. Read-only: Cowork transcripts and the plan
# usage history live here.
if os.name == "nt":
    DESKTOP_DIR = os.path.join(os.environ.get("APPDATA", ""), "Claude")
elif os.uname().sysname == "Darwin":
    DESKTOP_DIR = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Claude")
else:
    DESKTOP_DIR = os.path.join(os.path.expanduser("~"), ".config", "Claude")
DESKTOP_SESSIONS = os.path.join(DESKTOP_DIR, "local-agent-mode-sessions")
PLAN_HISTORY_PATH = os.path.join(DESKTOP_DIR, "plan-usage-history.json")
```

- [ ] **Step 4: Loader** — in `finops/etl.py`:
  - Import: `from .paths import ROOT, DB_PATH, DESKTOP_SESSIONS`
  - `__init__` signature gains `desktop_roots=None`; body adds:

```python
        # An explicit source means "load exactly this"; only the default also picks up
        # the desktop app's Cowork sessions (same JSONL format, different config dir).
        self.desktop_roots = (desktop_roots if desktop_roots is not None
                              else [DESKTOP_SESSIONS] if source == DEFAULT_SOURCE else [])
        self.cowork = False
```

  - In `build`, after `files.sort()`:

```python
        marker = os.sep + os.path.join(".claude", "projects") + os.sep
        desktop = sorted(os.path.join(d, n) for root in self.desktop_roots
                         for d, _, names in os.walk(root) for n in names
                         if n.endswith(".jsonl") and marker in d + os.sep)
```

  - Replace the load loop so both lists load, flagging Cowork:

```python
        for i, fp in enumerate(files + desktop, 1):
            if verbose and i % 20 == 0:
                print(f"  ...{i}/{len(files) + len(desktop)} transcripts", file=sys.stderr)
            self.cowork = i > len(files)
            try:
                self.load_file(fp)
            except Exception as exc:  # a corrupt transcript must not kill the load
                print(f"  ! skipped {os.path.basename(fp)}: {exc}", file=sys.stderr)
        self.cowork = False
```

  - Add `("desktop_transcript_files", str(len(desktop))),` to the meta tuple, and change `return len(files)` to `return len(files) + len(desktop)`.
  - In `project_id`, change the `name = ...` line to:

```python
        name = os.path.basename(cwd) if cwd else _slug_to_name(slug)
        if self.cowork:
            name = f"Cowork · {name or 'session'}"
```

- [ ] **Step 5:** Run `python3 -m unittest tests.test_etl -v` → all OK.

- [ ] **Step 6: Agents note** — in `finops/agents.py` `AGENTS["claude"]["note"]`, change to:
`"Tokens, model, cost, prompts and tool calls per request. Includes Claude desktop app (Cowork) sessions when present."`

- [ ] **Step 7: Commit** — `git add finops/paths.py finops/etl.py finops/agents.py tests/test_etl.py && git commit -m "Load Claude desktop app Cowork transcripts"`

---

### Task 5: Plan-limit history

**Files:** Create `finops/plan_history.py`; modify `finops/api.py`, `web/charts.js` (`timeSeries` `max` option), `web/app.js` (`VIEWS.burn`). Test: `tests/test_plan_history.py` (new), `tests/test_api.py`.

**Interfaces:**
- Consumes: `paths.PLAN_HISTORY_PATH` (Task 4).
- Produces: `plan_history.history(path=None) -> dict` (shape in spec §5); `GET /api/plan_history`; `timeSeries` opt `max` (number, y-axis floor for the top).

- [ ] **Step 1: Failing tests** — create `tests/test_plan_history.py`:

```python
"""Plan-limit history parsing. Run: python3 -m unittest tests.test_plan_history -v"""
import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finops import plan_history


def write(obj):
    p = tempfile.mktemp(suffix=".json", prefix="finops-plan-")
    with open(p, "w") as fh:
        fh.write(obj if isinstance(obj, str) else json.dumps(obj))
    return p


def s(t, fh, sd, org="o1"):
    return {"t": t, "org": org, "u": {"fh": fh, "sd": sd}}


class TestPlanHistory(unittest.TestCase):
    def test_series_and_summary(self):
        r = plan_history.history(write({"version": 2, "samples": [
            s(1000, 10, 5), s(2000, 95, 20), s(3000, 100, 30), s(4000, 40, 31), s(5000, 91, 32)]}))
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["series"]), 5)
        self.assertEqual(r["series"][0], {"t": "1970-01-01T00:00:01Z", "five_hour": 10.0, "weekly": 5.0})
        sm = r["summary"]
        self.assertEqual(sm["five_hour_peak"], 100.0)
        self.assertEqual(sm["five_hour_ge90"], 2)     # two separate runs at 90%+
        self.assertEqual(sm["five_hour_hit100"], 1)
        self.assertEqual(sm["weekly_peak"], 32.0)

    def test_missing_file(self):
        r = plan_history.history("/nonexistent/plan-usage-history.json")
        self.assertFalse(r["ok"]); self.assertIn("reason", r)

    def test_malformed_json(self):
        self.assertFalse(plan_history.history(write("{not json"))["ok"])

    def test_unknown_version(self):
        self.assertFalse(plan_history.history(write({"version": 99, "samples": [s(1, 1, 1)]}))["ok"])

    def test_latest_org_wins(self):
        r = plan_history.history(write({"version": 2, "samples": [
            s(1000, 50, 5, "old"), s(2000, 60, 6, "new"), s(3000, 70, 7, "new")]}))
        self.assertEqual(r["org_count"], 2)
        self.assertEqual([x["five_hour"] for x in r["series"]], [60.0, 70.0])

    def test_bad_values_become_gaps(self):
        r = plan_history.history(write({"version": 2, "samples": [
            s(1000, "x", None), {"t": 2000, "org": "o1", "u": {}}, s(3000, 20, 2), {"t": "bad"}]}))
        self.assertEqual([x["five_hour"] for x in r["series"]], [None, None, 20.0])
        self.assertEqual(r["summary"]["five_hour_peak"], 20.0)
```

- [ ] **Step 2:** Run → FAIL `ImportError: cannot import name 'plan_history'`.

- [ ] **Step 3: Implement** — create `finops/plan_history.py`:

```python
"""Plan-limit history, as the Claude desktop app records it.

The desktop app samples plan usage every few minutes into plan-usage-history.json:
`fh` is the 5-hour window and `sd` the weekly (seven-day) window, both percent used.
The format is undocumented, so anything unexpected returns {"ok": False, "reason"}
instead of raising. Read on request, cached on the file's mtime.
"""
import json
import os
from datetime import datetime, timezone

from .paths import PLAN_HISTORY_PATH

KNOWN_VERSIONS = {2}
_cache = {}


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _runs(values, threshold):
    """Separate stretches at or above the threshold, not raw sample counts."""
    n, above = 0, False
    for v in values:
        hit = v is not None and v >= threshold
        n += hit and not above
        above = hit
    return n


def _peak(values):
    return max((v for v in values if v is not None), default=None)


def history(path=None):
    path = path or PLAN_HISTORY_PATH
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {"ok": False, "reason": "No plan history on this machine. The Claude desktop "
                                       "app records it; it is not installed or has not saved any yet."}
    hit = _cache.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    res = _read(path)
    _cache[path] = (mtime, res)
    return res


def _read(path):
    try:
        with open(path) as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return {"ok": False, "reason": "The plan history file could not be read."}
    version = raw.get("version") if isinstance(raw, dict) else None
    if version not in KNOWN_VERSIONS:
        return {"ok": False, "reason": f"Unrecognised plan history format (version {version})."}
    samples = [x for x in raw.get("samples") or []
               if isinstance(x, dict) and _num(x.get("t")) is not None]
    if not samples:
        return {"ok": False, "reason": "The plan history file has no samples yet."}
    samples.sort(key=lambda x: x["t"])
    orgs = {x.get("org") for x in samples}
    org = samples[-1].get("org")
    series = []
    for x in samples:
        if x.get("org") != org:
            continue
        u = x.get("u") if isinstance(x.get("u"), dict) else {}
        t = datetime.fromtimestamp(x["t"] / 1000, timezone.utc).isoformat().replace("+00:00", "Z")
        series.append({"t": t, "five_hour": _num(u.get("fh")), "weekly": _num(u.get("sd"))})
    fh = [r["five_hour"] for r in series]
    sd = [r["weekly"] for r in series]
    return {"ok": True, "source": path, "org_count": len(orgs), "series": series,
            "summary": {"five_hour_peak": _peak(fh), "weekly_peak": _peak(sd),
                        "five_hour_ge90": _runs(fh, 90), "five_hour_hit100": _runs(fh, 100),
                        "weekly_ge90": _runs(sd, 90), "weekly_hit100": _runs(sd, 100),
                        "first": series[0]["t"], "last": series[-1]["t"]}}
```

Note: `datetime.fromtimestamp(1000/1000)` → `1970-01-01T00:00:01Z`, matching the test. ISO output of a whole-second timestamp has no fractional part.

- [ ] **Step 4:** Run `python3 -m unittest tests.test_plan_history -v` → 6 OK.

- [ ] **Step 5: API route + test.** In `finops/api.py` `api()`, next to the `usage` route (before `f = filters_from(qs)`):

```python
        if route == "plan_history":
            from .plan_history import history
            return self.send_json(history())
```

Append to `TestParityRoutes` in `tests/test_api.py`:

```python
    def test_plan_history_route_never_errors(self):
        from finops import plan_history
        orig = plan_history.PLAN_HISTORY_PATH
        plan_history.PLAN_HISTORY_PATH = "/nonexistent/plan.json"
        try:
            code, body = self.get("/api/plan_history")
        finally:
            plan_history.PLAN_HISTORY_PATH = orig
        self.assertEqual(code, 200)
        self.assertFalse(body["ok"])
```

(`history()` reads the module global at call time, so the patch takes effect.) Run `python3 -m unittest tests.test_api -v` → OK.

- [ ] **Step 6: `timeSeries` max option** — in `web/charts.js` `timeSeries`, replace
`const max = nice(Math.max(...totals, 0) || 1);` with
`const max = Math.max(opts.max || 0, nice(Math.max(...totals, 0) || 1));`

- [ ] **Step 7: Burn page card** — in `web/app.js` `VIEWS.burn`:
  - Change the first line to `const [burn, plan] = await Promise.all([api('burn'), fetch('/api/plan_history').then(r => r.json()).catch(e => ({ok: false, reason: e.message}))]);`
  - Insert before the `'Daily consumption within the billing period'` card:

```js
    ${card('Plan limits over time', plan.ok ? `
      <div class="grid g3">
        ${kpi('5-hour peak', fmtPct(plan.summary.five_hour_peak ?? 0, 0), null, {badge: BADGE.actual})}
        ${kpi('Times at 90%+ (5-hour)', fmtInt(plan.summary.five_hour_ge90),
          `${fmtInt(plan.summary.five_hour_hit100)} reached 100%`)}
        ${kpi('Weekly peak', fmtPct(plan.summary.weekly_peak ?? 0, 0),
          `${fmtInt(plan.summary.weekly_ge90)} times at 90%+`)}
      </div>
      <div class="legend" id="planleg"></div><div class="chart" id="plan"></div>`
      : `<div class="empty">${esc(plan.reason || 'Unavailable')}</div>`,
      {badge: BADGE.actual, hint: '5-hour and weekly plan usage, % used',
       footer: 'Read from the Claude desktop app\'s local plan-usage-history.json. '
             + 'Its format is undocumented, so this card may go blank after an app update.'})}
```

  - After the existing gauge loop, add:

```js
  if (plan.ok) {
    const ps = [{key: 'five_hour', label: '5-hour window', color: seriesVar(0), fmt: v => fmtPct(v, 0)},
                {key: 'weekly', label: 'Weekly window', color: seriesVar(1), fmt: v => fmtPct(v, 0)}];
    C.legend($('#planleg', page), ps.map(s => ({label: s.label, color: s.color})));
    C.timeSeries($('#plan', page), {rows: plan.series, x: 't', type: 'line', series: ps,
      fmt: v => fmtPct(v, 0), max: 100, height: 220,
      xLabel: t => new Date(t).toLocaleString([], {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'})});
  }
```

- [ ] **Step 8: Commit** — `git add finops/plan_history.py finops/api.py web/charts.js web/app.js tests/test_plan_history.py tests/test_api.py && git commit -m "Chart plan-limit history from the Claude desktop app"`

---

### Task 6: Older Claude models in the price table

**Files:** Modify `config/pricing.json`. Test: `tests/test_parity.py`.

- [ ] **Step 1: Failing test** — append to `tests/test_parity.py`:

```python
class TestLegacyPrices(unittest.TestCase):
    def test_older_claude_ids_are_priced(self):
        from finops.pricing import Pricing
        p = Pricing()
        for mid, inp, out in [("claude-sonnet-4-6", 3, 15), ("claude-sonnet-4-5-20250929", 3, 15),
                              ("claude-opus-4-1-20250805", 15, 75), ("claude-opus-4-8", 5, 25),
                              ("claude-3-5-haiku-20241022", 0.8, 4)]:
            self.assertTrue(p.is_known(mid), mid)
            self.assertEqual((p.rates(mid)["input"], p.rates(mid)["output"]), (inp, out), mid)
```

- [ ] **Step 2:** Run → FAIL on `claude-sonnet-4-6`.

- [ ] **Step 3: Add entries** to `"models"` in `config/pricing.json` (USD per MTok, from https://platform.claude.com/docs/en/about-claude/pricing, 2026-09-28). All have `"provider": "anthropic"`. Keys are undated: `Pricing.normalize` strips `-YYYYMMDD` suffixes.

| key | display_name | tier | input | output | cache_write_5m | cache_write_1h | cache_read | context_window |
|---|---|---|---|---|---|---|---|---|
| claude-opus-4-8 | Claude Opus 4.8 | frontier | 5.0 | 25.0 | 6.25 | 10.0 | 0.5 | 1000000 |
| claude-opus-4-8[fast] | Claude Opus 4.8 (fast mode) | frontier | 10.0 | 50.0 | 12.5 | 20.0 | 1.0 | 1000000 |
| claude-opus-4-7 | Claude Opus 4.7 | frontier | 5.0 | 25.0 | 6.25 | 10.0 | 0.5 | 1000000 |
| claude-opus-4-6 | Claude Opus 4.6 | frontier | 5.0 | 25.0 | 6.25 | 10.0 | 0.5 | 1000000 |
| claude-opus-4-5 | Claude Opus 4.5 | frontier | 5.0 | 25.0 | 6.25 | 10.0 | 0.5 | 200000 |
| claude-opus-4-1 | Claude Opus 4.1 | frontier | 15.0 | 75.0 | 18.75 | 30.0 | 1.5 | 200000 |
| claude-opus-4 | Claude Opus 4 | frontier | 15.0 | 75.0 | 18.75 | 30.0 | 1.5 | 200000 |
| claude-sonnet-4-6 | Claude Sonnet 4.6 | balanced | 3.0 | 15.0 | 3.75 | 6.0 | 0.3 | 1000000 |
| claude-sonnet-4-5 | Claude Sonnet 4.5 | balanced | 3.0 | 15.0 | 3.75 | 6.0 | 0.3 | 200000 |
| claude-sonnet-4 | Claude Sonnet 4 | balanced | 3.0 | 15.0 | 3.75 | 6.0 | 0.3 | 200000 |
| claude-3-5-haiku | Claude Haiku 3.5 | economy | 0.8 | 4.0 | 1.0 | 1.6 | 0.08 | 200000 |

Context windows: "Claude 4.6 and later … include the full 1M token context window at standard pricing" (pricing page); earlier models 200k. Opus 4.8 fast mode: $10/$50 (pricing page, fast mode table); cache multipliers stack on the fast rate, matching the existing `claude-opus-5[fast]` entry.

Also set `"updated": "2026-09-28"` and `"source": "Anthropic list prices (platform.claude.com/docs/en/about-claude/pricing, fetched 2026-09-28)"`.

The "cheaper tier" pick in `analytics.py` (~line 1166) takes the lowest-output `balanced` model — still `claude-sonnet-5` ($10), so recommendations don't change. `compare` only lists models present in `config/model_compare.json`, so retired models don't appear there.

- [ ] **Step 4:** Run `python3 -m unittest tests.test_parity -v` → OK.
- [ ] **Step 5: Commit** — `git add config/pricing.json tests/test_parity.py && git commit -m "Price Claude 4.x and Haiku 3.5 at published list rates"`

---

### Task 7: Verify end to end

- [ ] **Step 1:** `python3 -m unittest discover tests` → all pass (107 + new).
- [ ] **Step 2:** Rebuild the real warehouse: `python3 -m finops.etl` → output reports transcripts including the Cowork ones; `sqlite3 ~/.claude-finops/data/finops.db "select value from meta where key='desktop_transcript_files'"` → `2`.
- [ ] **Step 3:** `./run.sh`, open http://127.0.0.1:8787 and check: overview spend card shows "vs yesterday"; Usage timeline shows Peak hours with local-time hours; Burn rate & limits shows Plan limits over time; a session drawer shows Resume + Copy; Projects lists a `Cowork · …` project; Models no longer marks `claude-sonnet-4-6` unpriced. `./run.sh --stop` afterwards.
- [ ] **Step 4:** Add a CHANGES.md entry under a new `## Unreleased` heading describing the six changes in the file's plain-language style; commit.
