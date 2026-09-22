# Changes

Plain-language notes on what changed in the dashboard and why. Newest first.

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
