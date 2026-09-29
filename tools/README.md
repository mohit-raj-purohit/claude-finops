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
2. Open Budgets in a very wide window (2200 px) so two cards fit side by side. No sidebar
   is needed. In the browser dev tools (or any DevTools-protocol script) add this CSS to the
   page, which hides the "Budget used" card and puts the other two cards next to each other:
   `#page{display:grid!important;grid-template-columns:1fr 1fr;gap:16px;align-items:start}
   #page>.card:first-child{display:none}`
3. Screenshot from the top-left of the "Budget vs actual vs forecast" card to the right edge
   of the "Set your limits" card, ending at the bottom of block ③ (the Live warnings box).
   The result is a landscape picture of about 2:1. Scale it to 1300 px wide
   (`sips -Z 1300 web/guide/budgets.png`) and keep it under 250 KB.
4. Check the picture shows no sidebar, no name or email, and only demo project names.
5. If the layout changed, update the `marks` positions of slide `overview` in `GUIDES.budgets`
   (x and y are % of the picture) and the slide text. Slide 1 must fit without scrolling in a
   1280x800 window; CSS caps the picture at 45vh.
