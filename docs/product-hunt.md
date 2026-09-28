# Product Hunt launch — Claude FinOps

Copy-paste kit for the Product Hunt submission. Images are in `docs/img/`, listed in
upload order.

## Name

Claude FinOps

## Tagline (45 / 60 chars)

> Find out why your Claude Code bill is so high

## Description (~235 / 260 chars)

> A local dashboard that reads your Claude Code transcripts and tells you what you spent, why, and what to change — with a prompt you can paste back into Claude Code. One command, no API key, no account, nothing leaves your machine.

## Topics

Productivity · Developer Tools · Artificial Intelligence

## Links

- Website / GitHub: <https://github.com/mohit-raj-purohit/claude-finops>
- npm: <https://www.npmjs.com/package/claude-finops>

## Gallery (upload in this order)

### 1. Why so many tokens? — the differentiator, lead with it

![Why so many tokens?](img/diagnose.png)

### 2. Executive overview

![Executive overview](img/overview.png)

### 3. Waste detection

![Waste detection](img/waste.png)

### 4. Model analysis

![Model analysis](img/models.png)

### 5. FinOps scorecard

![FinOps scorecard](img/scorecard.png)

### 6. Projects

![Projects](img/projects.png)

### 7. Forecast

![Forecast](img/forecast.png)

## Maker's first comment

Hi everyone 👋 I'm Mohit, maker of Claude FinOps.

I built it because I use Claude Code every day and couldn't answer a simple question: *why did that session cost so much?*

Plenty of tools show you a total. I wanted one that explains the number and tells me what to change.

Run `npx claude-finops` and it opens a local dashboard with:

- Cost per project, drilling down to the session and the prompt
- **"Why so many tokens?"**: where your tokens went, plus a prompt to paste into Claude Code to fix it
- Waste detection: repeated prompts, abandoned sessions and unneeded context, each with a dollar estimate
- Model switch analysis: the same work priced on a cheaper model
- A 0–100 FinOps scorecard and a forecast against your budget
- Codex, Gemini CLI and Cursor too, if they're installed

It's pure Python standard library, binds to localhost, and needs no API key. Every number is labelled *Actual*, *Estimated* or *Forecast*. Transcripts don't include billed amounts, so dollar figures are estimates from list prices, and the dashboard says so.

Free and MIT-licensed: github.com/mohit-raj-purohit/claude-finops

I read every comment. What would you want to know about your AI spend that no tool tells you yet?
