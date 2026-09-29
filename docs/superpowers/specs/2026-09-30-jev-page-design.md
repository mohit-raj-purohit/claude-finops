# Jev page — design

Date: 2026-09-30
Status: approved in chat
Branch: feat/jev (from main)

## Goal

A sidebar page, **Jev (fast decisions)**, that lets anyone install TypeSafe's Jev plugin for
Claude Code in one click, add the Jev API key on the same page, and see honestly where Jev
would help.

Facts this relies on (docs.typesafe.ai, read 2026-09-30):
- Jev is a "System One" model: fast typed decisions (Choice, Score, Noul yes/no). It does not
  generate text or code. "Jev is not a drop-in replacement for the LLM behind Claude Code."
- Claude Code install: `claude plugin marketplace add typesafe-ai/skills` then
  `claude plugin install typesafe@typesafe-ai`. The skill teaches Claude to build software
  that uses Jev; it runs no scripts of its own.
- API key env var: `TYPESAFE_API_KEY`; base URL `https://api.typesafe.ai`; model `jev-latest`.
- Claimed price: $0.042 per million input tokens, output free.

Out of scope: routing Claude Code's own requests to Jev (not possible); calling the Jev API
from the dashboard; Laya AI.

## Page (sidebar: Optimize → "Jev (fast decisions)", always visible)

1. **What Jev is** — three plain sentences: Jev is a very fast, very cheap AI that makes
   decisions (pick one, yes or no, give a score, pull out a value) and can't write text or code;
   it doesn't make Claude Code itself cheaper, it makes the apps and scripts you build cheaper
   by replacing LLM calls that only make a decision; installing it teaches Claude how to use
   Jev when you build those. Link to docs.typesafe.ai.
2. **① Install** — status line ("○ Not installed" / "● Installed · version x" /
   "Claude Code's `claude` command was not found"). **Install** first shows the exact two
   commands and "This adds TypeSafe's plugin to Claude Code for all your projects", then
   **Confirm install**; output streams below. **Uninstall** (two clicks) runs
   `claude plugin uninstall typesafe@typesafe-ai`.
3. **② API key** — password input + **Save key**; saved as `env.TYPESAFE_API_KEY` in
   `~/.claude/settings.json` (backup first, nothing else changed). Status "set · ends ABCD";
   **Remove key** (two clicks) deletes only that entry (and an empty `env`). If the dashboard's
   own environment has `TYPESAFE_API_KEY`, say the shell value wins. Notes: new Claude Code
   sessions see it, restart open ones; get a key at console.typesafe.ai. Shape check: non-empty,
   no whitespace, at least 16 characters.
4. **③ Where Jev fits** — read-only: Claude Code prompts in the current filter that are short
   (≤ 2 requests, no tools, ≤ 300 output tokens) and worded as a decision (classify, which one,
   yes or no, extract, score, …) and not a build request; count, cost on Claude, rough cost on
   Jev (context tokens × $0.042/M), up to 5 examples with paths/emails/long numbers masked (and
   hidden entirely when "Hide prompts" is on). When the share of spend is under 1%, say:
   "Your Claude Code work is mostly writing code, which Jev can't do."

## Backend

`finops/jev.py`:
- `PLUGIN = "typesafe@typesafe-ai"`, `MARKETPLACE = "typesafe-ai/skills"`, `ENV_KEY = "TYPESAFE_API_KEY"`.
- `status()` → `{claude: bool, installed: bool, version, enabled}` from `claude plugin list --json`
  (fixed argv, 20 s timeout; errors → installed False with a message).
- `install(log)`, `uninstall(log)` — fixed argv via `actions._sh`, run as background jobs;
  marketplace add that fails because it already exists is not an error.
- `key_status()` → `{source: "shell"|"claude_settings"|None, last4}`; never the key.
- `save_key(value)`, `remove_key()` — through `integrate._load_settings/_save_settings`.
- `fit(a, f)` → numbers and examples (read-only SQL).

API: `GET /api/jev` → `{status, key, fit}`; `POST /api/do/jev/install|uninstall` → `{job}`;
`POST /api/do/jev/key` `{value}` or `{remove: true}`. Same-origin rules as other actions.

## Testing

- key saved into `env`, other settings and env entries kept, backup written, key never returned;
  remove deletes only that key; bad key rejected; shell value reported as winning.
- install/uninstall call exactly the expected argv (subprocess mocked); missing `claude` → clear
  error; "already added" marketplace tolerated; status parses `plugin list --json`.
- fit on a small warehouse: decision prompt counted, build prompt and tool-using prompt not;
  masking of paths/emails.
- API: shape, cross-origin 403, bad key 400.
- Browser (headless Chrome with its own profile, stopped by PID; demo data): page renders, install
  confirm step shows commands (not confirmed), key field errors, fit card. No real install, no
  real key saved.
