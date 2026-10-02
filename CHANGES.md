# Changes

Plain-language notes on what changed in the dashboard and why. Newest first.

## 0.15.0 — Subagent models, numbers you can trust, history that outlives cleanup

**Which subagents could run on a cheaper model, measured.** A new **Subagent models** page
(Optimize) shows each subagent type, the models it ran on, and what a run cost, how many turns it
took and how much it wrote, as medians. It suggests a cheaper model only when that model has at
least 5 runs of the same type and really cost less per run. Your own agent files get the exact
`model:` line to add. Forks get context advice instead, since they always use your conversation's
model and carry its whole context.

**It tells you the ceiling first.** The headline is subagents' share of your spend: the most
routing could ever save, even if every run were free. There is no savings estimate. The old Model
switch view was removed for repricing work that never ran, and this page doesn't do that.

**Try a model and see.** **Start experiment** sets `CLAUDE_CODE_SUBAGENT_MODEL` in Claude Code's
settings (after showing you the change, with a backup), and the page compares each subagent type
before and after. **Stop** puts it back. finops never overwrites a value you set yourself.

**Fixed.** Three guided-tour steps still sent you to the removed Model switch page.

**Overview layout.** The Model cost table sits next to its chart again, and Optimization
opportunities and FinOps score no longer stretch to match the cards beside them.

**Fixed numbers, from a full audit of every page.** Totals were already right everywhere;
these were not:

- **Waste detection** counted at most 15 items per rule and showed that cap as the count
  ("15 very long prompts" when there were 420). Every rule now counts and totals all
  matches and shows the 15 costliest as examples. The headline excess could be smaller
  than one rule's own excess; it now adds up. Duplicate prompts honour the 60-minute
  window in settings and never count the first ask. Rules that claim no excess say so
  instead of "$0.0000 excess".
- **Your own calendar days.** Days were cut at UTC midnight, so in India everything
  between midnight and 05:30 landed on the previous day, and "Today" meant the last day
  with data. Days, "today" and the billing period now follow this computer's time zone
  (or `CLAUDE_FINOPS_TZ`). Press **Sync** once to re-read your history this way.
- **Burn rate and Forecast** no longer change with the page's date range, and days after
  your last sync are projected instead of counted as $0; both pages say when that applies.
  Scenarios show the daily rate each one implies, and "A typical 30 days" replaces a
  figure that repeated the end-of-period one.
- **Claude Sonnet 5.5** is priced ($2 / $10 per million, like Sonnet 5) instead of $0.
  Press **Sync** to re-cost it. Compare models now lists every Claude model you used,
  Opus 5.5 included, with your usage in the selected range.
- **Skills & MCP** suggestions come from Claude Code's own prompts and commands in the
  selected range, not from other agents' templates.
- **The same figure everywhere.** The context share on What should I do? now matches
  Context hygiene and the scorecard (main conversation, at or above 150K). Context
  utilisation is measured against each model's own window.
- **Smaller fixes.** Usage timeline keeps idle days and counts each prompt once; the
  model-mix chart is right with a model filter on; Prompt intelligence averages are per
  prompt; project file counts, most-touched files and git branches follow the filters;
  "Cheapest" skips $0 local runs; "Most token-efficient" needs a real sample; anomalies
  rank by severity and show dollars as dollars; search treats `_` and `%` literally;
  the scorecard says how much caching actually saved; tool tokens in Why so many tokens?
  count each request once; estimated cards say Estimated; prompt and session drawers say
  when they show the whole item rather than the selected dates; Jev's cost uses the
  prompt, not Claude Code's whole context; Free models notices a model you already
  downloaded; Budgets suggestions use your last 30 synced days.
- **Stays fresh by itself.** While the dashboard runs it re-reads your transcripts once
  the data is an hour old (`CLAUDE_FINOPS_AUTOSYNC_MINUTES`, `0` to turn off).
