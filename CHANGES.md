# Changes

Plain-language notes on what changed in the dashboard and why. Newest first.

## Unreleased

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
