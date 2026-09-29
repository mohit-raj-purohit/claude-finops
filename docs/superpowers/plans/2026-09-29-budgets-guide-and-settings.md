# Budgets Guide, Simpler Budgets Form and Settings Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Budgets page understandable by anyone: numbered blocks with plain intros, an explicit "optional" Live warnings box, a picture-slide guide popup, and API keys moved to a new Settings page.

**Architecture:** All UI lives in `web/app.js` (one file, views registered on `VIEWS`, helpers are plain functions) and `web/styles.css`. The guide is a small engine (`GUIDES`, `openGuide`) that any page can supply slides to. Slide pictures are live, disabled copies of the real blocks built with the same field helpers, plus one real PNG captured from a synthetic demo warehouse made by `tools/demo_data.py`.

**Tech Stack:** Python 3 stdlib (server, ETL, tests with `unittest`), vanilla JS (no build step), CSS.

**Spec:** `docs/superpowers/specs/2026-09-29-budgets-guide-and-settings-design.md`

## Global Constraints

- Plain language on every new string: short sentences, everyday words, one idea per sentence.
- First use of a technical word on a surface is explained: token = "the small pieces of text Claude reads and writes (about ¾ of a word)"; session = "one Claude Code conversation"; guard/hook = "a small helper that runs inside Claude Code".
- Slides and screenshots never show the user's real data; only demo values.
- Install guard is described as optional everywhere: "Your limits above already work without this."
- No new runtime dependencies; no build step. `tools/` is never added to `package.json` `files`.
- `localStorage` access is wrapped in try/catch; failure means the guide simply does not auto-open.
- Commit as `mohitrj49@gmail.com`: `git -c user.email=mohitrj49@gmail.com commit ...`, ending the message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- The user's uncommitted popover fix in `web/app.js` (functions `filterBar` range handler and `openCustom`) must never be committed: stage `web/app.js` by applying a patch that excludes it (see "Committing web/app.js" below).

### Committing web/app.js

The working tree carries an unrelated uncommitted change. A copy of it is saved at
`$SP/popover.patch` where `SP=/private/tmp/claude-501/-Users-mohitrajpurohit/79fd1c76-e8e5-4102-8458-0babc3cb668b/scratchpad`. To commit only your changes:

```bash
git diff web/app.js > "$SP/app-now.patch"
git apply --cached "$SP/app-now.patch"
git apply --cached -R "$SP/popover.patch"
git diff web/app.js | grep -c "openCustom"   # must still show the popover lines as unstaged
```

## Review Focus

1. **Guide opened while the welcome tour is showing** (first ever visit): both must not stack; the guide waits until the tour closes. Pinned in Task 4, step "first-visit".
2. **Keyboard users**: Tab must stay inside the open guide, Esc closes it, and focus returns to the button that opened it. Pinned in Task 4.
3. **Saving a key on Settings when Billed vs local was hidden** (the nav hides it until a key exists): after Save key the sidebar must show Billed vs local without a reload. Pinned in Task 2.
4. **Live copies colliding with real form IDs** (duplicate `id="b-monthly"` would make the real Save read the copy): every live copy prefixes IDs. Pinned in Task 5 with a DOM check.
5. **Installed guard with overrides only** (global budget empty, a project override set): the Live warnings box must not say "does nothing". Pinned in Task 3.

---

## File Structure

- `tools/demo_data.py` (create): synthetic transcripts → demo warehouse + demo settings, for the screenshot. Not packaged.
- `tools/README.md` (create): how to regenerate the demo home and recapture `web/guide/budgets.png`.
- `tests/test_demo_data.py` (create): generator output checks.
- `web/app.js` (modify): nav entry + `VIEWS.settings`; Budgets restructure; guide engine; Budgets slides; tour text.
- `web/styles.css` (modify): `.blk` blocks, `.livebox`, `details.adv`, `.guide` dialog, `.pulse`.
- `web/guide/budgets.png` (create): the one real screenshot.
- `README.md` (modify): Budgets and Settings wording, guide mention.

---

### Task 1: Demo data generator

**Files:**
- Create: `tools/demo_data.py`, `tools/README.md`
- Test: `tests/test_demo_data.py`

**Interfaces:**
- Produces: `tools.demo_data.build(home: str, days: int = 30, seed: int = 7) -> dict` returning `{"home": home, "projects": [...names], "sessions": int}`; CLI `python3 tools/demo_data.py [home]` prints the `CLAUDE_FINOPS_HOME=...` line to run with.

- [ ] **Step 1: Write the failing test**

```python
"""Demo data for the guide screenshot. Run: python3 -m unittest tests.test_demo_data -v"""
import json, os, sqlite3, sys, tempfile, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import demo_data


class TestDemoData(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="finops-demo-")
        self.out = demo_data.build(self.home)

    def test_builds_a_warehouse_with_demo_projects(self):
        db = sqlite3.connect(os.path.join(self.home, "data", "finops.db"))
        names = {r[0] for r in db.execute("SELECT name FROM projects")}
        self.assertTrue({"shop-app", "blog", "data-pipeline"} <= names)
        sizes = [r[0] for r in db.execute("SELECT billable_tokens FROM sessions ORDER BY 1")]
        self.assertGreaterEqual(len(sizes), 20)
        self.assertGreater(sizes[-1], 10 * sizes[len(sizes) // 2])   # a real spread

    def test_writes_example_budgets(self):
        with open(os.path.join(self.home, "settings.local.json")) as fh:
            s = json.load(fh)
        self.assertGreater(s["budgets"]["monthly_usd"], 0)
        self.assertGreater(s["guard"]["session_tokens"], 0)

    def test_contains_nothing_from_the_real_machine(self):
        real = os.path.expanduser("~")
        for dirpath, _, files in os.walk(self.home):
            for f in files:
                if f.endswith((".jsonl", ".json")):
                    with open(os.path.join(dirpath, f), errors="replace") as fh:
                        self.assertNotIn(real, fh.read(), f)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_demo_data -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'demo_data'`

