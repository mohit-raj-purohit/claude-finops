"""Token diagnosis: why consumption is high, what to change, and which projects'
Claude config files (CLAUDE.md, memory, .claude/settings.json, MCP servers) need work.

Token counts are actual. Dollar figures are estimated from config/pricing.json.
Config checks read the files on disk right now, so they reflect today's state,
not the state at the time the tokens were spent.
"""
import json
import os
import re
from collections import Counter, defaultdict
from datetime import timedelta

from .analytics import _d

HOME = os.path.expanduser("~")
CLAUDE_DIR = os.path.join(HOME, ".claude")
CHARS_PER_TOKEN = 4          # rough, stated wherever it is used
CLAUDE_MD_WARN_TOKENS = 2500
MEMORY_WARN_TOKENS = 1500
EXPLORE_BASH = re.compile(r"^\s*(cd [^;&]+[;&]+\s*)?(ls|find|grep|rg|cat|head|tail|sed -n|tree|wc)\b")
# What each agent calls the same things, so advice names the right file and command.
VOCAB = {
    "claude": {"name": "Claude Code", "md": "CLAUDE.md", "md_paths": ("CLAUDE.md", ".claude/CLAUDE.md"),
               "clear": "/clear", "compact": "/compact", "shell": ("Bash",),
               "explore": ("Read", "Grep", "Glob"),
               "model_how": "Run /model sonnet for routine edits, tests, docs and lookups (or set "
                            "\"model\" in ~/.claude/settings.json). Give subagents a cheaper model in "
                            "their agent definition.",
               "cheaper": "Default to Sonnet; switch to Opus only for hard problems"},
    "codex": {"name": "Codex", "md": "AGENTS.md", "md_paths": ("AGENTS.md",),
              "clear": "/new", "compact": "/compact", "shell": ("exec_command", "shell", "local_shell"),
              "explore": (),
              "model_how": "Use /model to pick a smaller GPT-5 model (or lower reasoning effort) for "
                           "routine edits, tests and docs; set model = \"...\" in ~/.codex/config.toml.",
              "cheaper": "Use a cheaper GPT model for routine work"},
    "gemini": {"name": "Gemini CLI", "md": "GEMINI.md", "md_paths": ("GEMINI.md", ".gemini/GEMINI.md"),
               "clear": "/clear", "compact": "/compress", "shell": ("run_shell_command",),
               "explore": ("read_file", "read_many_files", "glob", "search_file_content", "list_directory"),
               "model_how": "Run gemini -m gemini-2.5-flash (or /model) for routine edits, tests "
                            "and docs; set \"model\" in ~/.gemini/settings.json.",
               "cheaper": "Default to Flash; switch to Pro only for hard problems"},
}
NOISY_PATH = re.compile(r"(node_modules/|/dist/|/build/|/\.next/|/coverage/|package-lock\.json|"
                        r"yarn\.lock|pnpm-lock\.yaml|\.min\.js|\.map$|/data/.*\.(json|csv)$)")


