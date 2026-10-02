# Subagent models: measured routing and experiments

Date: 2026-10-01 · Status: approved in conversation, implementing on `feat/subagent-models`

## Why

The old Model switch view repriced past requests at a cheaper model's rates and was removed
(see CHANGES.md): it ignored extra turns and the per-model prompt cache. Subagents are the one
place a model switch is free of the cache penalty (each run starts its own cache), and Claude Code
lets you set their model. This page answers "which subagents could run on a cheaper model" from
measured runs only, and lets you test a switch with a before/after experiment.

On the author's data subagents are 2.7% of spend. The page shows that share as the hard ceiling
on what routing could ever save, so nobody mistakes it for the main lever.

## Page

"Subagent models" in the sidebar under Optimize (`?view=subagents`). Follows the page date range
and agent filter; subagent routing applies to Claude Code only. `?view=modelswitch` keeps opening
Compare models. The three tour steps that still say "Model switch" point here instead.

1. KPI row: subagent spend, its share of all spend (labelled as the ceiling), runs, types.
2. "By type and model": per agent_type, one row per model: runs, median $/run, median turns/run,
   median output/run, median context/run, total spend, and one Suggestion per type.
3. "Experiment": state, start/stop, before/after results.

## Data (`finops/subagents.py`, `GET /api/subagents`)

- A run is one `agent_id` (`requests` with `is_sidechain=1`, `agent='claude'`). Per run: cost =
  sum, turns = request count, output = sum of output tokens, context = max context_tokens.
- A run that used several models is filed under the model that cost it the most.
- Medians, not means.
- A model with tokens but no price (cost 0 on a non-free model) is "not priced" and never compared.
- No ETL or schema change.

## Suggestion rules (first match wins, one per type)

1. `fork`: never a model suggestion. Forks use the conversation's model and inherit its whole
   context; show the median context and suggest /compact before forking or a fresh subagent.
2. The type's main model (most spend) is unpriced: "can't compare".
3. Already on the cheapest model this type has used (list output price): say so.
4. A cheaper priced model with at least `subagents.min_runs` (default 5, `config/settings.json`)
   runs of this type and a lower median $/run than the main model: show both medians and run
   counts, the caveat that runs did different tasks, and the fix:
   - custom agent (a `.md` in `~/.claude/agents/` or a known project's `.claude/agents/` whose
     name matches): the file path and the `model: <alias>` line;
   - built-in: no documented per-type setting, so point to the experiment.
5. Otherwise: "not enough runs to compare", pointing to the experiment.

Never a savings total.

## Experiment

- Start (model alias haiku / sonnet / opus / fable) shows a confirm box with the exact change:
  `"env": {"CLAUDE_CODE_SUBAGENT_MODEL": "<alias>"}` in `~/.claude/settings.json`; applies to new
  sessions; does not override agent files with their own `model:` or a per-call model; no `_FORCE`.
- Write: back up to `settings.json.finops-backup`, merge, keep every other key. Refuse if the file
  is unreadable/invalid, or if the variable is already set and finops did not set it.
- Stop: remove the key only if it still holds finops' value; drop an emptied `env` block. If the
  user changed it, leave it, say so, and end the experiment anyway.
- One experiment at a time. State in `~/.claude-finops/settings.local.json` under
  `subagent_experiment`: `active` {model, started_at, stopped_at} and `history` (last 10).
- If the variable disappears or changes while active, the card warns.
- Results ignore the page date filter: after = start → stop/now; before = same length right before
  start, capped at 30 days. Per type: runs, median $/run, turns, output, before vs after, and the
  change in median $/run. Runs after start on another model are counted as "ignored the
  experiment" and kept out of the after column. Fewer than `min_runs` on either side shows
  "collecting n of 5".

## Errors

Invalid settings.json: refuse, nothing written. No runs: empty state, KPIs show 0. Claude Code not
in the agent filter: explain the page applies to Claude Code only.

## Tests

`tests/test_subagents.py`: run grouping, medians, dominant-model rule, unpriced exclusion, each
suggestion rule, custom agent detection, experiment windows and off-model counting, settings
writes (backup, other keys kept, refuse foreign value, stop only removes ours, empty env removed,
invalid JSON refused). `tests/test_api.py`: `/api/subagents` responds.

## Out of scope

Pricing for Sonnet 5.5 (shows $0 today) is a separate fix.
