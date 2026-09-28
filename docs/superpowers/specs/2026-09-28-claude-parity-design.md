# Claude parity features — design

Date: 2026-09-28
Status: approved in chat, pending spec review

## Goal

Close the Claude-only gaps found when comparing claude-finops with usages.pro
(AI Usage Tracker), and add one Claude data source neither tool reads today.

Scope is Claude only. Out of scope: a signed/notarised macOS app, non-Claude tools
(Windsurf, Cline, Roo Code, Aider, Continue.dev, OpenClaw), and claude.ai web/desktop
chats (stored server-side, no local token data).

## Features

### 1. Peak-hours heatmap

- **Backend:** `Analytics.heatmap(f)` in `finops/analytics.py`. Returns
  `{"cells": [{"dow": 0-6, "hour": 0-23, "cost": float, "tokens": int, "requests": int}, ...],
  "tz": "<local tz name>", "cost_basis": "estimated"}` with all 168 cells present
  (zeros filled). `dow` 0 = Monday.
- Honours the global filter via `self.where(f)`.
- **Local time:** the warehouse stores UTC `ts`. Bucket with
  `strftime('%w', r.ts, 'localtime')` and `strftime('%H', r.ts, 'localtime')`
  (SQLite handles half-hour offsets such as +05:30). Convert SQLite's `%w`
  (0 = Sunday) to Monday-first. The existing UTC `requests.hour` column is not used.
  Date-range filtering stays on UTC `r.day`, like every other view.
- **API:** `GET /api/heatmap` → `a.heatmap(f)`.
- **Frontend:** `heatmap(host, {cells, metric})` in `web/charts.js`: SVG grid, 7 rows
  (Mon–Sun) × 24 columns, single-hue sequential scale, hover tooltip with day, hour,
  cost, tokens, requests. New card "Peak hours" on the Usage timeline page,
  below the usage-over-time card, following the page's existing Cost/Tokens metric
  toggle. Badge: Estimated when showing cost, Actual when showing tokens.

### 2. "Vs yesterday" on the overview

- `overview()` adds `cost_yesterday = spend("r.day = ?", [today - 1 day])`, same shape
  as `cost_today`.
- Overview spend card detail becomes
  `$X today (▲12% vs yesterday) · $Y last 7d`. The delta is omitted when yesterday's
  cost is 0. ▲ for up, ▼ for down.

### 3. `claude --resume` in the session detail drawer

- `session_detail(sid)` adds `resume`: `"claude --resume <sid>"` when
  `agent == 'claude'` and `source_file` is not a Cowork transcript (path does not
  contain `local-agent-mode-sessions`); otherwise `None` (Cowork sessions live under
  a different config dir, so resume would not find them; other agents are not in scope).
- The Session detail drawer shows the command with a Copy button, reusing the
  Live page's copy pattern.

### 4. Claude desktop app Cowork sessions

- New `DESKTOP_SOURCES` in `finops/paths.py`: the Claude desktop app data dir per OS
  (`~/Library/Application Support/Claude` on macOS, `%APPDATA%\Claude` on Windows,
  `~/.config/Claude` on Linux) joined with `local-agent-mode-sessions`.
- `Loader.build` also walks those roots and loads only `*.jsonl` files whose path
  contains `/.claude/projects/` (skips `audit.jsonl` and anything else). Same
  `load_file` parser; rows keep `agent = 'claude'`.
- Projects from these files are named `Cowork · <basename of cwd>` (fallback:
  `Cowork · session`), so the long slug never shows.
- `Loader(desktop_roots=None)`: `None` means "scan `DESKTOP_SESSIONS` only when
  `source` is the default `~/.claude/projects`". An explicit source (tests, a custom
  path) loads exactly that directory unless roots are passed in. A new `meta` row `desktop_transcript_files` records the count.
- The Agents page Claude note mentions that Claude desktop app (Cowork) sessions are
  included when present.
- No schema change, no `SCHEMA_VERSION` bump.

### 5. Plan-limit history

- New module `finops/plan_history.py` with `history()`:
  - Reads `<desktop data dir>/plan-usage-history.json` on request (not loaded into
    the warehouse). Cached in memory keyed on file mtime.
  - Expected shape: `{"version": 2, "samples": [{"t": ms, "org": str, "u": {"fh": pct, "sd": pct}}]}`.
    `fh` = 5-hour window %, `sd` = weekly (seven-day) window %.
  - If samples span several `org` values, keep the org with the most recent sample.
  - Returns `{"ok": true, "source": path, "org_count": n, "series": [{"t": iso, "five_hour": pct, "weekly": pct}],
    "summary": {"five_hour_peak", "weekly_peak", "five_hour_ge90", "five_hour_hit100",
    "weekly_ge90", "weekly_hit100", "first", "last"}}`.
    Counts are the number of distinct excursions (runs of consecutive samples at or
    above the threshold), not raw sample counts.
  - Missing file, unreadable JSON, unknown `version`, or no samples →
    `{"ok": false, "reason": "..."}`. Never raises.
- **API:** `GET /api/plan_history` → `plan_history.history()`.
- **UI:** card "Plan limits over time" on the Burn rate & limits page: two-line
  `timeSeries` (5-hour %, weekly %) with the y-axis fixed to at least 100% (new
  optional `max` on `timeSeries`), plus three stat tiles
  (5-hour peak, times at 90%+, weekly peak). Badge: Actual. Footnote: source is the
  Claude desktop app's local file, whose format is undocumented. When `ok` is false,
  the card shows the reason and the rest of the page is unaffected.

### 6. Older Claude models in the price table

- Add Claude 4.x models (Opus 4.x, Sonnet 4.x including `claude-sonnet-4-6`) and any
  still-listed 3.x models to `config/pricing.json`, same fields as existing entries.
- Rates only from Anthropic's published pricing page, fetched at build time; the
  file's `source` and `updated` fields are updated. A model with no published rate is
  not added and stays unpriced.

## Testing

Existing `unittest` style in `tests/`:

- `test_heatmap.py`: fixture warehouse; 168 cells; Monday-first mapping; local-time
  conversion (run with `TZ=Asia/Kolkata` via `time.tzset()` and check a UTC 23:00
  request lands at 04:00 next day).
- Overview: `cost_yesterday` with the existing `_today` hook.
- Session detail: `resume` present for a `~/.claude/projects` session, `None` for a
  Cowork one.
- ETL: a temp dir mimicking `local-agent-mode-sessions/.../.claude/projects/<slug>/x.jsonl`
  plus an `audit.jsonl`; the transcript loads with a `Cowork · ` project name, audit is
  skipped.
- `test_plan_history.py`: valid file, missing file, malformed JSON, unknown version,
  two orgs (latest wins), excursion counting.
- `test_api.py`: one test each for `/api/heatmap` and `/api/plan_history`.
- Full suite: `python3 -m unittest discover tests`, then a manual check in the
  running dashboard.
