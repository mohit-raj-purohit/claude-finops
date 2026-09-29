# Session token guard — design

Date: 2026-09-29
Status: approved in chat, pending spec review

## Goal

Let a user cap how many tokens a single Claude Code session may use, and keep a human
in the loop when a session runs past it:

1. A **per-session token budget** shown on the Budgets page and flagged in the
   Sessions table (reporting, after the fact).
2. An opt-in **session guard**: a Claude Code `PreToolUse` hook that warns at
   configurable percentages and, at 100% of the budget, pauses the next tool call for
   the user's approval (live, while the session runs).

Out of scope: terminating a running session (no Claude Code hook can do that),
blocking prompts via `UserPromptSubmit`, cost-based (USD) session caps, and changes to
the existing monthly/daily budgets, plan limits, waste rules or alert thresholds.

## Decisions

| Question | Decision |
|---|---|
| Token basis | **Billable tokens** = input + output + cache read + cache write, identical to `sessions.billable_tokens` and the Sessions table "Tokens" column. Cache reads dominate long sessions, which is exactly the signal a session cap is for. |
| At the budget | **Ask** (`permissionDecision: "ask"`) on the next tool call. |
| Before the budget | **Warn** at user-configured percentages (default `[75, 80]`), never block. |
| After approval | User-selectable: **step** (ask again every +N%, default 25) or **once** (no further asks this session). |
| Setup surface | Both dashboard (Budgets page) and CLI, like the statusline. |
| Scope | One global budget **plus per-project overrides** (a different budget, or off). |
| Counting approach | The hook reads the session transcript itself, incrementally. Warehouse (stale until Sync) and statusline payload (context % only) rejected. |

## Hook facts this relies on (Claude Code docs)

- `PreToolUse` stdin carries `session_id`, `transcript_path`, `cwd`, `permission_mode`,
  `tool_name`, `hook_event_name`.
- Output `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision":
  "ask", "permissionDecisionReason": "..."}}` prompts the user; the reason is shown to
  the user, not Claude.
- `systemMessage` shows the user a warning while allowing;
  `hookSpecificOutput.additionalContext` adds a note for Claude.
- A hook that errors, exits non-zero (other than 2) or times out does not block: the
  action proceeds (fail open).
- In auto mode, `ask` forces a prompt. Behaviour in `bypassPermissions`,
  `acceptEdits`, and for tools on the allow list is **not documented** and is verified
  on a real session during implementation (see Verification); the result is recorded
  in README and the site docs.

## Settings

New top-level `guard` block in `config/settings.json` (shipped defaults); user values
are written to `settings.local.json` by the existing `POST /api/settings`.

```json
"guard": {
  "_comment": "Per-session token budget and the opt-in session guard hook. session_tokens null => no per-session budget; the guard allows everything.",
  "session_tokens": null,
  "warn_pct": [75, 80],
  "after_approval": "step",
  "step_pct": 25,
  "projects": {}
}
```

- `session_tokens`: number or null. Positive integer tokens.
- `warn_pct`: list of numbers, each `0 < x < 100`, deduplicated and sorted on load.
- `after_approval`: `"step"` or `"once"`.
- `step_pct`: number `1..1000`.
- `projects`: `{ "<absolute project path>": {"session_tokens": number} | {"off": true} }`.

Validation:
- `finops/analytics.py` `_validate_settings`: coerce `guard` like budgets — invalid
  leaves fall back to the shipped default with a stderr warning; an invalid project
  entry is dropped.
- `finops/api.py`: add `guard: dict` to `SETTINGS_SHAPE`, a `_validate_guard(payload)`
  that raises `BadRequest` on bad values, and add `"guard"` to the keys merged into
  `settings.local.json`. `projects` is replaced wholesale on save (the UI always sends
  the full map).

The one number `guard.session_tokens` drives the Budgets line, the Sessions table
flag and the hook.

### Resolving a session's budget

`budget_for(settings, cwd)`: among `guard.projects` keys, pick the **longest** path
that equals `cwd` or is a parent of it (compare with `os.path.realpath`, separator
aware so `/a/app` does not match `/a/app2`). `{"off": true}` → no budget;
`{"session_tokens": n}` → n; no match → global `session_tokens`. In the hook, `cwd`
comes from stdin. In the dashboard (Sessions flag), `projects.path` is used.