- [ ] **Step 3: Write the implementation**

```python
"""Made-up usage for screenshots and demos. Never reads anything from this machine.

  python3 tools/demo_data.py [home]      # default: a new temp dir
  CLAUDE_FINOPS_HOME=<home> CLAUDE_PROJECTS=<home>/projects ./run.sh --foreground

Writes Claude Code-shaped transcripts for a few invented projects under
<home>/projects, builds <home>/data/finops.db from them, and writes example
budgets to <home>/settings.local.json.
"""
import json
import os
import random
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PROJECTS = {"shop-app": 1.6, "blog": 0.5, "data-pipeline": 1.0, "mobile-client": 0.8}
MODEL = "claude-sonnet-4-5"


def _usage(rng, ctx):
    out = rng.randint(200, 2500)
    return {"input_tokens": rng.randint(5, 60), "output_tokens": out,
            "cache_read_input_tokens": ctx, "cache_creation_input_tokens": rng.randint(500, 6000)}


def _session(rng, project, start, turns):
    cwd = f"/work/{project}"
    rows, ctx, ts = [], 20000, start
    for t in range(turns):
        ts += timedelta(seconds=rng.randint(20, 240))
        rows.append({"type": "user", "uuid": f"u{t}-{ts.timestamp()}", "timestamp": ts.isoformat(),
                     "cwd": cwd, "message": {"role": "user", "content": f"step {t + 1} of the {project} task"}})
        ts += timedelta(seconds=rng.randint(3, 40))
        ctx += rng.randint(1500, 9000)
        rows.append({"type": "assistant", "uuid": f"a{t}-{ts.timestamp()}", "timestamp": ts.isoformat(),
                     "requestId": f"req-{project}-{start.timestamp()}-{t}", "cwd": cwd,
                     "message": {"id": f"msg-{project}-{start.timestamp()}-{t}", "model": MODEL,
                                 "role": "assistant", "usage": _usage(rng, ctx),
                                 "content": [{"type": "text", "text": "done"}]}})
    return rows


def build(home, days=30, seed=7):
    from finops.etl import Loader
    rng = random.Random(seed)
    src = os.path.join(home, "projects")
    today = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
    n = 0
    for project, weight in PROJECTS.items():
        pdir = os.path.join(src, "-work-" + project)
        os.makedirs(pdir, exist_ok=True)
        for d in range(days):
            if rng.random() > 0.35 * weight + 0.2:
                continue
            turns = int(rng.lognormvariate(2.3, 0.9)) + 1        # mostly short, a few very long
            start = today - timedelta(days=days - 1 - d, hours=rng.randint(0, 8))
            with open(os.path.join(pdir, f"demo-{project}-{d}.jsonl"), "w") as fh:
                for r in _session(rng, project, start, min(turns, 160)):
                    fh.write(json.dumps(r) + "\n")
            n += 1
    os.makedirs(os.path.join(home, "data"), exist_ok=True)
    # other_agents=False and desktop_roots=[]: never pick up this machine's real
    # Cursor/Codex/Gemini data or Claude desktop (Cowork) sessions
    Loader(db_path=os.path.join(home, "data", "finops.db"), source=src,
           other_agents=False, desktop_roots=[]).build(verbose=False)
    with open(os.path.join(home, "settings.local.json"), "w") as fh:
        json.dump({"budgets": {"monthly_usd": 400, "daily_usd": 25, "monthly_tokens": 900000000},
                   "guard": {"session_tokens": 8000000, "warn_pct": [75, 80],
                             "after_approval": "step", "step_pct": 25,
                             "projects": {"/work/shop-app": {"session_tokens": 15000000}}}}, fh, indent=2)
    open(os.path.join(home, ".migrated"), "w").close()
    return {"home": home, "projects": list(PROJECTS), "sessions": n}


if __name__ == "__main__":
    home = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="finops-demo-")
    out = build(home)
    print(f"{out['sessions']} demo sessions in {home}")
    print(f"CLAUDE_FINOPS_HOME={home} CLAUDE_PROJECTS={home}/projects ./run.sh --foreground")
```

`Loader(db_path, source, pricing=None, other_agents=True, desktop_roots=None)` is the signature in `finops/etl.py:206`. `CLAUDE_PROJECTS` is read by `run.py` only at startup; the dashboard's **Sync** button (`finops/actions.py` `sync`) reads the real `~/.claude/projects`, so never press Sync on the demo home.

- [ ] **Step 4: Write `tools/README.md`**

```markdown
# tools/

Developer helpers. Not shipped in the npm package.

## demo_data.py — made-up usage for screenshots

    python3 tools/demo_data.py /tmp/finops-demo
    CLAUDE_FINOPS_HOME=/tmp/finops-demo CLAUDE_PROJECTS=/tmp/finops-demo/projects PORT=8790 ./run.sh --foreground

Everything in it is invented. Use it for every screenshot, never real data.

**Never press Sync on the demo dashboard**: Sync re-reads your real `~/.claude/projects`
and would replace the demo warehouse with your own data.

### Recapturing web/guide/budgets.png

1. Start the dashboard on the demo home as above, open http://127.0.0.1:8790, skip the tour.
2. Open Budgets. Window width 1280 px. Scroll to the top of the page.
3. Screenshot the page area (no sidebar needed), crop to the results card and the
   "Set your limits" card down to block ③, and save as `web/guide/budgets.png` (< 250 KB).
4. If the layout changed, update the `marks` positions of slide `overview` in `GUIDES.budgets`.
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_demo_data -v`
Expected: 3 tests OK.