- **New Claude models aren't $0 any more.** A model missing from `pricing.json` is priced
  as the newest listed model of its family and marked "priced as …" until it gets its own
  entry. Requests that still have no price (for example Cursor's) are counted on the
  Requests figure so you can see them.
- **Fewer prompts in "other".** Short steering turns ("do that", "fix all", a reply in
  another language) are now *follow-up*, and commit/push/merge work is *version control*.
  Sync to re-sort your history.
- **Drawers show both numbers.** A prompt or session opened from a filtered list shows
  what it cost in the selected dates next to its whole total.
- **Billed vs local** compares on UTC days, as the vendors report them. Hourly charts and
  the heatmap follow `CLAUDE_FINOPS_TZ` too. The forecast chart's labels no longer print
  on top of each other, and a zero amount reads "$0".
- **Cursor agent transcripts are no longer read.** They carry no times and no token
  counts, so 18,975 rows were dated by each file's last save and every assistant line was
  counted as a request. Cursor now shows only what its IDE recorded: real times and
  tokens (635 requests here).
- **Waste rules say who sent what.** Very long prompts are split into ones you sent and
  ones a script sent through the SDK (each with advice that fits), Claude Code's own
  continuation summaries are no longer counted as your prompts, and a pipeline re-sending
  its template is no longer a "repeated prompt". The advice for frontier-model prompts no
  longer suggests switching models mid-session, which rewrites the cache.
- **Your history survives Claude Code's cleanup.** Claude Code deletes old transcripts,
  and every sync used to rebuild from what was left, so those sessions vanished from the
  dashboard. A sync now keeps sessions whose transcript is gone, re-costed at current
  prices. `history.keep_deleted_transcripts: false` restores the old behaviour.
- **Gemini cache reads** are priced at Google's current rate (10% of input, not 25%).
  OpenAI and Gemini prices were checked against the vendors' pages.

## 0.14.0 — Limits for one conversation, a simpler Budgets page, Jev

**A token limit for each Claude Code conversation.** Set a per-session token budget on the
Budgets page (block ③). It counts everything a conversation uses, including re-reading its
context, the same figure as the Tokens column in Sessions. You can give a big project its own
limit, or turn it off for a project. The Budgets page shows the largest conversation against its
limit, and Sessions marks conversations near (amber) or over (red) theirs.

**Live warnings inside Claude Code (optional).** Your limits work without this. Install it (Budgets,
block ③, or `claude-finops --install-guard`) and Claude Code warns you at your warn percentages
and asks "continue?" before the next step once a conversation reaches its limit. After you say
yes it asks again every +25% (or never, your choice). It can't end a conversation, and if it
ever fails it lets Claude carry on.

**Set a limit on a conversation that is already running.** On Running sessions, **Set limit**
shows what that conversation has used so far, suggests +10%, +25%, +50% or double, and applies
from its next step.

**A Budgets page anyone can use.** The settings are now three numbered blocks: ① money,
② tokens, ③ one conversation, with advanced settings folded away. Every amount suggests values
from your own last 30 days, accepts shorthand like `20M`, `500k` or `$3,000`, and tells you what
is wrong before you save. **How does this work?** opens a short picture guide, and each block has
its own **?**.

**Settings page.** The optional API keys for Billed vs local (Anthropic Admin, Cursor) now live on
their own Settings page, where you can add or remove them without the terminal. The page only
ever shows a key's last four characters.

**Jev (fast decisions).** A new page for TypeSafe's Jev, a very fast, very cheap AI that only
makes decisions. Install its Claude Code plugin in one click, add your Jev API key, and see how
much of your own work was really just a decision. Jev makes the apps you build cheaper; it does
not make Claude Code itself cheaper, and the page says so.

**Fixes.** The Custom date range picker opens in the right place and closes on a second click.

## 0.13.0 — Flat icons everywhere, the dashboard opens itself

**Opens in your browser.** Run `claude-finops` and the dashboard opens in your default
browser as soon as it is ready, so there is no URL to copy. On a machine without a browser
(SSH, headless) nothing opens and the printed address still works.

**One icon style across the app.** The emoji and text symbols in the sidebar, buttons,
alerts, card headings and ratings are now flat line icons in the same style as the top
bar. They follow the light and dark theme and look the same on every system. Severity
markers are small flat dots in green, amber, orange and red.

## 0.12.0 — A cleaner top bar, colour-graded context metrics

**A cleaner, data-rich top bar.** The page title now carries the date range and active
filters on a small line under it. Next to it, a strip shows estimated spend, sessions,
prompts, requests and active days for the current filters, on every page. Agent and date
range are compact grouped controls, the sandbox setting is a **Hide sandbox** switch, and
the data coverage note is shorter (hover it for the last rebuild time).

**Icons that say what they do.** The top-bar buttons are one joined toolbar: a compass for
**Tour**, a history clock for **Recent** (still `R`), an eye for **Hide prompts** (crossed
out while prompts are hidden), a moon or sun for the theme you would switch to, and a
circular arrow for **Reload**, which redraws the page from the warehouse. **Sync** now
shows a database icon, because it reads your transcripts from disk, so the two no longer
look alike.

**Colour-graded context metrics.** On Context hygiene, the spend-share cards are tinted
green, amber, orange or red by how much of your spend sat at large context (hover a card
for its bands). The context-window card uses tighter bands, and the totals cards stay
neutral. The colours are a reading aid, not a verdict.

**A tour step for every metric.** The Context hygiene tour now walks through each card:
what it measures, why it matters and how to read it against the others, plus the context
chart.

**A loader worth waiting for.** While a page computes you see a skeleton in its shape,
a status line saying what it is doing (with a timer on slow queries) and a tip.

## 0.11.0 — Hide prompts, Recent panel

**Hide prompts.** A new top-bar button hides prompt text and session titles as dots
everywhere in the dashboard: tables, prompt details, chart labels, tooltips and the
Act now strip. Use it before you share your screen. Click again to show them. The
choice is remembered in your browser. Exports are unchanged.

**Recent panel.** A new **☰ Recent** button in the top bar (or press `R`) opens a side
panel on any page. Sessions running right now come first, with Resume, Compact and Stop.
Stop interrupts the current turn, and each of Compact and Stop needs two clicks. Past
sessions follow, newest first. Click one to see its prompts, and click a prompt for
the same detail view the Prompt explorer opens. Search finds prompts by their text.
Esc closes the detail view first, then the panel. It covers all dates, keeps your
agent and project filters, and respects Hide prompts. Close and Force kill stay on
Running sessions.

## 0.10.1 — Maintenance release

Version number only. The dashboard is the same as 0.10.0.

## 0.10.0 — Act now

**Act now, on the overview.** The things only this dashboard can do used to sit a few
clicks deep. The overview now opens with a short list of one-click actions that apply
to you right now: compact or hand over a running session carrying heavy context, turn
a shell command you keep re-running into a skill, move an instruction you keep
re-typing into CLAUDE.md, or install the statusline. Only high-signal suggestions
make the list; the full lists stay on their own pages.

**Resume and Compact in every session row.** The Sessions table and Cost rankings
now have a Resume button that copies `claude --resume <id>`, and a Compact button on
any session that is running right now, without opening the session panel.

**"Focus on these first" is now "What to change".** It used to mix a running session,
one-off fixes and habits learned from past sessions under one "Fix first" label, with
no reason given, so it read as if closed sessions still needed fixing. Running sessions
now live only in Act now. Each remaining item is labelled **Fix once** or **Habit**,
shows the evidence behind it in one line, and has a ✕ to hide it until that evidence
changes.

**The statusline works.** It was being written to Claude Code's settings in a shape
Claude Code doesn't accept, read a context field that doesn't exist, and broke on
install paths with a space. It now uses the documented settings shape and fields, and
also shows how much of your 5-hour limit you've used. It can be installed from the
dashboard as well as with `--install-statusline`.

## 0.9.0 — Peak hours, plan-limit history, Claude desktop sessions

**Peak hours.** The Usage timeline has a new weekday × hour heatmap showing when
you spend, in your own local time. Transcripts are stamped in UTC, so without the
conversion a 10am session in India would show up at 4am.

**Plan limits over time.** The Claude desktop app keeps a local record of your
5-hour and weekly plan usage, sampled every few minutes. Burn rate & limits now
charts it, with your peaks and how often you ran above 90%. The file's format is
undocumented; if an app update changes it, the card says so and goes blank instead
of showing something wrong.

**Claude desktop app (Cowork) sessions are included.** Cowork writes Claude Code
transcripts into the desktop app's own folder, which the dashboard never read. They
now load alongside your other Claude sessions, as projects named `Cowork · …`.

**Today against yesterday.** The overview's spend card shows how today compares
with yesterday.

**Resume from any session.** The session panel now shows the
`claude --resume <id>` command with a Copy button, not only for running sessions.

**Older Claude models are priced.** Claude 4.x (Opus 4 to 4.8, Sonnet 4 to 4.6) and
Haiku 3.5 were missing from the price table, so their requests showed as unpriced.
They now use Anthropic's published list prices.

**Run Sync once after upgrading.** Cowork sessions and the new prices are picked up
when the warehouse is rebuilt: click Sync, or start with `--rebuild`.

## 0.8.0 — Cost figures corrected: one row per request, list prices fixed

**Requests were counted more than once.** A streamed response arrives as several
content blocks, and each block was written as its own row in the warehouse. A
single request could land as two or three rows, so request counts — and every cost
figure built on top of them — were roughly doubled across the dashboard.

**The price table had two models wrong.** Opus 5 was priced at three times its
published list price, and Fable was priced at a third of its list price. Both are
now taken directly from the published rate cards.

**The 1M-context surcharge no longer applies.** The long-context pricing tier that
some providers charge above 200K tokens was being applied everywhere, including to
usage that never qualified for it. It is now applied only where it actually holds.

**Totals fall sharply as a result.** On the author's own data, correcting the double
counting, the two mispriced models, and the surcharge together took total estimated
spend from about $17,000 to about $3,400. If your numbers used to look implausibly
high, this is why.

**The warehouse rebuilds itself.** Existing installs carry the old, inflated numbers
in their local database. The dashboard detects the schema change on first launch
after upgrading and rebuilds the warehouse from your transcripts automatically, so
you do not need to run `--rebuild` by hand.

**Several features were removed because they could not be trusted or defended:**

- **Model-switch back-test** (the "what if you'd used a cheaper model" comparison)
  assumed a smaller model would have produced the same conversation, which is not
  something a transcript can tell you. It is gone rather than left to mislead.
- **Trial mode** hid the accuracy gaps above behind a shortened, cherry-picked demo
  view; once the underlying numbers are honest, there is no reason to keep a
  separate, less honest one around.
- **Live model advice** suggested switching models mid-session using the same
  unverifiable assumption as the back-test above, so it goes for the same reason.
- **Waste "excess" on four rules** claimed a specific dollar amount was avoidable
  under rules whose counterfactual (what would have happened instead) cannot be
  computed from the data on hand; those four rules now report what happened, not
  what a different choice would have cost.
- **End-of-day forecast** projected a full day's spend from a partial day using a
  method that broke down badly on short or unusual days, and cost more confidence
  than it delivered.
- **Scorecard grade** collapsed several independent metrics into a single letter
  grade that implied a precision none of the underlying numbers actually have; the
  individual metrics remain, without the invented letter on top.

## New first page: Context hygiene

**What you'll notice.** The dashboard now opens on **Context hygiene** instead of the
executive overview. It shows, from your own transcripts: the share of spend in
requests above 100K and 150K context (both configurable in `config/settings.json`
under `hygiene.context_thresholds`); the share of spend that came *after* a session
first crossed each line; every session ranked by what it spent after crossing; and,
for any session you click, the context size of each request in order.

**Why it comes first.** Almost all of the bill is re-reading context. On the data this
was built against, 87% of spend sat in requests above 150K context, and 98% of
billable tokens were cache reads. That is the dominant cost, and it is directly
observable — unlike a model-switch estimate, nothing here has to be assumed.

**What it deliberately does not show.** No "what compaction would have saved" figure.
Whether a fresh session would have cost less depends on what the work still needed
from the old context, and the transcript does not say. Subagent turns are left out
because they run against their own prefix. `/clear` and `/compact` are not recorded
by Claude Code, so a compaction appears only as the context dropping.

## Four numbers that could not be defended are gone, and a crash is fixed

**Pages no longer fail at random.** Views that load several figures at once — the
advisor, the scorecard, recommendations — sometimes showed "Something went wrong"
for no reason and worked on reload. The server shared one database connection
across every request; it now uses one per thread. Under a load that previously
failed about half the time, it fails none.

**Context utilisation no longer reads 146%.** The Model analysis table divided your
average context by the model's standard window, even for requests that were served
by the long-context variant. It is now measured against the window that actually
served each request, so Opus reads 53%, not 146%. Requests that ran over a window
this price table cannot explain are counted beside the figure ("· 10,887 over")
rather than averaged into an impossible percentage.

**The advisor's "estimated savings opportunity" line is gone.** It was 20% of your
large-context spend, then 0.6× of that for a low end. Neither number came from your
data. The same guessed figures have been removed from the "Why so many tokens?"
playbook and the Recommendations view. The observations behind them stay — what
share of spend runs at large context, that caching is working — with no dollar
saving attached.

**The $90,000 "caching saving" is now called what it is.** What the same tokens
would have cost with no cache at all is a counterfactual nobody would have run, not
money you avoided. It is still shown, labelled "uncached counterfactual — not a
saving", and no longer appears as a recommendation.

## The "Model switch" view and its savings figures are gone

**What you'll notice.** The Model switch page has been removed from the sidebar. The
"Potential savings", "Safe to switch" and per-category "Saves $X" figures no longer
appear anywhere — not on that page, not in the advisor, not in the printable report.
Old links to the page open **Compare models** instead. The "Consider a cheaper model
for X work" recommendation, which carried the same numbers by another route, is gone
too.

**Why.** Those figures were produced by taking every request you made and repricing
the same tokens at a cheaper model's rates. That method has two problems big enough
to make the number meaningless:

- It assumed the cheaper model would finish the job in exactly the same number of
  turns. It often will not, and each extra turn re-reads the whole conversation, so a
  "cheaper" model can cost more.
- It ignored that the prompt cache belongs to one model. Switching mid-session means
  the new model has to write the entire conversation into its own cache first, which
  costs roughly twelve times what reading it did. Switching per request was never free
  in the way the reprice implied.

On real usage the "safe" savings came to about 4% of spend — smaller than either of
those errors. The high / medium / low confidence labels were assigned by hand, not
measured, so they were removed as well.

**What stays.** The one honest piece — how much of your spend ran near the ceiling of
the context window — is kept and will become the basis of a context-hygiene view. The
**Compare models** page still shows what each model actually cost when you used it on
the same kind of work; that is measured, not repriced.