## Part 1: reporting

### Budgets line

`Analytics.budgets(f)` appends a `"Per-session tokens"` line when (and only when)
`guard.session_tokens` is set or any project override has a budget:

- Scope: sessions with any request in the current billing period, under the current
  filter (`self.where(f)`).
- Each session is compared with its own resolved budget (override or global);
  sessions whose project is `off` are skipped.
- `actual` = the largest `billable_tokens` among those sessions; `budget` = that
  session's budget; built with the existing `line(..., unit="tokens")` helper so
  thresholds, status and meter work unchanged. `forecast` is None.
- Extra fields: `sessions_over` (count with tokens ≥ budget),
  `top_over` (up to 5: `session_id`, `title`, `project`, `tokens`, `budget`, `pct`).
- When unset, the line is rendered as "not configured" like the others.

UI (`VIEWS.budgets`): the existing line renderer shows it. Below its meter, a note
"N sessions over budget this period" linking to Sessions sorted by tokens, and a
tooltip: "Most of a long session's tokens are cache reads, priced at about a tenth of
the input rate."

### Sessions table flag

`GET /api/sessions` rows gain `budget_tokens` (resolved per row, null when none).
In `VIEWS.sessions`, the title-cell dot:

- when the row has `budget_tokens`: red at ≥100%, amber at ≥75%, none below; the dot's
  title is "X% of your N-token session budget";
- otherwise: unchanged (red when above 3× the loaded average).

The note above the table reads "rows over your session budget are flagged" when a
budget is set, otherwise unchanged.

## Part 2: the session guard hook

### Module layout

- `finops/guard.py` (new): counting, decision, state. Imports only the stdlib,
  `finops.paths` and `load_settings` — never the ETL or analytics query code — so the
  hook starts fast.
- `finops/integrate.py`: `guard_state()`, `install_guard(remove=False)`.
- `run.py`: `--guard` (invoked by Claude Code), `--install-guard`,
  `--uninstall-guard`; help text updated.
- `finops/api.py`: `POST /api/do/guard` with `{"remove": bool}`; `guard_state` exposed
  in the options payload the UI already loads.

The retired `--hook` / `--install-hook` stay no-ops. They are not reused, so old
installs never start enforcing without the user opting in.

### Counting (`count_session`)

