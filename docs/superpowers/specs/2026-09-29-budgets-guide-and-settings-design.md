# Budgets guide, simpler Budgets form, and a Settings page — design

Date: 2026-09-29
Status: approved in chat, pending spec review
Branch: feat/session-token-guard (builds on the session guard and budgets-form work)

## Goal

Make the Budgets page usable by anyone, "even a 5th-grade student":

1. Reorganise the budget form into numbered blocks, each opening with one plain sentence.
2. Say clearly that **Install guard is optional**: budgets work without it.
3. Add a **guide popup** of picture slides that explains every block.
4. Move **API keys** out of Budgets onto a new **Settings** page.

The guide is built as a small reusable engine (a page supplies its slides), because the
next piece of work is guides for the whole app. That work is out of scope here.

Out of scope: guides for other pages; moving the statusline install; changing what any
budget computes; changes to the session guard hook itself.

## Language rules (apply to every new string)

- Short sentences, everyday words, one idea per sentence.
- Explain a technical word the first time it appears on a surface:
  - token: "the small pieces of text Claude reads and writes (about ¾ of a word)"
  - session: "one Claude Code conversation"
  - hook / guard: "a small helper that runs inside Claude Code"
- Tell the user what to do: "Type a number, or tap a suggestion."
- Numbers in examples come from demo data, never from the user's data.

## Part 1: Budgets page layout

Top to bottom:

1. **Results** (existing "Budget vs actual vs forecast" card, unchanged except a one-line
   intro: "How you're doing against your limits. Set your limits below.").
2. **Set your limits** card (replaces "Configure budgets, limits and thresholds"). Header
   has a **How does this work?** button. Blocks, each a `<section>` with a numbered title,
   a one-sentence intro and a small **?** button that opens the guide at its slide:
   - **① Money limits** — "Set the most money you want to spend. We warn you before you
     go over." Fields: Monthly budget (USD), Daily budget (USD).
   - **② Token limits** — "Tokens are the small pieces of text Claude reads and writes.
     Set how many you want to use in a month." Field: Monthly token budget.
   - **③ Session limit** — "A session is one Claude Code conversation. Stop one
     conversation from getting too big." Fields: Per-session token budget, Warn me at,
     After I say "continue", Different limit for a project. Then the **Live warnings box**
     (below).
   - **Advanced** — a `<details>` element, closed by default, summary "Advanced: plan
     limits and alert thresholds (most people can skip this)". Plan limits keep their
     one note; Alert thresholds: "When a money or token limit reaches these percentages,
     the dashboard marks it."
   - **Save** row: button, status message, and one line "Saved on this computer only."
3. All existing behaviour stays: suggestion chips, shorthand, live hints, inline errors,
   blocked save, override rows, the step select.

### Live warnings box (inside ③)

A bordered box, visually separate from the fields:

- Title: "Live warnings in Claude Code (optional)".
- Status line + one button:
  - not installed: "○ Not installed" + **Install**
  - installed with a budget: "● Installed · works in new Claude Code sessions" +
    **Uninstall**
  - installed without any per-session budget (global or override): amber
    "Installed, but it does nothing until you set a per-session token budget above." +
    **Uninstall**
- Body: "Your limits above already work without this. Install it if you want Claude Code
  itself to warn you at your warn percentages and ask \"continue?\" when a conversation
  reaches its limit. Needs: a per-session token budget. Undo any time."
- The install result message shows inside the box.

## Part 2: Settings page

- New sidebar group **Setup** (last group) with one item: **Settings** (gear icon),
  view id `settings`. It is always visible (not filtered by agent/priced flags).
- Content: one card **API keys** with the rows moved unchanged from Budgets (status badge,
  gives you, get a key, password input, Save key, two-click Remove, errors). Intro:
  "You only need these for the **Billed vs local** page. Skip this if you don't use it.
  Keys stay on this computer, in a file only you can read, and are never shown again."
- Budgets no longer shows API keys.
- Billed vs local's "Set up the APIs" card and its tour step link to **Settings** instead
  of Budgets. The page tour for `settings` gets one step.

## Part 3: the guide engine

`web/app.js`, one small module section:

- `GUIDES = {budgets: [slide, ...]}`. A slide:
  `{id, title, img?, live?, marks?, text: [sentences], target?}`
  - `img`: path of a real screenshot (slide 1 only).
  - `live(host)`: draws a frozen copy of the real block with demo values.
  - `marks`: numbered circles, `[{n, x, y}]` in percent of the picture box.
  - `target`: a selector on the real page for **Show me on the page**.