- [ ] **Step 6: Commit**

```bash
git add tools/ tests/test_demo_data.py
git -c user.email=mohitrj49@gmail.com commit -m "Demo data generator for guide screenshots

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Settings page with API keys

**Files:**
- Modify: `web/app.js` — `NAV` (line ~123), `drawKeys` (budgets helpers), `VIEWS.budgets` (remove API keys block), `VIEWS.cloud` setup card text, `TOURS.cloud` step, add `TOURS.settings`, add `VIEWS.settings`.

**Interfaces:**
- Consumes: `drawKeys(page)` (exists; renders into `#cfg-keys` inside `page`), `GET /api/keys`, `POST /api/do/key/<pid>`.
- Produces: view id `settings`; `drawKeys` refreshes `S.opts` and calls `applyAgentChrome()` after a save/remove.

- [ ] **Step 1: Add the nav group** (end of `NAV`)

```js
  ['Setup', [
    ['settings', 'cog', 'Settings'],
  ]],
```

- [ ] **Step 2: Add the view** (after `VIEWS.budgets`)

```js
/* ---------- settings ---------- */
VIEWS.settings = async (page) => {
  page.innerHTML = card('API keys', `<div class="cfg">
      <div class="fld-hint" style="margin-bottom:8px">You only need these for the <b>Billed vs local</b> page,
        which compares what the company that makes the tool billed with what this computer recorded.
        Skip this if you don't use it. Keys stay on this computer, in a file only you can read, and are
        never shown again.</div>
      <div id="cfg-keys" class="stack"><div class="note">Loading…</div></div></div>`,
    {hint: 'optional'});
  drawKeys(page);
};
```

- [ ] **Step 3: Remove the API keys block from `VIEWS.budgets`**: delete the `<div class="sec" ...>API keys ...` heading, its intro `fld-hint`, the `<div id="cfg-keys">` element and the trailing `drawKeys(page);` call.

- [ ] **Step 4: Keep the sidebar in sync after a key change.** In `drawKeys`, replace the body of `done`:

```js
  const done = async (pid, text) => {
    S.opts = await fetch('/api/options').then(r => r.json());
    applyAgentChrome();                    // Billed vs local appears once a key exists
    await drawKeys(page);
    const ok = $('#key-' + pid + '-ok', page); if (ok) ok.textContent = text;
  };
```

- [ ] **Step 5: Point Billed vs local at Settings.** In `VIEWS.cloud` setup HTML replace `<a href="#" data-go-budgets>Budgets</a> page` with `<a href="#" data-go-settings>Settings</a> page`, and the handler line with:

```js
  page.querySelectorAll('[data-go-settings]').forEach(a => a.onclick = e => { e.preventDefault(); go('settings'); });
```

In `TOURS.cloud`, the "Set up the APIs" step `act` becomes `'Add a key on the Settings page, or run claude-finops --set-key.'`. Remove the `API keys` step from `TOURS.budgets` and add:

```js
  settings: [
    {el: 'card:API keys', t: 'API keys', see: 'Optional keys for the Anthropic Admin API and Cursor.', get: 'The Billed vs local page, without the terminal.', act: 'Paste a key and press Save key. It is never shown again, only its last four characters.'}],
```

- [ ] **Step 6: Verify**

Run: `node --check web/app.js && python3 -m unittest discover tests 2>&1 | tail -1`
Expected: no output from node, `OK`.
Browser (dashboard on demo home, Task 1): sidebar shows **Setup → Settings**; Settings lists both providers; Budgets has no API keys; Billed vs local "Settings" link opens Settings. Review Focus 3: on a home with no key, Billed vs local is hidden; a key saved through a mocked `doAction` (or on a throwaway `CLAUDE_FINOPS_HOME`, with a clearly fake `sk-ant-admin-demo-000000000000` value) makes it appear without reload.

- [ ] **Step 7: Commit** (use "Committing web/app.js" above)

```bash
git -c user.email=mohitrj49@gmail.com commit -m "Settings page for API keys

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Budgets form in numbered blocks, with the Live warnings box

**Files:**
- Modify: `web/app.js` — the `card('Configure budgets, limits and thresholds', ...)` markup and the `#g-install` handler in `VIEWS.budgets`; the results card intro; `TOURS.budgets` card names.
- Modify: `web/styles.css` — append block styles.

**Interfaces:**
- Consumes: `amountField`, `pctField`, `usdChips`, `tokChips`, `SG`, `sg`, `pctile`, `stepVal`, `planHint` (all exist in `VIEWS.budgets`).
- Produces: card title `Set your limits`; blocks `<section class="blk" data-blk="money|tokens|session|advanced">`; each block header has `<button class="blk-help" data-guide="<slide id>">?</button>`; header button `#guide-open`; live box `#livebox`. Later tasks use `data-blk` for "Show me" targets and `data-guide` for opening slides.

- [ ] **Step 1: Replace the card markup.** Keep every field call exactly as it is today; only the wrappers and headings change. Structure:

```js
    ${card('Set your limits', `<div class="cfg">
      <section class="blk" data-blk="money">
        <h4><span class="num">①</span> Money limits <button type="button" class="blk-help" data-guide="money" aria-label="Help: money limits">?</button></h4>
        <p class="blk-intro">Set the most money you want to spend. We warn you before you go over.</p>
        <div class="grid g2">${/* b-monthly */''}${amountField({...monthly as today})}${amountField({...daily as today})}</div>
      </section>
      <section class="blk" data-blk="tokens">
        <h4><span class="num">②</span> Token limits <button type="button" class="blk-help" data-guide="tokens" aria-label="Help: token limits">?</button></h4>
        <p class="blk-intro">Tokens are the small pieces of text Claude reads and writes (about ¾ of a word).
          Set how many you want to use in a month.</p>
        <div class="grid g2">${amountField({...b-tokens as today})}<div></div></div>
      </section>
      <section class="blk" data-blk="session">
        <h4><span class="num">③</span> Session limit <button type="button" class="blk-help" data-guide="session" aria-label="Help: session limit">?</button></h4>
        <p class="blk-intro">A session is one Claude Code conversation. Stop one conversation from getting too big.</p>
        <div class="grid g3">
          <div>${g-tokens amountField}${g-warn pctField with label 'Warn me at'}</div>
          <div>${after-approval fld with heading 'After I say "continue"'}</div>
          <div>${project overrides fld with heading 'Different limit for a project'}</div>
        </div>
        ${liveBox()}
      </section>
      <details class="blk adv" data-blk="advanced">
        <summary><h4>Advanced: plan limits and alert thresholds <span class="blk-sub">(most people can skip this)</span>
          <button type="button" class="blk-help" data-guide="advanced" aria-label="Help: advanced">?</button></h4></summary>
        <div class="grid g2">
          <div><div class="sec">Plan limits</div>${planHint note}${the four l-* amountFields}</div>
          <div><div class="sec">Alert thresholds</div>
            <p class="blk-intro">When a money or token limit reaches these percentages, the dashboard marks it.</p>
            ${t-thr pctField}</div>
        </div>
      </details>
      <div class="save-row">
        <button class="chip on" id="savecfg">Save</button><span id="cfg-msg" role="status"></span>
        <span class="fld-hint">Saved on this computer only. Amounts take shorthand: 20M, 500k, $3,000.</span>
      </div>
    </div>`, {actions: `<button class="act" id="guide-open">${I('help')} How does this work?</button>`,
              footer: `Plan allowances are NOT available from ${agentWord()} data. Anything you enter here is your own declared figure.`})}`;
```

Replace the `${... }` placeholders above with the existing field calls moved verbatim from today's card (they are the lines calling `amountField({id: 'b-monthly', ...})`, `amountField({id: 'b-daily', ...})`, `amountField({id: 'b-tokens', ...})`, `amountField({id: 'g-tokens', ...})`, `pctField({id: 'g-warn', ...})` (change its `label` to `'Warn me at'`), the `After you approve at 100%` `.fld` (change its heading to `After I say "continue"`), the `Project overrides` `.fld` (heading `Different limit for a project`, keep `#g-projects`, `#gtok-list`, `#g-add`), the four `l-*` fields, `pctField({id: 't-thr', ...})`). Delete the old `Hook` `.fld` (replaced by the live box) and the old guard explanation `fld-hint` (moved into the live box). If there is no `help` icon in `ICON`, add `help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6V14"/><path d="M12 17h.01"/>',`.

- [ ] **Step 2: Add `liveBox()`** inside `VIEWS.budgets`, before `page.innerHTML`:

```js
  // Does any per-session budget exist (global or a project override)? The hook needs one.
  const anyBudget = !!sg.session_tokens || Object.values(sg.projects || {}).some(o => o.session_tokens);
  const liveBox = () => {
    const on = !!S.opts.guard_installed;
    const state = !on ? `<span class="lb-state">○ Not installed</span>`
      : anyBudget ? `<span class="lb-state on">● Installed · works in new Claude Code sessions</span>`
      : `<span class="lb-state warn">Installed, but it does nothing until you set a per-session token budget above.</span>`;
    return `<div class="livebox" id="livebox">
      <div class="lb-head"><b>Live warnings in Claude Code</b> <span class="blk-sub">(optional)</span>
        <span class="spacer"></span>${state}
        <button class="act" id="g-install">${on ? 'Uninstall' : 'Install'}</button></div>
      <p>Your limits above already work without this. Install it if you want Claude Code itself to warn
        you at your warn percentages, and ask "continue?" when a conversation reaches its limit.</p>
      <p class="blk-sub">It is a small helper that runs inside Claude Code. Needs: a per-session token budget.
        It cannot end a conversation, and if it ever fails it lets Claude carry on. Undo any time.</p>
      <div class="fld-hint" id="g-msg" role="status"></div></div>`;
  };
```

The existing `#g-install` click handler stays; it already re-renders and writes the message into `#g-msg`.

- [ ] **Step 3: Results card intro.** In the `card('Budget vs actual vs forecast', ...)` body, prepend `<p class="blk-intro" style="margin-top:0">How you're doing against your limits. Set your limits below.</p>`.

- [ ] **Step 4: Styles** (append to `web/styles.css`)

