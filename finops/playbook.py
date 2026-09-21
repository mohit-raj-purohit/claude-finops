"""Copy-paste fixes: for each finding, numbered steps plus a prompt to give Claude.

`where` says where to run it (which repo, or "any session"). Prompts are written so
Claude does the edit itself and keeps it small.
"""
import os


def _rel(p, root):
    return os.path.relpath(p, root) if root and p and p.startswith(root) else p


def _fix(steps, prompt=None, where=None):
    return {"steps": steps, "prompt": prompt, "where": where}


# ---------------------------------------------------------------- recommendations
def for_recommendation(r):
    t = r["title"]
    if t.startswith("Clear or compact"):
        return _fix([
            "Before switching to an unrelated task, type /clear.",
            "Once a session passes ~100K context (check with /context), type /compact.",
            "For long work, ask Claude to save progress to a file first (prompt below), then /clear "
            "and start the next session with: \"Read NOTES.md and continue.\"",
        ], "Write a short handoff to NOTES.md: goal, what's done, what's left, key files and "
           "decisions. Max 30 lines. I'm going to /clear after this.", "any long session")
    if t.startswith("Break up marathon"):
        return _fix([
            "One session per ticket/feature: start with `claude` in the repo, finish, then exit.",
            "Before ending, use the handoff prompt below so the next session starts small.",
            "Resume later with /resume only if you need that exact context; otherwise start fresh.",
        ], "Summarise this session into NOTES.md for the next session: goal, done, pending, "
           "gotchas, files touched. Keep it under 30 lines.", "the long session")
    if t.startswith("Default to Sonnet"):
        return _fix([
            "Add \"model\": \"sonnet\" to ~/.claude/settings.json (or run /model sonnet).",
            "Switch up with /model opus only for design, tricky debugging or large refactors.",
            "For custom agents, add `model: sonnet` (or haiku for search) in their frontmatter.",
        ], "Set my default Claude Code model to sonnet in ~/.claude/settings.json. Don't change "
           "anything else in that file.", "any session")
    if t.startswith("Cap noisy shell"):
        return _fix([
            "Add the output rules below to ~/.claude/CLAUDE.md (applies to every project).",
            "Run test suites with failure-only reporters (e.g. `jest --silent`, `pytest -q`).",
        ], "Add a short 'Shell output' section to ~/.claude/CLAUDE.md: pipe long output through "
           "`| tail -50` or `| head -50`, use quiet flags (-q, --silent), grep logs for errors "
           "instead of printing them, and never cat files over 300 lines — use Read with a range. "
           "Max 5 bullet points.", "any session")
    if t.startswith("Browser/MCP"):
        return _fix([
            "Prefer text reads (get_page_text, find, read_page) over screenshots.",
            "Run browser checks in a subagent so screenshots don't stay in your main context.",
            "Add the rule below to ~/.claude/CLAUDE.md.",
        ], "Add to ~/.claude/CLAUDE.md under 'Browser tools': use get_page_text/find before "
           "screenshots; take a screenshot only to verify visuals; do multi-step browser checks "
           "inside a subagent and return a short summary.", "any session")
    if t.startswith("Subagents are a large"):
        return _fix([
            "Use subagents for wide searches only; read known files directly.",
            "Give custom agents a cheaper model: `model: haiku` for search, `sonnet` for coding.",
        ], "List my custom agents in ~/.claude/agents and .claude/agents. For each one without a "
           "`model:` line, add `model: sonnet` (or `haiku` if it only searches/reads). Show me "
           "the diff.", "any session")
    if "need Claude config changes" in t:
        return _fix(["Open each project's Fix card below and run its prompt in that repo."])
    return None


