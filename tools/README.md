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
2. Open Budgets. Window width 1280 px. Scroll to the top of the page.
3. Screenshot the page area (no sidebar needed), crop to the results card and the
   "Set your limits" card down to block ③, and save as `web/guide/budgets.png` (< 250 KB).
4. If the layout changed, update the `marks` positions of slide `overview` in `GUIDES.budgets`.