```css
/* ---------- budgets: numbered blocks ---------- */
.cfg .blk { border:1px solid var(--border); border-radius:10px; padding:12px 14px 4px; margin:0 0 12px; }
.cfg .blk h4 { display:flex; align-items:center; gap:8px; margin:0 0 2px; font-size:14px; }
.cfg .blk .num { color:var(--s1); font-size:16px; }
.cfg .blk-intro { margin:0 0 8px; color:var(--text); font-size:12.5px; }
.cfg .blk-sub { color:var(--muted); font-weight:450; font-size:12px; }
.cfg .blk-help { width:20px; height:20px; border-radius:50%; border:1px solid var(--border);
  background:var(--surface-2); color:var(--muted); font-size:11px; font-weight:700; cursor:pointer; line-height:1; }
.cfg .blk-help:hover, .cfg .blk-help:focus-visible { color:var(--text); border-color:var(--border-strong); }
.cfg details.adv > summary { cursor:pointer; list-style:none; padding-bottom:8px; }
.cfg details.adv > summary::-webkit-details-marker { display:none; }
.cfg details.adv > summary h4::before { content:'▸'; color:var(--muted); }
.cfg details.adv[open] > summary h4::before { content:'▾'; }
.cfg .livebox { border:1px dashed var(--border-strong); border-radius:8px; padding:10px 12px; margin:4px 0 10px;
  background:var(--surface-2); }
.cfg .livebox p { margin:6px 0 0; font-size:12.5px; }
.cfg .lb-head { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.cfg .lb-state { font-size:12px; color:var(--muted); }
.cfg .lb-state.on { color:var(--good-ink); }
.cfg .lb-state.warn { color:var(--warning-ink); font-weight:550; }
```

- [ ] **Step 5: Tour names.** In `TOURS.budgets` change `'card:Configure budgets'` to `'card:Set your limits'` (three steps) and update the Session guard step's `see` to mention "the Live warnings box (optional)".

- [ ] **Step 6: Verify** (demo home)

Run: `node --check web/app.js`
Browser: blocks ①②③ with intros; Advanced closed, opens on click; chips/hints/errors/Save still work (type `abc` in Monthly → error; Save shows "Fix 1 field"); Live warnings box states — set `guard.session_tokens` null and `projects` `{}` then install on a throwaway home to see the amber state; Review Focus 5: with only a project override (`{"/work/shop-app": {"session_tokens": 15000000}}`) and the guard installed, the box shows "● Installed", not amber. Narrow window (≤ 700 px): grids stack.

- [ ] **Step 7: Commit** (see "Committing web/app.js")

```bash
git add web/styles.css
git -c user.email=mohitrj49@gmail.com commit -m "Budgets form in numbered blocks, with an optional Live warnings box

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Guide engine

**Files:**
- Modify: `web/app.js` — add the guide section right after the budgets form helpers; call `maybeGuide('budgets')` at the end of `VIEWS.budgets`; wire `#guide-open` and `[data-guide]`.
- Modify: `web/styles.css` — dialog, picture box, marks, pulse.

**Interfaces:**
- Produces:
  - `const GUIDES = {}` — `GUIDES[name] = [slide, ...]`; slide `{id: string, title: string, text: string[], img?: string, live?: (host: HTMLElement) => void, marks?: {n: number, x: number, y: number}[], target?: string}`.
  - `openGuide(name: string, slideId?: string, opener?: HTMLElement): void`
  - `closeGuide(): void`
  - `maybeGuide(name: string): void` — first visit only.
  - `showMe(selector: string): void` — scroll + 2 s pulse.

- [ ] **Step 1: Write the engine**

```js
/* ---------- guides: picture slides that explain a page ---------- */
const GUIDES = {};
let GUIDE = null;          // {name, i, opener, el}
const guideSeenKey = name => `finops.guide.${name}.seen`;

function openGuide(name, slideId, opener) {
  const slides = GUIDES[name];
  if (!slides || !slides.length) return;
  closeGuide();
  const el = document.createElement('div');
  el.className = 'guide-layer';
  el.innerHTML = `<div class="guide" role="dialog" aria-modal="true" aria-labelledby="guide-title" tabindex="-1"></div>`;
  document.body.appendChild(el);
  el.addEventListener('click', e => { if (e.target === el) closeGuide(); });
  GUIDE = {name, i: Math.max(0, slides.findIndex(s => s.id === slideId)), opener: opener || document.activeElement, el};
  document.addEventListener('keydown', guideKeys, true);
  try { localStorage.setItem(guideSeenKey(name), '1'); } catch (_) {}
  drawGuide();
}
function closeGuide() {
  if (!GUIDE) return;
  const {el, opener} = GUIDE;
  GUIDE = null;
  document.removeEventListener('keydown', guideKeys, true);
  el.remove();
  if (opener && opener.isConnected && opener.focus) opener.focus();
}
function guideKeys(e) {
  if (!GUIDE) return;
  const slides = GUIDES[GUIDE.name];
  if (e.key === 'Escape') { e.preventDefault(); closeGuide(); }
  else if (e.key === 'ArrowRight' && GUIDE.i < slides.length - 1) { e.preventDefault(); GUIDE.i++; drawGuide(); }
  else if (e.key === 'ArrowLeft' && GUIDE.i > 0) { e.preventDefault(); GUIDE.i--; drawGuide(); }
  else if (e.key === 'Tab') {                       // keep focus inside the dialog
    const f = [...GUIDE.el.querySelectorAll('button:not([disabled]), a[href]')];
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
}
function drawGuide() {
  const slides = GUIDES[GUIDE.name], s = slides[GUIDE.i], n = slides.length, last = GUIDE.i === n - 1;
  const box = GUIDE.el.querySelector('.guide');
  box.innerHTML = `
    <header><span class="g-step">${GUIDE.i + 1} of ${n}</span>
      <h2 id="guide-title">${esc(s.title)}</h2>
      <button class="act g-x" data-g="close" aria-label="Close guide">${I('x')}</button></header>
    <div class="g-pic">${s.img ? `<img src="${esc(s.img)}" alt="">` : '<div class="g-live"></div>'}
      ${(s.marks || []).map(m => `<span class="g-mark" style="left:${m.x}%;top:${m.y}%">${m.n}</span>`).join('')}</div>
    <div class="g-text">${s.text.map(t => `<p>${t}</p>`).join('')}</div>
    <footer>
      <div class="g-dots" aria-hidden="true">${slides.map((_, i) => `<i class="${i === GUIDE.i ? 'on' : ''}"></i>`).join('')}</div>
      <span class="spacer"></span>
      ${s.target ? '<button class="act" data-g="show">Show me on the page</button>' : ''}
      <button class="act" data-g="back" ${GUIDE.i ? '' : 'disabled'}>Back</button>
      <button class="chip on" data-g="${last ? 'close' : 'next'}">${last ? 'Done' : 'Next'}</button>
    </footer>`;
  if (s.live) s.live(box.querySelector('.g-live'));
  box.querySelectorAll('[data-g]').forEach(b => b.onclick = () => {
    const a = b.dataset.g;
    if (a === 'close') closeGuide();
    else if (a === 'next') { GUIDE.i++; drawGuide(); }
    else if (a === 'back') { GUIDE.i--; drawGuide(); }
    else if (a === 'show') { const t = s.target; closeGuide(); showMe(t); }
  });
  (box.querySelector('[data-g="next"], [data-g="close"]:not(.g-x)') || box).focus();
}
function showMe(selector) {
  const t = $(selector);
  if (!t) return;
  if (t.tagName === 'DETAILS') t.open = true;
  t.scrollIntoView({block: 'center', behavior: 'smooth'});
  t.classList.remove('pulse'); void t.offsetWidth; t.classList.add('pulse');
  setTimeout(() => t.classList.remove('pulse'), 2200);
}
// First visit to a page with a guide: open it once. Never on top of the welcome tour.
function maybeGuide(name) {
  let seen = true;
  try { seen = localStorage.getItem(guideSeenKey(name)) === '1'; } catch (_) {}
  if (seen || !GUIDES[name]) return;
  const tryOpen = () => {
    if (S.view !== name || GUIDE) return;
    if (TOUR) return setTimeout(tryOpen, 800);     // wait for the welcome tour to finish
    openGuide(name);
  };
  setTimeout(tryOpen, 400);
}
```