# ---------------------------------------------------------------- project issues
def for_project_issue(i, project):
    t, path = i["title"], project.get("path")
    where = f"cd \"{path}\" && claude" if path else None
    if t == "No CLAUDE.md":
        return _fix([
            f"Open Claude in the repo: {where}",
            "Run /init to generate a first CLAUDE.md.",
            "Then paste the prompt below to tighten it.",
        ], "Review CLAUDE.md and keep it under 150 lines with: 1) exact install/run/test/lint "
           "commands, 2) a folder map (one line per top-level folder), 3) where key things live "
           "(routes, API layer, state, config), 4) coding conventions that differ from defaults. "
           "Remove anything Claude can infer from the code.", where)
    if t.startswith("CLAUDE.md isn't saving"):
        return _fix([f"Open Claude in the repo: {where}", "Paste the prompt below."],
                    "Look at which folders you usually have to search to find things in this repo. "
                    "Add a 'Where things live' section to CLAUDE.md: one line per area (routes, "
                    "API calls, state, shared components, config, tests) with its path. Max 15 "
                    "lines. Don't touch other sections.", where)
    if t.startswith("CLAUDE.md is ~"):
        return _fix([
            f"Open Claude in the repo: {where}",
            "Paste the prompt below; review the diff before accepting.",
            "Aim for under ~2,500 tokens (~10,000 characters).",
        ], "CLAUDE.md is loaded on every request and is too long. Shrink it to under 10,000 "
           "characters: keep commands, folder map, conventions and hard rules. Move long "
           "reference sections into docs/ files and leave a one-line pointer to each in CLAUDE.md "
           "(\"For X, read docs/x.md\"). Don't lose any rule.", where)
    if t.startswith("MEMORY.md is ~") or "long MEMORY.md" in t:
        return _fix(["In any session in this project, paste the prompt below."],
                    "Clean up my auto-memory for this project: keep MEMORY.md to one short line "
                    "per memory (under 150 chars), merge duplicates, delete memories that are "
                    "stale or already in CLAUDE.md. Show what you removed.", where)
    if t == "Same files re-read across sessions":
        files = ", ".join(_rel(x["path"], path) for x in (i.get("evidence") or [])[:5])
        return _fix([f"Open Claude in the repo: {where}", "Paste the prompt below."],
                    f"These files get re-read in almost every session: {files}. Add a 'Key files' "
                    f"section to CLAUDE.md with 2–3 lines each: what it does, main exports/"
                    f"functions, and when to open it. Don't paste their code.", where)
    if t == "Generated/large files were read":
        return _fix([f"Open Claude in the repo: {where}", "Paste the prompt below."],
                    "Create or update .claude/settings.json in this repo with permissions.deny "
                    "rules so Claude can't read generated or huge files: node_modules, dist, "
                    "build, coverage, lockfiles, *.min.js, *.map. Keep existing settings.", where)
    if t == "Unused MCP servers enabled":
        return _fix([f"In a terminal: cd \"{path}\"",
                     "Run `claude mcp list`, then `claude mcp remove <name>` for each unused one."])
    if t == "CLAUDE.md has no build/test commands":
        return _fix([f"Open Claude in the repo: {where}", "Paste the prompt below."],
                    "Add a 'Commands' section at the top of CLAUDE.md with the exact commands "
                    "to install, run dev, run tests (all and a single file), lint and build — "
                    "check package.json/Makefile to get them right.", where)
    if "stale path" in t:
        return _fix([f"Open Claude in the repo: {where}", "Paste the prompt below."],
                    f"CLAUDE.md references paths that no longer exist ({i['fix'].split(': ', 1)[-1]}). "
                    f"Find where each moved and update the reference, or delete the line if the "
                    f"thing is gone.", where)
    if "duplicated line" in t:
        return _fix(["Paste the prompt below in the repo."],
                    "Remove duplicated lines and repeated rules from CLAUDE.md. Show the diff.", where)
    if t == "No auto-memory yet":
        return _fix(["When you catch yourself repeating an instruction, say: \"remember: …\"",
                     "See 'Add to CLAUDE.md / memory' above for candidates."])
    if "links to missing" in t or "not indexed" in t:
        return _fix(["Paste the prompt below in a session in this project."],
                    "Fix my memory index: every memory file must have one line in MEMORY.md and "
                    "every MEMORY.md link must point to an existing file. Remove dead links.", where)
    return None


def for_global_issue(i):
    if i["title"] == "No default model set":
        return for_recommendation({"title": "Default to Sonnet"})
    if i["title"] == "Global CLAUDE.md is large":
        return _fix(["Paste the prompt below in any session."],
                    "~/.claude/CLAUDE.md loads in every project. Keep only rules that apply "
                    "everywhere (under 60 lines); move project-specific parts to that project's "
                    "CLAUDE.md. Show the diff.", "any session")
    return None