- `openGuide(name, slideId?)` opens a modal dialog (`role="dialog"`, `aria-modal`,
  focus trapped, Esc closes, ← → move). Footer: progress dots, **Back**, **Next** (last:
  **Done**), **Show me on the page** (only when `target` exists). "Show me" closes the
  dialog, scrolls the target into view and adds a 2-second pulse outline.
- First visit: Budgets opens the guide once per browser (`localStorage`
  `finops.guide.budgets.seen`), wrapped in try/catch; if storage fails, it simply does not
  auto-open. The existing welcome tour takes priority: if it is showing, the guide waits.
- Live copies are built by the same field helpers as the form (`amountField`,
  `pctField`) with fixed demo values and inputs disabled, inside a `.guide-live` wrapper
  that scales down and ignores pointer events. IDs are prefixed so they never collide
  with the real form.

### Slides (Budgets)

1. **What is this page?** — real screenshot `web/guide/budgets.png`, marks ①②③ on the
   results, the money/token blocks and the session block. "The top shows how you're doing.
   The bottom is where you set your limits. You only need to fill in what you care about."
2. **① Money limits** — live copy. "Type the most you want to spend in a month or a day,
   or tap a suggestion. Suggestions come from your own recent spending. We warn you as you
   get close."
3. **② Token limits** — live copy. "A token is a small piece of text, about ¾ of a word.
   Claude counts everything in tokens. Set a monthly number, or tap a suggestion."
4. **③ Session limit** — live copy of budget + warn. "A session is one Claude Code
   conversation. Long conversations get expensive, because Claude re-reads everything each
   time. Set a limit, and pick when you want a warning."
5. **After you say "continue", and project limits** — live copy. "When a conversation
   hits its limit, you can let it continue. Choose if we ask again later, or never. Give a
   big project its own limit, or turn it off."
6. **Live warnings (optional)** — live copy of the box plus a drawn copy of the Claude
   Code prompt ("Session guard: … Allow this tool call?"). "Your limits already work
   without this. Install it to get the warning inside Claude Code while you work. You can
   uninstall it any time."
7. **Advanced** — live copy. "Plan limits: only if you know your plan's numbers. Alert
   thresholds: when a limit gets marked. Most people can skip this."
8. **Save, then check your results** — live copies of a budget line and a Sessions dot.
   "Press Save. The top of this page shows how you're doing. In Sessions, amber and red
   dots mark conversations near or over their limit."

### The screenshot

- `tools/demo_data.py` (new; `tools/` is not in the npm `files` list): writes made-up
  transcripts (projects such as `shop-app`, `blog`, `data-pipeline`; ~30 days; a spread of
  session sizes) into a temp dir, builds a warehouse with `Loader`, and writes a demo
  `settings.local.json` with example budgets. Prints the `CLAUDE_FINOPS_HOME` to run with.
- The screenshot is captured from the dashboard running on that home, at a fixed window
  width, cropped to the Budgets page, and saved as `web/guide/budgets.png` (target
  < 250 KB). `tools/README.md` records how to recapture it.
- Never captured from real data.

## Part 4: files

- `web/app.js`: Budgets view restructure; `VIEWS.settings`; nav entry; guide engine and
  the Budgets slides; tour text updates.
- `web/styles.css`: block, live-warnings box, `<details>`, guide dialog, pulse.
- `web/guide/budgets.png`: the screenshot.
- `finops/api.py`: nothing new (keys endpoints already exist).
- `tools/demo_data.py`, `tools/README.md`.
- `README.md`: Budgets section wording, Settings page, guide.

## Testing

- `tests/test_demo_data.py`: the generator creates a warehouse with the expected projects
  and a non-empty session spread, and contains none of the real `~/.claude` paths.
- Existing suite passes (`python3 -m unittest discover tests`).
- `node --check web/app.js`.
- Browser check on the demo home and on real data:
  - blocks render with intros; Advanced is closed by default; Save/errors still work;
  - Live warnings box shows the right status in all three states;
  - guide: first-visit auto-open once; every ? opens its slide; Back/Next/Esc/arrows;
    Show me scrolls and pulses; focus returns to the button that opened it;
  - live copies show demo values only and cannot be edited;
  - Settings page shows API keys; Budgets no longer does; Billed vs local links to Settings;
  - light and dark theme, and a narrow (phone-width) window.