- [ ] **Step 2: Wire the Budgets buttons** (end of `VIEWS.budgets`, after the other handlers)

```js
  $('#guide-open', page).onclick = e => openGuide('budgets', null, e.currentTarget);
  page.querySelectorAll('[data-guide]').forEach(b => b.onclick = e => {
    e.preventDefault(); e.stopPropagation();          // inside <summary>: don't toggle Advanced
    openGuide('budgets', b.dataset.guide, b);
  });
  maybeGuide('budgets');
```

- [ ] **Step 3: Styles** (append)

```css
/* ---------- guide popup ---------- */
.guide-layer { position:fixed; inset:0; z-index:200; background:rgba(0,0,0,.45);
  display:flex; align-items:center; justify-content:center; padding:16px; }
.guide { width:min(760px, 100%); max-height:calc(100vh - 32px); overflow:auto; background:var(--surface);
  border:1px solid var(--border); border-radius:14px; box-shadow:var(--shadow); outline:none; }
.guide header { display:flex; align-items:center; gap:10px; padding:14px 16px 6px; }
.guide h2 { margin:0; font-size:18px; }
.guide .g-step { font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:.07em; }
.guide .g-x { margin-left:auto; }
.guide .g-pic { position:relative; margin:6px 16px; border:1px solid var(--border); border-radius:10px;
  overflow:hidden; background:var(--bg, var(--surface-2)); }
.guide .g-pic img { display:block; width:100%; height:auto; }
.guide .g-live { padding:12px; pointer-events:none; user-select:none; }
.guide .g-mark { position:absolute; transform:translate(-50%,-50%); width:26px; height:26px; border-radius:50%;
  background:var(--s1); color:#fff; font-weight:700; font-size:14px; display:flex; align-items:center;
  justify-content:center; box-shadow:0 0 0 3px var(--surface); }
.guide .g-text { padding:4px 18px 2px; font-size:15px; line-height:1.5; }
.guide .g-text p { margin:6px 0; }
.guide footer { display:flex; align-items:center; gap:8px; padding:10px 16px 14px; flex-wrap:wrap; }
.guide .g-dots { display:flex; gap:5px; }
.guide .g-dots i { width:7px; height:7px; border-radius:50%; background:var(--border-strong); }
.guide .g-dots i.on { background:var(--s1); }
.pulse { animation: guide-pulse 1s ease-in-out 2; border-radius:10px; }
@keyframes guide-pulse { 50% { box-shadow:0 0 0 4px color-mix(in srgb, var(--s1) 55%, transparent); } }
@media (max-width: 600px) { .guide .g-text { font-size:14px; } .guide footer .spacer { display:none; } }
```

- [ ] **Step 4: Verify** with a temporary one-slide guide in the console: `GUIDES.test = [{id:'a', title:'Hi', text:['One.'], target:'.cfg'}]; openGuide('test')`.
  Check: Tab cycles only inside the dialog; Esc closes; focus returns to the element that had focus; clicking the dark backdrop closes; "Show me" scrolls to `.cfg` and pulses it. **first-visit** (Review Focus 1): clear `localStorage`, reload on Budgets: the welcome tour shows first, the guide opens only after the tour is closed, and never again after that.

- [ ] **Step 5: Commit** (see "Committing web/app.js")