# ---------------------------------------------------------------- sessions
def for_live(s):
    if s["severity"] == "ok":
        return None
    return _fix([
        "In that session, paste the handoff prompt below.",
        "Then type /clear (or /compact if you need the details that are still in context).",
        "Continue with: \"Read NOTES.md and continue.\"",
    ], "Write a handoff to NOTES.md: goal, what's done, what's left, key files, decisions. "
       "Max 30 lines.", "that running session")


def for_past_session(s):
    steps = ["Next time for similar work, start a fresh session per task."]
    if any("Never compacted" in f for f in s["fixes"]):
        steps.append("Type /compact when /context shows more than ~100K.")
    if any("Same files" in f for f in s["fixes"]):
        steps.append("Run the prompt below in that repo so those files are summarised once.")
    if any("Large tool outputs" in f for f in s["fixes"]):
        steps.append("Ask for trimmed output (`| tail -50`) or run the command in a subagent.")
    if any("No subagents" in f for f in s["fixes"]):
        steps.append("Start research with: \"Use an Explore subagent to find … and report back "
                     "in 10 lines.\"")
    return _fix(steps,
                "Summarise the files you re-read most in this session into CLAUDE.md under "
                "'Key files' (2–3 lines each: purpose, main functions, when to open). Keep it "
                "short.", f"the {s.get('project')} repo")


# ---------------------------------------------------------------- memory suggestions
def for_memory(m):
    ex = (m.get("examples") or [m["text"]])
    if m["kind"] == "security":
        return _fix([
            "Rotate the exposed credential now; it is stored in plain text in ~/.claude/projects.",
            "Put credentials in an untracked .env file (add it to .gitignore).",
            "Tell Claude the variable name, never the value: \"use $DB_URL from .env\".",
        ], "Create a .env.example listing the variable names this project needs (no values), "
           "make sure .env is in .gitignore, and update the code/docs to read from env vars.",
           "the affected repo")
    if m["kind"] == "template":
        return _fix([
            "Create .claude/skills/<name>/SKILL.md in the repo (or ~/.claude/skills for global).",
            "Paste the prompt below; afterwards run it as /<name>.",
        ], "Turn this repeated prompt into a skill at .claude/skills/<pick-a-name>/SKILL.md with "
           "a one-line description and the instructions as the body. Replace the parts that "
           "change each time with $ARGUMENTS. The prompt:\n\n" + m["text"], "that repo")
    if m["kind"] == "reference":
        return _fix(["Paste the prompt below in the project where you use it."],
                    f"Add this to CLAUDE.md under 'Related locations' with a one-line description "
                    f"of what it is, so I can refer to it by name: {m['text']}", "that repo")
    if m.get("already_saved"):
        return _fix(["Paste the prompt below in any session."],
                    "I keep repeating this kind of instruction even though it's saved. Examples: "
                    + " | ".join(f'"{e[:120]}"' for e in ex[:3])
                    + ". Find the matching rule in ~/.claude/CLAUDE.md or my memory and rewrite "
                      "it to be explicit and unambiguous (or update it if these show my "
                      "preference changed). One or two lines.", "any session")
    return _fix(["Paste the prompt below in any session."],
                "remember: " + ("; ".join(e[:120] for e in ex[:2]))
                + " — save this as a standing preference.", "any session")


def attach(result):
    """Add a `playbook` to every actionable item in a diagnose() result."""
    for r in result.get("recommendations", []):
        r["playbook"] = for_recommendation(r)
    for p in result.get("projects", []):
        for i in p["issues"]:
            i["playbook"] = for_project_issue(i, p)
    for i in (result.get("global") or {}).get("issues", []):
        i["playbook"] = for_global_issue(i)
    for s in result.get("live_sessions", []):
        s["playbook"] = for_live(s)
    for s in result.get("session_health", []):
        s["playbook"] = for_past_session(s)
    for m in result.get("memory_suggestions", []):
        m["playbook"] = for_memory(m)
    return result