def _tok(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            return len(fh.read()) // CHARS_PER_TOKEN
    except OSError:
        return None


def _load_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _pct(a, b):
    return round(100.0 * a / b, 1) if b else 0.0


class Diagnoser:
    def __init__(self, analytics):
        self.a = analytics

    # ------------------------------------------------------------------ drivers
    def drivers(self, f):
        a = self.a
        w, p = a.where(f)
        t = a.one(f"""SELECT COUNT(*) n, COUNT(DISTINCT r.session_id) sessions,
            SUM(r.input_tokens) i, SUM(r.output_tokens) o, SUM(r.cache_read_tokens) cr,
            SUM(r.cache_write_tokens) cw, SUM(r.billable_tokens) b, SUM(r.est_cost_usd) c,
            AVG(r.context_tokens) ctx, SUM(r.is_sidechain) side,
            SUM(CASE WHEN r.is_sidechain=1 THEN r.billable_tokens ELSE 0 END) side_tok
            FROM requests r WHERE {w}""", p)
        if not t.get("n"):
            return {"total": t, "drivers": []}
        b = t["b"] or 1
        out = []

        # 1. context re-reading: every request re-sends the whole conversation
        out.append({
            "key": "context_reread", "share_pct": _pct(t["cr"], b),
            "title": "Conversation history re-read on every request",
            "detail": f"{_pct(t['cr'], b)}% of tokens are cache reads — the whole context "
                      f"(avg {int(t['ctx'] or 0):,} tokens) is re-sent each time Claude takes a "
                      f"step. {t['n']:,} requests × that context is where the volume comes from.",
            "basis": "actual"})

        # 2. long sessions
        thr = a.settings["waste_rules"].get("large_context_tokens", 100000)
        ls = a.one(f"""SELECT COUNT(*) n, SUM(b) b FROM (SELECT r.session_id, COUNT(*) k,
            SUM(r.billable_tokens) b, MAX(r.context_tokens) mx FROM requests r WHERE {w}
            GROUP BY r.session_id HAVING k >= 150 OR mx >= ?)""", p + [thr])
        out.append({
            "key": "long_sessions", "share_pct": _pct(ls["b"] or 0, b),
            "title": "Long-running sessions",
            "detail": f"{ls['n'] or 0} sessions ran 150+ steps or passed {thr // 1000}K context; "
                      f"they hold {_pct(ls['b'] or 0, b)}% of all tokens. Each step late in a "
                      f"session costs several times an early one.",
            "basis": "actual"})

        # 3. frontier model share
        frontier = [m for m, v in a.pricing.models.items() if v.get("tier") == "frontier"]
        if frontier:
            ph = ",".join("?" * len(frontier))
            fr = a.one(f"SELECT SUM(r.billable_tokens) b, SUM(r.est_cost_usd) c FROM requests r "
                       f"WHERE {w} AND r.model IN ({ph})", p + frontier)
            out.append({
                "key": "frontier_model", "share_pct": _pct(fr["b"] or 0, b),
                "title": "Frontier-tier (Opus) usage",
                "detail": f"{_pct(fr['b'] or 0, b)}% of tokens and {_pct(fr['c'] or 0, t['c'])}% "
                          f"of estimated cost ran on frontier models. Tokens are the same "
                          f"count on Sonnet/Haiku, but cheaper.",
                "basis": "actual tokens, estimated cost"})

        # 4. tool output flooding context
        tools = a.q(f"""SELECT tc.name, COUNT(*) calls, SUM(r.billable_tokens) b
            FROM tool_calls tc JOIN requests r ON r.id=tc.request_pk WHERE {w}
            GROUP BY tc.name ORDER BY b DESC LIMIT 6""", p)
        if tools:
            top = tools[0]
            out.append({
                "key": "tool_heavy", "share_pct": _pct(top["b"], b),
                "title": f"Tool-driven steps ({top['name']} leads)",
                "detail": "Requests that issued a tool call, by tool: " + ", ".join(
                    f"{x['name']} {x['calls']:,}× ({_pct(x['b'], b)}%)" for x in tools[:5])
                    + ". Every tool call adds a round trip that re-reads the context, and "
                      "its output stays in context for the rest of the session.",
                "evidence": tools, "basis": "actual"})

        # 5. subagents
        if t["side"]:
            out.append({
                "key": "subagents", "share_pct": _pct(t["side_tok"] or 0, b),
                "title": "Subagents",
                "detail": f"{t['side']:,} requests ({_pct(t['side_tok'] or 0, b)}% of tokens) came "
                          f"from subagents. Each one starts with its own system prompt and "
                          f"CLAUDE.md load.",
                "basis": "actual"})

        out.sort(key=lambda d: -d["share_pct"])
        return {"total": t, "drivers": out}

    # ------------------------------------------------------------------ trend
    def trend(self, f):
        """Last 7 days vs the 7 before, split into volume vs size-per-step."""
        a = self.a
        if not a.last_day:
            return None
        end = _d(f.get("end") or a.last_day)
        ranges = {"current": (end - timedelta(days=6), end),
                  "previous": (end - timedelta(days=13), end - timedelta(days=7))}
        res = {}
        for k, (s, e) in ranges.items():
            ff = dict(f, start=s.isoformat(), end=e.isoformat())
            w, p = a.where(ff)
            r = a.one(f"""SELECT COUNT(*) n, COALESCE(SUM(r.billable_tokens),0) b,
                COALESCE(SUM(r.est_cost_usd),0) c, AVG(r.context_tokens) ctx,
                COUNT(DISTINCT r.session_id) sessions FROM requests r WHERE {w}""", p)
            r["per_request"] = (r["b"] / r["n"]) if r["n"] else 0
            r["start"], r["end"] = s.isoformat(), e.isoformat()
            res[k] = r
        c, pv = res["current"], res["previous"]
        if pv["b"] and c["n"] and pv["n"]:
            vol = c["n"] / pv["n"]
            size = c["per_request"] / pv["per_request"] if pv["per_request"] else 1
            res["change_pct"] = round(100.0 * (c["b"] / pv["b"] - 1), 1)
            res["volume_factor"] = round(vol, 2)
            res["size_factor"] = round(size, 2)
            res["explanation"] = (
                f"Tokens {'up' if c['b'] >= pv['b'] else 'down'} {abs(res['change_pct'])}% week "
                f"over week: {vol:.2f}× as many requests, each {size:.2f}× the size "
                f"({int(pv['per_request']):,} → {int(c['per_request']):,} tokens/request).")
        return res

    # ------------------------------------------------------------------ recommendations
    def recommendations(self, f, drv, projects):
        a = self.a
        v = getattr(self, "v", VOCAB["claude"])
        recs = []
        d = {x["key"]: x for x in drv["drivers"]}
        t = drv["total"]
        if not t.get("n"):
            return recs

        if d.get("context_reread", {}).get("share_pct", 0) > 60:
            recs.append({
                "priority": 1, "title": "Clear or compact between tasks",
                "why": d["context_reread"]["detail"],
                "how": f"Run {v['clear']} when you switch to an unrelated task, and {v['compact']} once a "
                       "session passes ~100K context. Start one session per ticket rather than "
                       "one per day.",
                # No dollar figure: the old one assumed 30% fewer cache-read tokens, a
                # number that came from nowhere in the data.
                "est_savings_usd": None,
                "savings_basis": None})
        if d.get("long_sessions", {}).get("share_pct", 0) > 30:
            recs.append({
                "priority": 1, "title": "Break up marathon sessions",
                "why": d["long_sessions"]["detail"],
                "how": f"Finish a unit of work, write the state to a file or memory, then {v['clear']}. "
                       "Use the Sessions view sorted by cost to find the worst ones.",
                "est_savings_usd": None, "savings_basis": None})
        fm = d.get("frontier_model")
        if fm and fm["share_pct"] > 70:
            # No dollar figure: the repriced model-switch estimate was removed because it
            # held turn counts fixed and ignored the per-model cache. The observation
            # (most spend is on the frontier model) stands; the saving is not knowable
            # without running the work on both models.
            recs.append({
                "priority": 2, "title": v["cheaper"],
                "why": fm["detail"],
                "how": v["model_how"],
                "est_savings_usd": None,
                "savings_basis": None})
        th = d.get("tool_heavy")
        if th and th.get("evidence"):
            bash = next((x for x in th["evidence"] if x["name"] in v["shell"]), None)
            if bash and bash["calls"] > 500:
                recs.append({
                    "priority": 2, "title": "Cap noisy shell output",
                    "why": f"{bash['name']} ran {bash['calls']:,} times; long command output sits in "
                           f"context for the rest of the session.",
                    "how": "Ask for `| head`/`| tail`, quiet flags (`-q`, `--silent`), and run "
                           "test suites with a reporter that prints failures only. Add these "
                           "habits to " + v["md"] + " so the agent does it unprompted.",
                    "est_savings_usd": None, "savings_basis": None})
            mcp = [x for x in th["evidence"] if x["name"].startswith("mcp__")]
            if mcp:
                recs.append({
                    "priority": 3, "title": "Browser/MCP tools return large payloads",
                    "why": "Top MCP tools by tokens: " + ", ".join(
                        f"{x['name'].split('__')[-1]} {x['calls']}×" for x in mcp[:3]),
                    "how": "Prefer targeted reads (find / get_page_text) over screenshots, and "
                           "do browser checks in a subagent so the payload doesn't stay in the "
                           "main session.",
                    "est_savings_usd": None, "savings_basis": None})
        sub = d.get("subagents")
        if sub and sub["share_pct"] > 25:
            recs.append({
                "priority": 3, "title": "Subagents are a large share",
                "why": sub["detail"],
                "how": "Use subagents for wide searches only; for known files, read them "
                       "directly. Set `model: sonnet` or `haiku` in custom agent frontmatter.",
                "est_savings_usd": None, "savings_basis": None})
        n_fix = sum(1 for pj in projects if pj["issues"])
        if n_fix:
            recs.append({
                "priority": 2, "title": f"{n_fix} project(s) need {v['md']} / config changes",
                "why": "Missing or oversized CLAUDE.md, oversized memory, repeated file "
                       "re-reads or reads of generated files — see the project table below.",
                "how": "Apply the per-project fixes listed below.",
                "est_savings_usd": None, "savings_basis": None})
        recs.sort(key=lambda r: (r["priority"], -(r["est_savings_usd"] or 0)))
        return recs

    def _main_model(self, f):
        w, p = self.a.where(f)
        r = self.a.one(f"SELECT r.model m FROM requests r WHERE {w} GROUP BY r.model "
                       f"ORDER BY SUM(r.billable_tokens) DESC LIMIT 1", p)
        return r.get("m")

    # ------------------------------------------------------------------ config audit
    def _mcp_servers(self):
        cfg = _load_json(os.path.join(HOME, ".claude.json"))
        glob = list((cfg.get("mcpServers") or {}).keys())
        per = {k: list((v.get("mcpServers") or {}).keys())
               for k, v in (cfg.get("projects") or {}).items()}
        return glob, per

    def global_config(self):
        files = []
        for rel in ("CLAUDE.md", "settings.json", "settings.local.json"):
            p = os.path.join(CLAUDE_DIR, rel)
            if os.path.exists(p):
                files.append({"path": p, "tokens": _tok(p)})
        glob, _ = self._mcp_servers()
        agents = os.path.join(CLAUDE_DIR, "agents")
        issues = []
        g = next((x for x in files if x["path"].endswith("CLAUDE.md")), None)
        if g and g["tokens"] and g["tokens"] > CLAUDE_MD_WARN_TOKENS:
            issues.append({"severity": "medium", "title": "Global CLAUDE.md is large",
                           "fix": f"~{g['tokens']:,} tokens load into every session in every "
                                  f"project. Move project-specific parts into that project."})
        st = _load_json(os.path.join(CLAUDE_DIR, "settings.json"))
        if not st.get("model"):
            issues.append({"severity": "low", "title": "No default model set",
                           "fix": "Add \"model\": \"sonnet\" to ~/.claude/settings.json and "
                                  "switch to Opus with /model when a task needs it."})
        return {"files": files, "mcp_servers": glob,
                "custom_agents": sorted(os.listdir(agents)) if os.path.isdir(agents) else [],
                "default_model": st.get("model"), "issues": issues}

    def agent_global_config(self):
        """Codex / Gemini equivalent of global_config(): instructions file, default model, MCP."""
        v, issues, files, model, mcp = self.v, [], [], None, []
        base = os.path.join(HOME, ".codex" if self.agent == "codex" else ".gemini")
        for rel in (v["md"], "config.toml", "settings.json"):
            p = os.path.join(base, rel)
            if os.path.exists(p):
                files.append({"path": p, "tokens": _tok(p)})
        if self.agent == "codex":
            try:
                txt = open(os.path.join(base, "config.toml"), errors="replace").read()
            except OSError:
                txt = ""
            m = re.search(r'^\s*model\s*=\s*"([^"]+)"', txt, re.M)
            model = m.group(1) if m else None
            mcp = re.findall(r'^\s*\[mcp_servers\.([^\]]+)\]', txt, re.M)
            where = "model = \"...\" in ~/.codex/config.toml"
        else:
            st = _load_json(os.path.join(base, "settings.json"))
            model = (st.get("model") or {}).get("name") if isinstance(st.get("model"), dict) else st.get("model")
            mcp = list((st.get("mcpServers") or {}).keys())
            where = "\"model\": {\"name\": \"...\"} in ~/.gemini/settings.json"
        g = next((x for x in files if x["path"].endswith(v["md"])), None)
        if g and g["tokens"] and g["tokens"] > CLAUDE_MD_WARN_TOKENS:
            issues.append({"severity": "medium", "title": f"Global {v['md']} is large",
                           "fix": f"~{g['tokens']:,} tokens load into every session in every "
                                  f"project. Move project-specific parts into that project."})
        if not model:
            issues.append({"severity": "low", "title": "No default model set",
                           "fix": f"Set a cheaper everyday default with {where}; switch up with "
                                  f"/model when a task needs it."})
        return {"files": files, "mcp_servers": mcp, "custom_agents": [],
                "default_model": model, "issues": issues}

    def projects(self, f):
        a = self.a
        w, p = a.where(f)
        rows = a.q(f"""SELECT pj.id, pj.name, pj.path, pj.slug, pj.is_sandbox,
            COUNT(*) requests, SUM(r.billable_tokens) tokens, SUM(r.est_cost_usd) cost,
            AVG(r.context_tokens) avg_ctx, COUNT(DISTINCT r.session_id) sessions
            FROM requests r JOIN projects pj ON pj.id=r.project_id
            WHERE {w} AND pj.is_sandbox=0 GROUP BY pj.path ORDER BY tokens DESC LIMIT 25""", p)
        _, mcp_per = self._mcp_servers()
        mcp_used = {r["name"].split("__")[1] for r in a.q(
            "SELECT DISTINCT name FROM tool_calls WHERE name LIKE 'mcp__%'")}
        out = []
        for pj in rows:
            path = pj["path"]
            pids = [x["id"] for x in a.q("SELECT id FROM projects WHERE path=?", (path,))]
            ph = ",".join("?" * len(pids))
            exists = bool(path) and os.path.isdir(path)
            v = getattr(self, "v", VOCAB["claude"])
            claude = v is VOCAB["claude"]
            cm = [os.path.join(path, x) for x in v["md_paths"]] if path else []
            cm_found = [x for x in cm if os.path.exists(x)]
            cm_tok = sum(_tok(x) or 0 for x in cm_found)
            local = os.path.join(path, "CLAUDE.local.md") if path else None
            mem = os.path.join(CLAUDE_DIR, "projects", pj["slug"] or "", "memory", "MEMORY.md")
            mem_tok = _tok(mem) if os.path.exists(mem) else None
            settings = os.path.join(path, ".claude", "settings.json") if path else None
            s_json = _load_json(settings) if settings and os.path.exists(settings) else None

            ex = ",".join("?" * len(v["explore"])) or "NULL"
            sh = ",".join("?" * len(v["shell"]))
            calls = a.one(f"""SELECT COUNT(*) n,
                SUM(CASE WHEN name IN ({ex}) THEN 1 ELSE 0 END) explore
                FROM tool_calls WHERE project_id IN ({ph})""", list(v["explore"]) + pids)
            bash = a.q(f"SELECT target FROM tool_calls WHERE project_id IN ({ph}) AND name IN ({sh})",
                       pids + list(v["shell"]))
            explore = (calls["explore"] or 0) + sum(1 for b in bash if EXPLORE_BASH.match(b["target"] or ""))
            explore_pct = _pct(explore, calls["n"] or 0)
            reread = a.q(f"""SELECT path, COUNT(*) n, COUNT(DISTINCT session_id) s
                FROM files_touched WHERE project_id IN ({ph}) AND op='read'
                GROUP BY path HAVING s >= 3 ORDER BY n DESC LIMIT 5""", pids)
            noisy = a.q(f"""SELECT path, COUNT(*) n FROM files_touched
                WHERE project_id IN ({ph}) AND op='read' GROUP BY path""", pids)
            noisy = [x for x in noisy if NOISY_PATH.search(x["path"] or "")][:5]

            issues = []
            heavy = (pj["tokens"] or 0) > 20_000_000
            if exists and not cm_found and heavy:
                issues.append({"severity": "high", "title": f"No {v['md']}",
                               "fix": f"{explore_pct}% of tool calls here are exploration "
                                      f"(reads/greps/ls/find). Run /init in this repo, then add "
                                      f"build/test commands, folder map and conventions so the "
                                      f"agent stops rediscovering them each session."})
            elif exists and cm_found and explore_pct > 45 and heavy:
                issues.append({"severity": "medium", "title": "CLAUDE.md isn't saving exploration",
                               "fix": f"{explore_pct}% of tool calls are still exploration. Add a "
                                      f"folder map and where-things-live notes to CLAUDE.md."})
            if cm_tok > CLAUDE_MD_WARN_TOKENS:
                reload_cost = a.pricing.estimate(self._main_model(dict(f, projects=[str(i) for i in pids])),
                                                 cache_read=cm_tok * pj["requests"])
                issues.append({"severity": "medium", "title": f"CLAUDE.md is ~{cm_tok:,} tokens",
                               "fix": f"It's re-read on each of {pj['requests']:,} requests "
                                      f"(~${reload_cost:,.2f} est.). Trim to essentials (<"
                                      f"{CLAUDE_MD_WARN_TOKENS:,} tokens); move rarely-needed "
                                      f"detail to docs Claude reads on demand."})
            if mem_tok and mem_tok > MEMORY_WARN_TOKENS:
                issues.append({"severity": "low", "title": f"MEMORY.md is ~{mem_tok:,} tokens",
                               "fix": "Prune stale memories; keep the index to one line each."})
            if reread and exists:
                issues.append({"severity": "medium", "title": "Same files re-read across sessions",
                               "fix": "Summarise these in CLAUDE.md (purpose, key exports) so "
                                      "Claude doesn't open them every session: "
                                      + ", ".join(f"{os.path.relpath(x['path'], path) if x['path'].startswith(path) else x['path']}"
                                                  f" ({x['s']} sessions)" for x in reread[:3]),
                               "evidence": reread})
            if noisy and exists:
                issues.append({"severity": "medium", "title": "Generated/large files were read",
                               "fix": "Add deny rules to .claude/settings.json, e.g. "
                                      "\"permissions\": {\"deny\": [\"Read(./node_modules/**)\", "
                                      "\"Read(./dist/**)\", \"Read(./**/*.lock)\"]}. Seen: "
                                      + ", ".join(os.path.basename(x["path"]) for x in noisy[:3]),
                               "evidence": noisy})
            unused = [m for m in mcp_per.get(path, []) if m not in mcp_used] if claude else []
            if unused:
                issues.append({"severity": "low", "title": "Unused MCP servers enabled",
                               "fix": f"{', '.join(unused)} never called here but their tool "
                                      f"definitions load every session. Remove with "
                                      f"`claude mcp remove <name>`."})
            if claude and exists and cm_found:
                issues += self.claude_md_quality(path, cm_found)
            if claude:
                issues += self.memory_quality(pj["slug"], heavy)
            else:                                      # Claude-only files: don't flag them
                issues = [i for i in issues if not i["title"].startswith(("MEMORY.md", "Generated/large"))]
            if not exists:
                issues = []
            out.append({
                "name": pj["name"], "path": path, "exists": exists,
                "requests": pj["requests"], "sessions": pj["sessions"], "tokens": pj["tokens"],
                "cost": pj["cost"], "avg_context": pj["avg_ctx"], "explore_pct": explore_pct,
                "claude_md": {"paths": cm_found, "tokens": cm_tok},
                "claude_local_md": bool(local) and os.path.exists(local),
                "memory_tokens": mem_tok,
                "settings": {"exists": s_json is not None,
                             "deny_rules": len(((s_json or {}).get("permissions") or {}).get("deny") or [])},
                "mcp_servers": mcp_per.get(path, []),
                "issues": issues,
                "harness": self.harness(pj, path, exists, cm_found, s_json, explore_pct, pids, ph)
                if claude else None,
            })
        return out

    def harness(self, pj, path, exists, cm_found, s_json, explore_pct, pids, ph):
        """Does this project need a Claude Code harness, and which pieces are missing?

        Harness = CLAUDE.md + .claude/settings.json (permissions, hooks) + skills/commands/agents.
        Need is judged from actual usage here; presence is checked on disk now.
        """
        if not exists:
            return {"verdict": "unknown", "reasons": ["Project path is not on this machine"],
                    "have": [], "missing": []}
        dot = os.path.join(path, ".claude")
        s = s_json or {}
        perms = s.get("permissions") or {}

        def any_files(sub):
            d = os.path.join(dot, sub)
            return os.path.isdir(d) and any(not n.startswith(".") for n in os.listdir(d))

        pieces = [
            ("CLAUDE.md", bool(cm_found),
             "Run /init, then add build/test commands, a folder map and conventions."),
            ("Permissions", bool(perms.get("allow") or perms.get("deny")),
             "Add allow rules for safe commands you approve repeatedly and deny rules for "
             "node_modules/dist/lock files in .claude/settings.json."),
            ("Hooks", bool(s.get("hooks")),
             "Add a PostToolUse hook that runs the formatter/linter after edits, so Claude "
             "doesn't spend turns fixing style."),
            ("Skills / commands", any_files("skills") or any_files("commands"),
             "Turn the commands you repeat into a skill in .claude/skills/."),
            ("Subagents", any_files("agents"),
             "Add a subagent in .claude/agents/ (on a cheaper model) for exploration or review."),
        ]

        # usage signals (actual)
        sessions, tokens = pj["sessions"] or 0, pj["tokens"] or 0
        repeated = self.a.q(f"""SELECT target, COUNT(*) n, COUNT(DISTINCT session_id) s
            FROM tool_calls WHERE project_id IN ({ph}) AND name='Bash' AND target IS NOT NULL
            GROUP BY target HAVING s >= 3 ORDER BY s DESC, n DESC LIMIT 5""", pids)
        edits = self.a.one(f"""SELECT COUNT(*) n FROM tool_calls WHERE project_id IN ({ph})
            AND name IN ('Edit','Write','MultiEdit')""", pids)["n"] or 0

        reasons = []
        if sessions >= 5:
            reasons.append(f"{sessions} sessions: context is rebuilt from scratch every time")
        if tokens > 20_000_000:
            reasons.append(f"{tokens/1e6:,.0f}M tokens used here")
        if explore_pct > 40:
            reasons.append(f"{explore_pct}% of tool calls are exploration (Read/Grep/ls)")
        if repeated:
            reasons.append(f"{len(repeated)} shell commands repeated across 3+ sessions")
        if edits >= 50:
            reasons.append(f"{edits:,} edits: worth an auto-format/lint hook")

        wanted = {"CLAUDE.md", "Permissions"}
        if repeated:
            wanted.add("Skills / commands")
        if edits >= 50:
            wanted.add("Hooks")
        if explore_pct > 40 and tokens > 20_000_000:
            wanted.add("Subagents")

        have = [n for n, ok, _ in pieces if ok]
        missing = [{"piece": n, "fix": fix} for n, ok, fix in pieces if not ok and n in wanted]
        if repeated and any(m["piece"] == "Skills / commands" for m in missing):
            missing[[m["piece"] for m in missing].index("Skills / commands")]["fix"] += \
                " Repeated: " + ", ".join(f"`{r['target'][:60]}` ({r['s']} sessions)" for r in repeated[:3])

        light = sessions < 3 and tokens < 5_000_000
        if light:
            verdict = "not_needed"
            reasons = [f"Light use ({sessions} sessions, {tokens/1e6:.1f}M tokens): not worth setting up yet"]
            missing = []
        elif not missing:
            verdict = "in_place"
        elif len(missing) >= 2 or "CLAUDE.md" in [m["piece"] for m in missing]:
            verdict = "needed"
        else:
            verdict = "partial"
        return {"verdict": verdict, "reasons": reasons, "have": have, "missing": missing,
                "repeated_commands": repeated}


    # ------------------------------------------------------------------ breakdowns
    BUILTIN_CMDS = {"model", "compact", "clear", "doctor", "upgrade", "login", "logout", "config",
                    "help", "usage-credits", "rate-limit-options", "cost", "status", "resume",
                    "memory", "permissions", "mcp", "exit", "fast", "context", "agents", "hooks"}

    def breakdown(self, f=None):
        """Session / subagent / skill / MCP / connector attribution.

        injected tokens  = size of what the tool/skill put into context (chars/4, actual size)
        carried tokens   = injected x later main-thread requests in that session that
                           re-read it (upper bound: /compact is not visible in transcripts)
        """
        a = self.a
        f = f or {}
        w, p = a.where(f)
        rate = a.pricing.rates(self._main_model(f)).get("cache_read", 0.3) / 1e6
        tcw = f"tc.request_pk IN (SELECT r.id FROM requests r WHERE {w})"

        sessions = a.q(f"""SELECT r.session_id, s.title, pj.name project,
            SUM(CASE WHEN r.is_sidechain=0 THEN r.billable_tokens ELSE 0 END) main_tokens,
            SUM(CASE WHEN r.is_sidechain=1 THEN r.billable_tokens ELSE 0 END) sub_tokens,
            COUNT(DISTINCT r.agent_id) subagents, SUM(r.est_cost_usd) cost, COUNT(*) requests,
            MAX(r.context_tokens) max_ctx
            FROM requests r JOIN sessions s ON s.id=r.session_id JOIN projects pj ON pj.id=r.project_id
            WHERE {w} GROUP BY r.session_id ORDER BY main_tokens+sub_tokens DESC LIMIT 30""", p)
        if sessions:
            ids = [x["session_id"] for x in sessions]
            ph = ",".join("?" * len(ids))
            ext = defaultdict(lambda: defaultdict(set))
            for r in a.q(f"SELECT session_id, kind, server FROM tool_calls WHERE session_id IN ({ph})"
                         f" AND kind IN ('mcp','connector','skill')", ids):
                ext[r["session_id"]][r["kind"]].add(r["server"])
            for x in sessions:
                e = ext.get(x["session_id"], {})
                x["skills"] = sorted(e.get("skill", []))
                x["mcp"] = sorted(e.get("mcp", set()) | e.get("connector", set()))

        types = a.q(f"""SELECT r.agent_type, COUNT(DISTINCT r.agent_id) runs, COUNT(*) requests,
            SUM(r.billable_tokens) tokens, SUM(r.est_cost_usd) cost
            FROM requests r WHERE {w} AND r.is_sidechain=1 GROUP BY r.agent_type
            ORDER BY tokens DESC""", p)
        for t in types:
            t["tokens_per_run"] = t["tokens"] / t["runs"] if t["runs"] else None
        runs = a.q(f"""SELECT r.agent_id, r.agent_type, r.agent_desc, r.session_id,
            pj.name project, COUNT(*) requests, SUM(r.billable_tokens) tokens,
            SUM(r.est_cost_usd) cost, MIN(r.ts) ts
            FROM requests r JOIN projects pj ON pj.id=r.project_id
            WHERE {w} AND r.is_sidechain=1 AND r.agent_id IS NOT NULL
            GROUP BY r.agent_id ORDER BY tokens DESC LIMIT 20""", p)
        ret = {x["server"]: x for x in a.q(f"""SELECT server, SUM(result_chars)/{CHARS_PER_TOKEN} t
            FROM tool_calls tc WHERE kind='agent' AND {tcw} GROUP BY server""", p)}
        for t in types:
            t["returned_tokens"] = (ret.get(t["agent_type"]) or {}).get("t")

        def ext_rows(kinds):
            ph = ",".join("?" * len(kinds))
            rows = a.q(f"""SELECT tc.kind, tc.server, COUNT(*) calls,
                COUNT(DISTINCT tc.session_id) sessions,
                SUM(tc.result_chars)/{CHARS_PER_TOKEN} injected,
                SUM(tc.result_chars*1.0*tc.carry_requests)/{CHARS_PER_TOKEN} carried,
                MAX(tc.result_chars)/{CHARS_PER_TOKEN} largest
                FROM tool_calls tc WHERE tc.kind IN ({ph}) AND {tcw}
                GROUP BY tc.kind, tc.server ORDER BY carried DESC""", kinds + p)
            for r in rows:
                r["carried_cost"] = (r["carried"] or 0) * rate
                r["tools"] = a.q(f"""SELECT tc.name, COUNT(*) calls,
                    SUM(tc.result_chars)/{CHARS_PER_TOKEN} injected,
                    SUM(tc.result_chars*1.0*tc.carry_requests)/{CHARS_PER_TOKEN} carried
                    FROM tool_calls tc WHERE tc.server=? AND tc.kind=? AND {tcw}
                    GROUP BY tc.name ORDER BY carried DESC LIMIT 8""", [r["server"], r["kind"]] + p)
            return rows

        skills = ext_rows(["skill"])
        for s in skills:
            s["invoked_by"] = "Claude"
        # user-typed slash commands / skills: attribute the whole turn
        slash = a.q(f"""SELECT substr(pr.source, 8) server, COUNT(DISTINCT pr.id) calls,
            COUNT(DISTINCT pr.session_id) sessions, SUM(r.billable_tokens) turn_tokens,
            SUM(r.est_cost_usd) turn_cost
            FROM prompts pr JOIN requests r ON r.prompt_id=pr.id
            WHERE {w} AND pr.source LIKE 'slash:/%' GROUP BY pr.source ORDER BY turn_tokens DESC""", p)
        for s in slash:
            s["builtin"] = s["server"] in self.BUILTIN_CMDS

        mcp = ext_rows(["mcp", "connector"])
        glob, per = self._mcp_servers()
        configured = set(glob) | {m for v in per.values() for m in v}
        used = {r["server"] for r in a.q("SELECT DISTINCT server FROM tool_calls WHERE kind IN ('mcp','connector')")}
        return {
            "sessions": sessions,
            "subagents": {"types": types, "runs": runs},
            "skills": skills, "slash_commands": slash,
            "mcp": [r for r in mcp if r["kind"] == "mcp"],
            "connectors": [r for r in mcp if r["kind"] == "connector"],
            "configured_unused_mcp": sorted(configured - used)
            if "claude" in ((f or {}).get("agents") or ["claude"]) else [],
            "cache_read_rate_per_mtok": rate * 1e6,
            "note": "Injected = size of what came back into context (actual size, ~4 chars/token). "
                    "Carried = injected × later requests in the same session that re-read it — an "
                    "upper bound, since /compact isn't visible. Carried cost prices those re-reads at "
                    "your main model's cache-read rate (estimated). Subagent tokens are actual. "
                    "Slash-command figures attribute the whole turn they started.",
        }


    # ------------------------------------------------------------------ session health
    LIVE_WINDOW_S = 20 * 60
    CTX_WARN, CTX_CRIT = 150_000, 300_000

    def live_sessions(self):
        """Transcripts written in the last 20 minutes, read straight from disk."""
        import time
        now, out = time.time(), []
        root = self.a.meta.get("source_dir") or os.path.join(CLAUDE_DIR, "projects")
        for dirpath, _, names in os.walk(root):
            if os.path.basename(dirpath) == "subagents":
                continue
            for n in names:
                fp = os.path.join(dirpath, n)
                if not n.endswith(".jsonl") or now - os.path.getmtime(fp) > self.LIVE_WINDOW_S:
                    continue
                steps, last, first_ctx, cwd, title, tokens = 0, None, None, None, None, 0
                with open(fp, errors="replace") as fh:
                    for line in fh:
                        try:
                            r = json.loads(line)
                        except ValueError:
                            continue
                        cwd = cwd or r.get("cwd")
                        if r.get("type") == "ai-title":
                            title = r.get("aiTitle")
                        u = (r.get("message") or {}).get("usage") if r.get("type") == "assistant" else None
                        if u:
                            ctx = (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) \
                                + (u.get("cache_creation_input_tokens") or 0)
                            steps += 1
                            tokens += ctx + (u.get("output_tokens") or 0)
                            first_ctx = first_ctx or ctx
                            last = ctx
                if not last:
                    continue
                sev = "high" if last >= self.CTX_CRIT else "medium" if last >= self.CTX_WARN else "ok"
                out.append({
                    "session_id": n[:-6], "title": title, "project": os.path.basename(cwd or dirpath),
                    "idle_min": round((now - os.path.getmtime(fp)) / 60, 1), "steps": steps,
                    "context": last, "start_context": first_ctx, "tokens": tokens, "severity": sev,
                    "advice": ("Context is very large: every step re-reads ~%s tokens. Run /compact now, "
                               "or save state to a file and /clear." % f"{last:,}") if sev == "high" else
                              ("Getting heavy. /compact at the next natural break; /clear if the "
                               "next task is unrelated.") if sev == "medium" else "Healthy.",
                })
        out.sort(key=lambda x: -x["context"])
        return out

    def session_health(self, f=None, limit=15):
        """Past sessions that carried too much context, with specific fixes."""
        a = self.a
        f = f or {}
        w, p = a.where(f)
        base = 100_000
        rows = a.q(f"""SELECT r.session_id, s.title, pj.name project, COUNT(*) steps,
            MAX(r.context_tokens) peak, AVG(r.context_tokens) avg_ctx,
            SUM(r.billable_tokens) tokens, SUM(r.est_cost_usd) cost,
            COALESCE(SUM(CASE WHEN r.context_tokens > {base} THEN r.context_tokens - {base} ELSE 0 END), 0) over_base,
            SUM(CASE WHEN r.context_tokens > {self.CTX_WARN} THEN 1 ELSE 0 END) heavy_steps,
            COUNT(DISTINCT r.prompt_id) prompts, SUM(r.is_sidechain) side
            FROM requests r JOIN sessions s ON s.id=r.session_id JOIN projects pj ON pj.id=r.project_id
            WHERE {w} GROUP BY r.session_id HAVING peak >= ? ORDER BY over_base DESC LIMIT ?""",
                   p + [self.CTX_WARN, limit])
        compacts = {r["session_id"]: r["n"] for r in a.q(
            "SELECT session_id, COUNT(*) n FROM prompts WHERE source='slash:/compact' GROUP BY 1")}
        for r in rows:
            sid = r["session_id"]
            fixes = []
            # A session with no priced requests sums to NULL, not 0 — and one
            # NULL used to take the whole diagnose page down with a 500.
            over = r["over_base"] or 0
            r["over_base"] = over
            r["tokens_above_100k"] = over     # re-read above a 100K baseline; not a saving
            if not compacts.get(sid):
                fixes.append(f"Never compacted. {r['heavy_steps']:,} steps ran above "
                             f"{self.CTX_WARN // 1000}K context; /compact (or /clear between the "
                             f"{r['prompts']} prompts) would have kept it near {base // 1000}K.")
            if r["prompts"] >= 15:
                fixes.append(f"{r['prompts']} prompts in one session. Split by task: one session "
                             f"per ticket/feature.")
            reread = a.q("""SELECT path, COUNT(*) n FROM files_touched WHERE session_id=? AND op='read'
                GROUP BY path HAVING n >= 3 ORDER BY n DESC LIMIT 3""", (sid,))
            if reread:
                fixes.append("Same files read again and again: " + ", ".join(
                    f"{os.path.basename(x['path'])} ×{x['n']}" for x in reread)
                    + ". Key facts about them belong in CLAUDE.md.")
            big = a.q(f"""SELECT name, target, result_chars/{CHARS_PER_TOKEN} t FROM tool_calls
                WHERE session_id=? AND result_chars > 40000 ORDER BY result_chars DESC LIMIT 3""", (sid,))
            if big:
                fixes.append("Large tool outputs stayed in context: " + "; ".join(
                    f"{x['name'].split('__')[-1]} ~{x['t']:,} tok" for x in big)
                    + ". Trim output (head/grep/quiet flags) or run it in a subagent.")
            if not r["side"] and r["steps"] > 300:
                fixes.append("No subagents used. Send wide searches/research to an Explore subagent "
                             "so the result, not the search, lands in this context.")
            r["fixes"] = fixes
        return rows

    # ------------------------------------------------------------------ CLAUDE.md / memory quality
    PATH_REF = re.compile(r"`((?:\.{0,2}/)?[\w.-]+(?:/[\w.-]+)+/?)`")
    CMD_HINT = re.compile(r"\b(npm|yarn|pnpm|npx|make|pytest|python3? -m|go test|cargo|gradle|mvn|"
                          r"docker|\./[\w-]+\.sh)\b")

    def claude_md_quality(self, path, files):
        issues = []
        text = ""
        for fp in files:
            with open(fp, errors="ignore") as fh:
                text += fh.read() + "\n"
        if not text.strip():
            return issues
        if not self.CMD_HINT.search(text):
            issues.append({"severity": "medium", "title": "CLAUDE.md has no build/test commands",
                           "fix": "Add the exact commands to install, run, test and lint. Claude "
                                  "otherwise rediscovers them from package.json/Makefile each time."})
        # only refs to concrete files; a ref is stale if no file in the repo ends with it
        refs = {m.lstrip("./") for m in self.PATH_REF.findall(text)
                if re.search(r"\.\w{1,5}(:\d+)?$", m) and not m.startswith(("http", "~", "/"))}
        refs = {re.sub(r":\d+$", "", m) for m in refs}
        known = []
        for dp, dns, fns in os.walk(path):
            dns[:] = [d for d in dns if d not in ("node_modules", ".git", "dist", "build", ".next")]
            known += [os.path.relpath(os.path.join(dp, n), path) for n in fns]
            if len(known) > 60000:
                break
        dead = [m for m in refs if not any(k == m or k.endswith("/" + m) for k in known)]
        if dead:
            issues.append({"severity": "medium", "title": f"{len(dead)} stale path(s) in CLAUDE.md",
                           "fix": "These paths no longer exist, so Claude chases them: "
                                  + ", ".join(sorted(dead)[:5])})
        lines = [l.strip() for l in text.splitlines() if len(l.strip()) > 30]
        dup = {l for l in lines if lines.count(l) > 1}
        if dup:
            issues.append({"severity": "low", "title": f"{len(dup)} duplicated line(s) in CLAUDE.md",
                           "fix": "Remove repeats; each one is paid for on every request."})
        return issues

    def memory_quality(self, slug, heavy):
        d = os.path.join(CLAUDE_DIR, "projects", slug or "", "memory")
        idx = os.path.join(d, "MEMORY.md")
        issues = []
        if not os.path.exists(idx):
            if heavy:
                issues.append({"severity": "low", "title": "No auto-memory yet",
                               "fix": "Tell Claude \"remember …\" for preferences you keep repeating "
                                      "(see Prompt-derived suggestions); it writes them to memory."})
            return issues
        with open(idx, errors="ignore") as fh:
            lines = [l for l in fh.read().splitlines() if l.strip()]
        long = [l for l in lines if len(l) > 200]
        if long:
            issues.append({"severity": "low", "title": f"{len(long)} long MEMORY.md index line(s)",
                           "fix": "The index loads every session; keep each line to a short hook "
                                  "and put detail in the linked file."})
        linked = set(re.findall(r"\]\(([^)]+\.md)\)", "\n".join(lines)))
        files = {n for n in os.listdir(d) if n.endswith(".md") and n != "MEMORY.md"}
        if linked - files:
            issues.append({"severity": "medium", "title": "MEMORY.md links to missing files",
                           "fix": ", ".join(sorted(linked - files)[:5])})
        if files - linked:
            issues.append({"severity": "low", "title": f"{len(files - linked)} memory file(s) not indexed",
                           "fix": "Unindexed memories are never recalled: " + ", ".join(sorted(files - linked)[:5])})
        return issues

    # ------------------------------------------------------------------ prompt-derived memory
    INSTRUCTION = re.compile(r"\b(always|never|don'?t|do not|avoid|make sure|remember|prefer|"
                             r"use|only|must|should|reply|answer|in english|crisp|short|commit|"
                             r"mat|nahi|hamesha|kabhi|karo|krna|chahiye)\b", re.I)
    SPLIT = re.compile(r"(?<=[.!?\n])\s+|\s*[;\n]\s*")
    PATHISH = re.compile(r"(?:~|/Users/|https?://)[^\s'\"`)]+")

    THEMES = [
        ("git", "Git/commit rules (co-author, push, branch)",
         r"\b(commit|push|co-?authou?red|coauthor|cherry.?pick|branch|pr\b)"),
        ("language", "Reply language", r"\b(english|hindi|hinglish)\b"),
        ("brevity", "Answer length/style", r"\b(crisp|concise|short|brief|to the point|chota|detail me)\b"),
        ("scope", "Keep changes small / don't redesign", r"\b(don'?t|dont|mt|mat) (redesign|change|touch|break)|\bonly (fix|change)\b|theek (kr|kar)"),
        ("tests", "How to test / verify", r"\b(run (the )?tests?|playwright|e2e|screenshot|verify)\b"),
        ("secrets", "Credentials pasted in prompts", r"(://[^\s/:]+:[^\s@]+@|password\s*[=:]|api[_-]?key\s*[=:]|token\s*[=:])"),
    ]
    SECRET = re.compile(r"(://[^\s/:]+:)[^\s@]+@|((?:password|passwd|secret|token|api[_-]?key)\s*[=:]\s*)\S+", re.I)

    def _redact(self, t):
        return self.SECRET.sub(lambda m: (m.group(1) + "***@") if m.group(1) else (m.group(2) + "***"), t)

    def _norm(self, t):
        t = re.sub(r"[^\w\s/'-]", " ", t.lower())
        t = re.sub(r"\d+", "#", t)
        return re.sub(r"\s+", " ", t).strip()

    def memory_suggestions(self, f=None):
        a = self.a
        f = f or {}
        w, p = a.where(f)
        prompts = a.q(f"""SELECT pr.id, pr.text, pr.session_id, pj.path, pj.slug
            FROM prompts pr JOIN projects pj ON pj.id=pr.project_id
            WHERE pr.id IN (SELECT DISTINCT r.prompt_id FROM requests r WHERE {w})
              AND pj.is_sandbox=0 AND pr.char_len <= 1500
              AND (pr.source IS NULL OR pr.source NOT LIKE 'slash:%')
              AND pr.char_len BETWEEN 3 AND 4000 AND pr.text NOT LIKE '<%'
              AND pr.text NOT LIKE 'You are %'""", p)
        clauses = defaultdict(lambda: {"sessions": set(), "examples": [], "projects": Counter(),
                                       "slugs": Counter()})
        paths = defaultdict(lambda: {"sessions": set(), "projects": Counter(), "slugs": Counter()})
        prefixes = defaultdict(lambda: {"sessions": set(), "example": None, "projects": Counter()})
        themes = defaultdict(lambda: {"sessions": set(), "examples": [], "projects": Counter(),
                                      "slugs": Counter()})
        for pr in prompts:
            text = pr["text"]
            for c in self.SPLIT.split(text):
                c = c.strip()
                n = self._norm(c)
                if not (3 <= len(n.split()) <= 25) or not self.INSTRUCTION.search(n):
                    continue
                e = clauses[n]
                e["sessions"].add(pr["session_id"])
                e["projects"][pr["path"]] += 1
                e["slugs"][pr["slug"]] += 1
                if len(e["examples"]) < 2 and c not in e["examples"]:
                    e["examples"].append(c[:200])
            low = text.lower()
            for key, label, rx in self.THEMES:
                if re.search(rx, low):
                    e = themes[key]
                    e["sessions"].add(pr["session_id"])
                    e["projects"][pr["path"]] += 1
                    e["slugs"][pr["slug"]] += 1
                    if len(e["examples"]) < 3:
                        e["examples"].append(text.strip()[:160])
            for m in set(self.PATHISH.findall(text)):
                m = m.rstrip(".,:")
                if (pr["path"] and m.startswith(pr["path"])) or "/T/" in m or "/tmp/" in m:
                    continue          # inside the repo: Claude finds these itself
                e = paths[m]
                e["sessions"].add(pr["session_id"])
                e["projects"][pr["path"]] += 1
            if len(text) > 300:
                k = self._norm(text[:160])
                e = prefixes[k]
                e["sessions"].add(pr["session_id"])
                e["projects"][pr["path"]] += 1
                e["example"] = e["example"] or text[:220]

        known_cache = {}

        def known_text(path, slug):
            if (path, slug) not in known_cache:
                t = ""
                cands = [os.path.join(CLAUDE_DIR, "CLAUDE.md")]
                if path:
                    cands += [os.path.join(path, "CLAUDE.md"), os.path.join(path, ".claude", "CLAUDE.md"),
                              os.path.join(path, "CLAUDE.local.md")]
                md = os.path.join(CLAUDE_DIR, "projects", slug or "", "memory")
                if os.path.isdir(md):
                    cands += [os.path.join(md, n) for n in os.listdir(md)]
                for fp in cands:
                    if os.path.isfile(fp):
                        with open(fp, errors="ignore") as fh:
                            t += fh.read().lower() + "\n"
                known_cache[(path, slug)] = self._norm(t)
            return known_cache[(path, slug)]

        def covered(n, path, slug):
            kt = known_text(path, slug)
            words = [x for x in n.split() if len(x) > 3]
            return bool(words) and sum(1 for x in words if x in kt) / len(words) >= 0.7

        out = []
        labels = {k: l for k, l, _ in self.THEMES}
        for key, e in themes.items():
            if len(e["sessions"]) < 2:
                continue
            multi = len(e["projects"]) > 1
            if key == "secrets":
                out.append({"kind": "security", "text": labels[key], "sessions": len(e["sessions"]),
                            "projects": [os.path.basename(x or "") for x in e["projects"]],
                            "examples": [self._redact(x) for x in e["examples"]],
                            "target": "An env file / secret manager — not prompts or memory",
                            "why": f"Credentials appear in prompts in {len(e['sessions'])} sessions; "
                                   f"they are now stored in plain text in your transcripts."})
                continue
            path = e["projects"].most_common(1)[0][0]
            slug = e["slugs"].most_common(1)[0][0]
            rx = dict((k, r) for k, _, r in self.THEMES)[key]
            in_mem = bool(re.search(rx, known_text(path, slug)))
            out.append({"kind": "theme", "text": labels[key], "already_saved": in_mem, "sessions": len(e["sessions"]),
                        "projects": [os.path.basename(x or "") for x in e["projects"]],
                        "examples": [self._redact(x) for x in e["examples"]],
                        "target": "~/.claude/CLAUDE.md or memory (applies everywhere)" if multi
                                  else "that project's CLAUDE.md or memory",
                        "why": (f"Already in CLAUDE.md/memory, yet you repeated it in "
                                f"{len(e['sessions'])} sessions — make the rule more explicit, or "
                                f"update it if your preference changed.") if in_mem else
                               (f"You gave this kind of instruction in {len(e['sessions'])} sessions. "
                                f"Check the examples; if it's a standing rule, write it down once.")})
        for n, e in clauses.items():
            if len(e["sessions"]) < 2:
                continue
            path = e["projects"].most_common(1)[0][0]
            slug = e["slugs"].most_common(1)[0][0]
            if covered(n, path, slug):
                continue
            multi = len(e["projects"]) > 1
            out.append({"kind": "instruction", "text": self._redact(e["examples"][0]), "sessions": len(e["sessions"]),
                        "projects": [os.path.basename(x or "") for x in e["projects"]],
                        "target": "~/.claude/CLAUDE.md (applies everywhere)" if multi
                                  else f"{os.path.basename(path or '')}: CLAUDE.md or memory",
                        "why": f"You typed this in {len(e['sessions'])} different sessions."})
        for m, e in paths.items():
            if len(e["sessions"]) < 2 or "/browse/" in m or (
                    not m.startswith("http") and not os.path.exists(os.path.expanduser(m))):
                continue
            path = e["projects"].most_common(1)[0][0]
            out.append({"kind": "reference", "text": self._redact(m), "sessions": len(e["sessions"]),
                        "projects": [os.path.basename(x or "") for x in e["projects"]],
                        "target": f"{os.path.basename(path or '')}: CLAUDE.md (\"Related locations\")",
                        "why": f"Pasted into prompts in {len(e['sessions'])} sessions; note what it is "
                               f"once so you can refer to it by name."})
        for k, e in prefixes.items():
            if len(e["sessions"]) < 2:
                continue
            out.append({"kind": "template", "text": self._redact(e["example"]), "sessions": len(e["sessions"]),
                        "projects": [os.path.basename(x or "") for x in e["projects"]],
                        "target": "A skill or slash command (.claude/skills/<name>/SKILL.md)",
                        "why": f"Same long prompt opening reused in {len(e['sessions'])} sessions; "
                               f"make it a /command instead of pasting it."})
        out.sort(key=lambda x: -x["sessions"])
        return out[:40]

    # ------------------------------------------------------------------ entry point
    def _agent(self, f):
        """The agent the advice is written for: the one that spent most tokens in range."""
        w, p = self.a.where(f)
        r = self.a.one(f"SELECT r.agent a FROM requests r WHERE {w} AND r.agent IN "
                       f"('claude','codex','gemini') GROUP BY r.agent "
                       f"ORDER BY SUM(r.billable_tokens) DESC LIMIT 1", p)
        return r.get("a") or "claude"

    def run(self, f=None):
        f = f or {}
        self.agent = self._agent(f)
        self.v = VOCAB[self.agent]
        drv = self.drivers(f)
        tr = self.trend(f)
        pj = self.projects(f)
        gl = self.global_config() if self.agent == "claude" else self.agent_global_config()
        recs = self.recommendations(f, drv, pj)
        top = drv["drivers"][:3]
        headline = ("Your tokens are high mainly because of: "
                    + "; ".join(f"{d['title'].lower()} ({d['share_pct']}%)" for d in top)
                    + ".") if top else "No usage in the selected range."
        from .playbook import attach
        wants = f.get("agents") or ["claude"]
        res = attach({"headline": headline, "trend": tr, **drv, "recommendations": recs,
                "live_sessions": self.live_sessions() if "claude" in wants else [], "session_health": self.session_health(f),
                "memory_suggestions": self.memory_suggestions(f),
                "projects": pj, "global": gl,
                "note": "Token counts are actual. Dollar figures are estimated. Config checks "
                        f"read files on disk now; token sizes use ~{CHARS_PER_TOKEN} chars/token.",
                "basis": "mixed", "agent": self.agent, "vocab": {k: v for k, v in self.v.items()
                                                                  if isinstance(v, str)}})
        return res if self.agent == "claude" else _swap_md(res, self.v["md"])


def _swap_md(obj, md):
    """Advice text for a non-Claude agent: point at its instructions file, not CLAUDE.md."""
    if isinstance(obj, str):
        return obj.replace("CLAUDE.md", md).replace("Claude Code", "the agent")
    if isinstance(obj, list):
        return [_swap_md(x, md) for x in obj]
    if isinstance(obj, dict):
        return {k: (x if k in ("path", "paths", "session_id") else _swap_md(x, md)) for k, x in obj.items()}
    return obj