```bash
git add web/styles.css
git -c user.email=mohitrj49@gmail.com commit -m "Guide popup engine: picture slides with Show me on the page

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Budgets slides with live copies

**Files:**
- Modify: `web/app.js` — `GUIDES.budgets` and `guideCopy()` helper, placed after the guide engine.

**Interfaces:**
- Consumes: `amountField`, `pctField`, `tokChips`, `usdChips`, `GUIDES`, block selectors from Task 3 (`[data-blk="money"]` …, `#livebox`).
- Produces: `GUIDES.budgets` with slide ids `overview, money, tokens, session, after, live, advanced, save` (the `data-guide` values in Task 3 must match: `money, tokens, session, advanced`).

- [ ] **Step 1: Copy helper** — builds disabled, id-prefixed copies so the real form never reads them:

```js
// A frozen copy of a form block for a guide picture: demo values, no ids that clash.
function guideCopy(host, html) {
  host.innerHTML = `<div class="cfg">${html}</div>`;
  host.querySelectorAll('[id]').forEach(el => el.id = 'gcopy-' + el.id);
  host.querySelectorAll('[for]').forEach(el => el.setAttribute('for', 'gcopy-' + el.getAttribute('for')));
  host.querySelectorAll('input, select, button, textarea').forEach(el => { el.disabled = true; el.tabIndex = -1; });
}
const DEMO = {spend: 312, day: 14, busy: 29, tokens: 7.4e8, typical: 8.5e6, large: 3.3e7};
```

- [ ] **Step 2: The slides**

```js
GUIDES.budgets = [
  {id: 'overview', title: 'What is this page?', img: 'guide/budgets.png',
   marks: [{n: 1, x: 12, y: 18}, {n: 2, x: 12, y: 58}, {n: 3, x: 12, y: 82}],
   text: ['This page helps you stop spending too much on AI.',
          '<b>①</b> The top shows how you are doing. <b>②</b> Below it you set your limits. <b>③</b> Session limits stop one conversation from getting too big.',
          'You only need to fill in what you care about. Everything else can stay empty.'],
   target: '[data-blk="money"]'},
  {id: 'money', title: '① Money limits', target: '[data-blk="money"]',
   live: h => guideCopy(h, `<div class="grid g2">
     ${amountField({id: 'b-monthly', label: 'Monthly budget (USD)', kind: 'usd', value: 400,
        chips: usdChips([[DEMO.spend, 'Last 30 days'], 250, 500])})}
     ${amountField({id: 'b-daily', label: 'Daily budget (USD)', kind: 'usd', value: null,
        chips: usdChips([[DEMO.day, 'Average day'], [DEMO.busy, 'Busy day']])})}</div>`),
   text: ['Type the most money you want to spend in a month, or in a day.',
          'Not sure? Tap a suggestion. They come from your own recent spending.',
          'We warn you as you get close. Leave a box empty if you don\'t need it.']},
  {id: 'tokens', title: '② Token limits', target: '[data-blk="tokens"]',
   live: h => guideCopy(h, amountField({id: 'b-tokens', label: 'Monthly token budget', kind: 'tokens',
     value: 9e8, chips: tokChips([[DEMO.tokens, 'Last 30 days'], 1e9, 5e9])})),
   text: ['A token is a small piece of text, about ¾ of a word. Claude counts all its work in tokens.',
          'Set how many tokens you want to use in a month. You can type short numbers like <b>900M</b> or <b>2B</b>.']},
  {id: 'session', title: '③ Session limit', target: '[data-blk="session"]',
   live: h => guideCopy(h, `<div class="grid g2">
     ${amountField({id: 'g-tokens', label: 'Per-session token budget', kind: 'tokens', value: 8e6,
        chips: tokChips([[DEMO.typical, 'Typical'], [DEMO.large, 'Large'], 5e6, 10e6])})}
     ${pctField({id: 'g-warn', label: 'Warn me at', values: [75, 80], presets: [50, 60, 70, 75, 80, 90], max: 99})}</div>`),
   text: ['A session is one Claude Code conversation.',
          'Long conversations cost more, because Claude re-reads everything each time.',
          'Set a limit for one conversation. Then pick when you want a warning, like at 75% and 80%.']},
  {id: 'after', title: 'After you say "continue", and project limits', target: '#g-projects',
   live: h => guideCopy(h, `<div class="grid g2">
     <div class="fld"><div class="hd">After I say "continue"</div>
       <label style="display:flex;gap:6px;margin:6px 0"><input type="radio" checked> Ask again every +25%</label>
       <label style="display:flex;gap:6px;margin:6px 0"><input type="radio"> Once per session</label></div>
     <div class="fld"><div class="hd">Different limit for a project</div>
       <div style="display:flex;gap:6px;align-items:center"><select><option>shop-app</option></select>
         <input type="text" value="15M" style="width:90px"><label><input type="checkbox"> off</label></div></div></div>`),
   text: ['When a conversation reaches its limit, you can let it keep going.',
          'Choose if we ask you again a bit later, or never again for that conversation.',
          'A big project can have its own, bigger limit. Or you can turn the limit off for it.']},
  {id: 'live', title: 'Live warnings (optional)', target: '#livebox',
   live: h => { h.innerHTML = `<div class="cfg"><div class="livebox"><div class="lb-head"><b>Live warnings in Claude Code</b>
       <span class="blk-sub">(optional)</span><span class="spacer"></span><span class="lb-state">○ Not installed</span>
       <button class="act" disabled>Install</button></div></div>
       <div class="g-cc">Session guard: this session has used 8.4M tokens, 105% of its 8M budget.
         Allow this tool call? <b>❯ Yes</b> &nbsp; No</div></div>`; },
   text: ['<b>You don\'t need this for your limits to work.</b>',
          'Install it if you want Claude Code itself to warn you while you work, and to ask "continue?" when a conversation reaches its limit. The dark box shows what that looks like.',
          'You can uninstall it any time.']},
  {id: 'advanced', title: 'Advanced (you can skip this)', target: '[data-blk="advanced"]',
   live: h => guideCopy(h, `<div class="grid g2">
     ${amountField({id: 'l-cost', label: 'Monthly cost allowance (USD)', kind: 'usd', zero: 1, value: null})}
     ${pctField({id: 't-thr', label: 'Warn when a budget reaches', values: [50, 75, 90, 100], presets: [25, 50, 75, 90, 100, 110], max: 1000})}</div>`),
   text: ['<b>Plan limits:</b> only fill these in if you know your plan\'s real numbers.',
          '<b>Alert thresholds:</b> when a money or token limit reaches these percentages, it gets marked.',
          'Most people never need to change these.']},
  {id: 'save', title: 'Save, then check your results', target: '#savecfg',
   live: h => { h.innerHTML = `<div class="cfg"><div class="save-row"><button class="chip on" disabled>Save</button>
       <span class="ok">Saved.</span></div>
       <div class="meter high" style="margin:8px 0"><i style="width:82%"></i></div>
       <div class="fld-hint">Monthly spend · 82% used</div>
       <div style="margin-top:8px">${dot('yellow')} a conversation at 75% of its limit &nbsp; ${dot('red')} over its limit</div></div>`; },
   text: ['Press <b>Save</b>. Your limits are kept on this computer.',
          'The top of this page then shows how you are doing against each limit.',
          'On the <b>Sessions</b> page, amber and red dots show conversations near or over their limit.']},
];
```