Mirrors `finops/etl.py`:
- Files: `transcript_path`, plus `<dir>/<session_id>/subagents/*.jsonl` if present.
- Each line is JSON; a line with `message.usage` and a `requestId` sets
  `requests[requestId] = billable(usage)` (last line for an id wins, which matches
  `insert_request` using the group's final line). Lines without `requestId` are
  keyed by `message.id`, then by `uuid`.
- `billable(u)` = `input_tokens + output_tokens + cache_read_input_tokens + cw`, where
  `cw = cache_creation_input_tokens or (ephemeral_5m + ephemeral_1h)`.
- Incremental: per file, state stores the byte `offset` of the last complete line
  read. A partial trailing line is left for next time. If the file is smaller than the
  offset (rewritten), recount from 0.
- Total = sum of `requests` across all files.

A resumed session's transcript contains the earlier history, so the guard counts it
toward the resumed session; the dashboard attributes it to the original session. This
is accepted: for a budget it is the same conversation continuing.

### State

`<DATA_DIR>/guard/<session_id>.json`:
```json
{"files": {"<path>": {"offset": 0, "requests": {"<id>": 123}}},
 "warned": [75], "asked_at": null, "approved_pct": null, "updated": "<iso ts>"}
```
Written atomically (temp file + `os.replace`). On each run, state files not modified
in 30 days are deleted (best effort, at most once per hour, tracked by a marker file).

### Decision (`decide(total, budget, cfg, state)`)

`pct = 100 * total / budget`.

1. No budget (unset, or project `off`) → allow, no output.
2. If `asked_at` is set: the user must have approved (a declined ask stops Claude, so
   no further tool call arrives until they continue). Set `approved_pct = asked_at`,
   clear `asked_at`.
3. Next ask level: `100` if `approved_pct` is null; else if `after_approval == "once"`
   → none; else `approved_pct + step_pct` (100 → 125 → 150 with the default step).
4. If a next ask level exists and `pct >= level` → set `asked_at = level`, return
   **ask** with reason
   `"Session guard: this session has used 2.1M tokens, 105% of its 2M budget. Allow this tool call? (Approving continues until 125%.)"`
   (the trailing hint reflects step/once).
5. Else, if any `w` in `warn_pct` has `pct >= w` and is not in `warned` → add all such
   `w` to `warned` (one message even if several were crossed at once), return
   **allow** with
   `systemMessage` `"Session guard: 1.6M tokens, 80% of this session's 2M budget."` and
   `additionalContext` telling Claude the session is near its token budget and to
   suggest `/compact` or a fresh session at a natural break.
6. Else allow, no output.

"Allow" means exit 0 with no `permissionDecision`, so Claude Code's normal permission
flow still applies.

### Fail-open

`--guard` wraps everything: any exception, unreadable stdin, missing transcript or
settings → exit 0 with no output. A read of more than 64 MB of new data in one run
stops early and allows (state saved so the next run continues).

### Install / uninstall

`install_guard()` in `integrate.py`, using the existing `_load_settings` /
`_save_settings` (backup to `settings.json.finops-backup`):

- Adds to `hooks.PreToolUse` one matcher group
  `{"matcher": "", "hooks": [{"type": "command", "command": "<_command()> --guard", "timeout": 10}]}`.
- Idempotent: if an entry whose command contains `--guard` and `finops` exists, it is
  left as is (reported as already installed).
- Other hooks and other `PreToolUse` groups are never modified.
- `remove=True` deletes only our hook entry (and its group if it becomes empty, and
  `hooks.PreToolUse` / `hooks` if they become empty).
- `guard_state()` → `"installed"` or `None`.
- Returns `{"ok", "message"}`; the install message warns when no session budget is
  configured yet ("installed, but inactive until you set a per-session budget").

### Dashboard UI

On the Budgets page settings card, a new **Session guard** section:
- Per-session token budget (`guard.session_tokens`).
- Warn at % (comma-separated, default `75, 80`).
- After you approve: radio **Ask again every +N%** (with N input) / **Once per session**.
- Project overrides: rows of project picker (from the known projects list, showing
  name and path) + budget input or **Off** toggle + remove; **Add override**.
- Status + **Install guard** / **Uninstall guard** button calling `/api/do/guard`.
- Short note: the guard can pause a tool call for approval but cannot end a session;
  it fails open.

Saved with the existing **Save configuration** button (`guard` added to the payload).
The Budgets tour gets one step for the Session guard section.

## Testing

- `tests/test_guard.py` (fixture transcripts in a temp dir, `CLAUDE_FINOPS_HOME` set
  to a temp dir):
  - duplicate `requestId` lines counted once (last wins); lines without usage ignored;
  - subagent files included;
  - incremental read: append lines, count grows; partial trailing line deferred; file
    truncation recounts;
  - decisions: warn once per threshold; ask at 100%; step mode asks again at 125%;
    once mode never asks again; "next call after ask = approved";
  - override resolution: longest prefix wins, `/a/app` vs `/a/app2`, `off`;
  - fail-open: bad stdin, missing transcript, corrupt state file → exit 0, no output.
- Parity: guard total for a fixture session == ETL `sessions.billable_tokens` for the
  same fixture.
- `tests/test_integrate.py`: install adds exactly one entry and preserves an existing
  unrelated `PreToolUse` hook; second install is a no-op; uninstall removes only ours;
  backup file written.
- `tests/test_api.py`: `guard` accepted and persisted; rejects bad `warn_pct`,
  `after_approval`, `step_pct`, non-numeric project budgets.
- Budgets line: `sessions_over` and largest-session actual on a small warehouse,
  including an `off` project and an override.
- Run `python3 -m unittest discover tests`.

## Verification (real Claude Code session)

With a tiny budget (e.g. 50k) on a throwaway project with the guard installed:
- warnings appear at the configured percentages;
- the ask prompt appears at 100%, approving continues, declining stops;
- step and once modes behave as specified;
- record whether `ask` prompts under `--dangerously-skip-permissions`, `acceptEdits`
  and for an allow-listed tool. Document the outcome in README and the site's
  configuration/features pages.

## Docs and release

README (configuration + commands), `CHANGES.md` entry, and the Burrowkit site pages
(`features.md`, `commands.md`, `configuration.md`, `privacy.md` — the hook writes only
local state under the data dir) are updated when this ships in a release.
