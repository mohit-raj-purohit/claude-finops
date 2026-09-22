# Changes

Plain-language notes on what changed in the dashboard and why. Newest first.

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