Add to styles: `.guide .g-cc { margin-top:10px; padding:10px 12px; border-radius:8px; background:#1e1e1e; color:#e6e6e6; font:12.5px/1.5 ui-monospace, Menlo, monospace; }`

- [ ] **Step 3: Verify** (demo home). Open each slide from its **?** and from Next. Review Focus 4 — in the console with slide `money` open:

```js
document.querySelectorAll('#b-monthly').length === 1   // only the real field has this id
document.querySelector('.guide #gcopy-b-monthly').disabled === true
```

Both must be `true`. Also: pressing the real **Save** while the guide was opened and closed still saves the real values (type `500` in Monthly, Save, see "Saved.", `settings.local.json` on the demo home has `500`).

- [ ] **Step 4: Commit** (see "Committing web/app.js")

```bash
git add web/styles.css
git -c user.email=mohitrj49@gmail.com commit -m "Budgets guide: eight picture slides in plain language

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The screenshot, README, and full check

**Files:**
- Create: `web/guide/budgets.png`
- Modify: `README.md`; possibly `GUIDES.budgets[0].marks` in `web/app.js`.

- [ ] **Step 1: Build and run the demo home**

```bash
python3 tools/demo_data.py "$SP/finops-demo"
CLAUDE_FINOPS_HOME="$SP/finops-demo" CLAUDE_PROJECTS="$SP/finops-demo/projects" PORT=8790 CLAUDE_FINOPS_NO_UPDATE_CHECK=1 python3 run.py --foreground &
```

- [ ] **Step 2: Capture** in Chrome at 1280 px wide, following `tools/README.md`. Save to `web/guide/budgets.png`; check `ls -l web/guide/budgets.png` is under 250 KB (re-save as a smaller width, e.g. 1100 px, if not). Open the overview slide and move the three `marks` so each circle sits on its area; commit the new positions.

- [ ] **Step 3: README.** In the Configuration section, replace the paragraph starting "The **Budgets** view edits…" with:

```markdown
The **Budgets** page has three numbered blocks (① money, ② tokens, ③ one conversation) and an
Advanced section, folded away, for plan limits and alert thresholds. **How does this work?** (and
the small **?** on each block) opens a short picture guide; it also opens by itself the first
time. Values are saved to `~/.claude-finops/settings.local.json`. Each amount suggests values
from your own last 30 days, accepts shorthand (`20M`, `500k`, `$3,000`), and explains a bad
value before saving.

**Live warnings** (inside block ③) is the optional session guard hook. Your budgets work
without it; it only adds the warning and "continue?" prompt inside Claude Code.

**Settings** (sidebar, Setup) stores the optional API keys used by **Billed vs local**, in
`~/.claude-finops/secrets.local.json` (0600, the same file `--set-key` writes). The page only
ever gets back whether a key is set, where from, and its last four characters. An environment
variable (`ANTHROPIC_ADMIN_KEY`, `CURSOR_API_KEY`) takes priority over a stored key.
```

and in the "Actions" list change "**Install guard** (Budgets, or `--install-guard`)" to "**Install** under Live warnings (Budgets, block ③, or `--install-guard`)".

- [ ] **Step 4: Full check**

Run: `python3 -m unittest discover tests 2>&1 | tail -1 && node --check web/app.js`
Expected: `OK`.
Browser, demo home then real data (`./run.sh --stop && ./run.sh --detach`): every item in the spec's Testing list, in light and dark theme and at a 390 px wide window (guide fits, no horizontal scroll, buttons wrap).

- [ ] **Step 5: Commit** (see "Committing web/app.js" if `app.js` changed)

```bash
git add web/guide/budgets.png README.md
git -c user.email=mohitrj49@gmail.com commit -m "Guide screenshot from demo data, and docs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
