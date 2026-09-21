"""Loaders for other coding agents on this machine, into the same warehouse as Claude Code.

Each agent's rows carry `agent` so every view can be filtered to one agent or several.
Only what the agent actually records is loaded; missing fields stay NULL/0 and the UI
says so (see AGENTS[...]["data"]).

  codex   ~/.codex/sessions/**/rollout-*.jsonl    tokens per turn, model, prompts, tool calls
  gemini  ~/.gemini/tmp/<hash>/chats/*.json        tokens per reply, model, prompts
  cursor  ~/.cursor/projects/*/agent-transcripts   prompts + tool calls (no tokens, no model)
          Cursor IDE state.vscdb                   tokens on some replies (no model)
"""
import glob
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
IS_WIN, IS_MAC = os.name == "nt", sys.platform == "darwin"


def _cursor_state_db():
    if IS_MAC:
        base = os.path.join(HOME, "Library", "Application Support", "Cursor")
    elif IS_WIN:
        base = os.path.join(os.environ.get("APPDATA", ""), "Cursor")
    else:
        base = os.path.join(HOME, ".config", "Cursor")
    return os.path.join(base, "User", "globalStorage", "state.vscdb")


AGENTS = {
    "claude": {"name": "Claude Code", "data": "full",
               "note": "Tokens, model, cost, prompts and tool calls per request."},
    "codex": {"name": "Codex", "data": "tokens",
              "paths": [os.path.join(HOME, ".codex", "sessions")],
              "note": "Tokens and model per turn. Cost estimated at OpenAI API list prices; "
                      "on a ChatGPT plan you don't pay per token."},
    "gemini": {"name": "Gemini CLI", "data": "tokens",
               "paths": [os.path.join(HOME, ".gemini", "tmp")],
               "note": "Tokens and model per reply. Cost estimated at Gemini API list prices; "
                       "the free tier costs nothing."},
    "cursor": {"name": "Cursor", "data": "activity",
               "paths": [os.path.join(HOME, ".cursor", "projects"), _cursor_state_db()],
               "note": "Prompts and tool calls from agent transcripts; token counts only where "
                       "the Cursor IDE stored them, with no model. Cursor bills by subscription, "
                       "so no cost is estimated."},
}


def detect():
    """Which agents have data here (Claude is always listed)."""
    out = {"claude": True}
    for k, a in AGENTS.items():
        if k != "claude":
            out[k] = any(os.path.exists(p) for p in a.get("paths", []))
    return out


def _day(ts):
    return (ts or "")[:10]


def _iso_ms(ms):
    try:
        return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OSError):
        return None


def _iso_mtime(path):
    return datetime.fromtimestamp(os.path.getmtime(path), timezone.utc).isoformat().replace("+00:00", "Z")


class AgentLoader:
    """Writes other agents' usage through the Claude Loader's tables and helpers."""

    def __init__(self, loader, log=print):
        self.L = loader
        self.db = loader.db
        self.log = log
        self.counts = {}

    def run(self):
        found = detect()
        for key, fn in (("codex", self.codex), ("gemini", self.gemini), ("cursor", self.cursor)):
            if not found.get(key):
                continue
            try:
                fn()
            except Exception as exc:   # one agent's odd data must not break the load
                self.log(f"  ! {key}: {exc}")
        return self.counts

    # ---------- shared writers ----------
    def project(self, agent, cwd, fallback):
        slug = f"{agent}:{cwd or fallback}"
        pid = self.L.project_id(slug, cwd)
        self.db.execute("UPDATE projects SET agent=? WHERE id=?", (agent, pid))
        if not cwd:
            self.db.execute("UPDATE projects SET name=? WHERE id=?", (fallback, pid))
        return pid

    def session(self, agent, sid, pid, src, title=None, version=None):
        self.db.execute("INSERT OR REPLACE INTO sessions (id, project_id, source_file, title,"
                        " cli_version, agent) VALUES (?,?,?,?,?,?)",
                        (sid, pid, src, title, version, agent))

    def prompt(self, agent, sid, pid, ts, text, uid=None):
        prompt_id = self.L.insert_prompt({"timestamp": ts, "uuid": uid, "promptSource": "typed"},
                                         text, sid, pid)
        self.db.execute("UPDATE prompts SET agent=? WHERE id=?", (agent, prompt_id))
        return prompt_id

    def request(self, agent, sid, pid, prompt_id, ts, model, inp=0, out=0, cached=0,
                think=0, tools=()):
        p = self.L.pricing
        priced = p.is_known(model)
        cost = p.estimate(model, inp, out, cached) if priced else 0.0
        nc_part, c_part = p.uncached_baseline(model, cached, 0, 0) if priced else (0.0, 0.0)
        t = None
        try:
            t = datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
        except ValueError:
            pass
        cur = self.db.execute(
            "INSERT INTO requests (session_id, project_id, prompt_id, ts, day, hour, model,"
            " model_known, input_tokens, output_tokens, thinking_tokens, cache_read_tokens,"
            " cache_write_5m, cache_write_1h, cache_write_tokens, billable_tokens,"
            " context_tokens, est_cost_usd, est_cost_no_cache_usd, tool_call_count,"
            " is_sidechain, agent) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,0,0,?,?,?,?,?,0,?)",
            (sid, pid, prompt_id, ts, _day(ts), t.hour if t else None, model,
             1 if priced else 0, inp, out, think, cached, inp + out + cached, inp + cached,
             cost, cost - c_part + nc_part, len(tools), agent))
        rpk = cur.lastrowid
        for name, target in tools:
            self.db.execute("INSERT INTO tool_calls (request_pk, session_id, project_id,"
                            " prompt_id, ts, day, name, target, kind, agent)"
                            " VALUES (?,?,?,?,?,?,?,?,'builtin',?)",
                            (rpk, sid, pid, prompt_id, ts, _day(ts), name,
                             (target or "")[:300] or None, agent))
        self.counts[agent] = self.counts.get(agent, 0) + 1

    # ---------- Codex ----------
    def codex(self):
        files = sorted(glob.glob(os.path.join(HOME, ".codex", "sessions", "**", "*.jsonl"),
                                 recursive=True))
        for fp in files:
            rows = []
            with open(fp, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
            meta = next((r.get("payload") or {} for r in rows if r.get("type") == "session_meta"), {})
            sid = "codex-" + (meta.get("id") or os.path.basename(fp))
            cwd = meta.get("cwd")
            pid = self.project("codex", cwd, "Codex")
            self.session("codex", sid, pid, fp, version=meta.get("cli_version"))
            model, prompt_id, prev_total, tools = "unknown", None, None, []
            for r in rows:
                typ, p = r.get("type"), r.get("payload") or {}
                if typ == "turn_context":
                    model = p.get("model") or model
                elif typ == "event_msg" and p.get("type") == "user_message":
                    text = (p.get("message") or "").strip()
                    if text:
                        prompt_id = self.prompt("codex", sid, pid, r.get("timestamp"), text)
                elif typ == "response_item" and p.get("type") in ("function_call", "custom_tool_call"):
                    try:
                        args = json.loads(p.get("arguments") or "{}")
                    except ValueError:
                        args = {}
                    target = args.get("cmd") or args.get("command") or args.get("path") \
                        if isinstance(args, dict) else None
                    tools.append((p.get("name"), target if isinstance(target, str) else
                                  " ".join(target) if isinstance(target, list) else None))
                elif typ == "event_msg" and p.get("type") == "token_count" and p.get("info"):
                    tot = (p["info"] or {}).get("total_token_usage") or {}
                    key = tuple(sorted(tot.items()))
                    if key == prev_total:        # Codex repeats the same count; skip duplicates
                        continue
                    prev_total = key
                    last = (p["info"] or {}).get("last_token_usage") or {}
                    cached = int(last.get("cached_input_tokens") or 0)
                    inp = max(int(last.get("input_tokens") or 0) - cached, 0)
                    self.request("codex", sid, pid, prompt_id, r.get("timestamp"), model,
                                 inp, int(last.get("output_tokens") or 0), cached,
                                 int(last.get("reasoning_output_tokens") or 0), tools)
                    tools = []

    # ---------- Gemini CLI ----------
    def gemini(self):
        known = {hashlib.sha256(p.encode()).hexdigest(): p for (p,) in
                 self.db.execute("SELECT DISTINCT path FROM projects WHERE path IS NOT NULL")}
        for fp in sorted(glob.glob(os.path.join(HOME, ".gemini", "tmp", "*", "chats", "*.json"))):
            with open(fp, encoding="utf-8", errors="replace") as fh:
                d = json.load(fh)
            h = d.get("projectHash") or os.path.basename(os.path.dirname(os.path.dirname(fp)))
            cwd = known.get(h)
            pid = self.project("gemini", cwd, f"Gemini project {h[:8]}")
            sid = "gemini-" + (d.get("sessionId") or os.path.basename(fp))
            self.session("gemini", sid, pid, fp)
            prompt_id = None
            for m in d.get("messages") or []:
                ts = m.get("timestamp")
                if m.get("type") == "user":
                    text = m.get("content") if isinstance(m.get("content"), str) else \
                        " ".join(x.get("text", "") for x in m.get("content") or [] if isinstance(x, dict))
                    if (text or "").strip():
                        prompt_id = self.prompt("gemini", sid, pid, ts, text, m.get("id"))
                elif m.get("type") == "gemini":
                    t = m.get("tokens") or {}
                    cached = int(t.get("cached") or 0)
                    tools = [(c.get("name"), json.dumps(c.get("args"))[:300] if c.get("args") else None)
                             for c in m.get("toolCalls") or [] if isinstance(c, dict)]
                    self.request("gemini", sid, pid, prompt_id, ts, m.get("model") or "gemini",
                                 max(int(t.get("input") or 0) - cached, 0),
                                 int(t.get("output") or 0) + int(t.get("thoughts") or 0),
                                 cached, int(t.get("thoughts") or 0), tools)

    # ---------- Cursor ----------
    def cursor(self):
        root = os.path.join(HOME, ".cursor", "projects")
        for fp in sorted(glob.glob(os.path.join(root, "*", "agent-transcripts", "*", "*.jsonl"))):
            slug = os.path.relpath(fp, root).split(os.sep)[0]
            # "Users-me-Documents-JIRA" -> best effort real path, else show the slug
            guess = "/" + slug.replace("-", "/")
            cwd = guess if os.path.isdir(guess) else None
            pid = self.project("cursor", cwd, slug.rsplit("-", 1)[-1] or slug)
            sid = "cursor-" + os.path.splitext(os.path.basename(fp))[0]
            ts = _iso_mtime(fp)          # transcripts carry no timestamps; use last write
            self.session("cursor", sid, pid, fp)
            prompt_id = None
            with open(fp, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        o = json.loads(line)
                    except ValueError:
                        continue
                    content = (o.get("message") or {}).get("content") or []
                    if o.get("role") == "user":
                        text = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                        text = text.replace("<user_query>", "").replace("</user_query>", "").strip()
                        if text:
                            prompt_id = self.prompt("cursor", sid, pid, ts, text)
                    elif o.get("role") == "assistant":
                        tools = [(c.get("name"), (c.get("input") or "")[:300]
                                  if isinstance(c.get("input"), str) else
                                  json.dumps(c.get("input"))[:300])
                                 for c in content if isinstance(c, dict) and c.get("type") == "tool_use"]
                        self.request("cursor", sid, pid, prompt_id, ts, "cursor", tools=tools)
        self.cursor_ide()

    def cursor_ide(self):
        """Cursor IDE chat: token counts where Cursor stored them (read-only, never locks)."""
        db = _cursor_state_db()
        if not os.path.exists(db):
            return
        uri = "file:" + db.replace("\\", "/") + "?immutable=1"
        con = sqlite3.connect(uri, uri=True)
        rows = con.execute("""
            SELECT substr(key, 10, 36) composer, json_extract(value,'$.type') typ,
                   json_extract(value,'$.createdAt') ts, json_extract(value,'$.text') text,
                   json_extract(value,'$.tokenCount.inputTokens') i,
                   json_extract(value,'$.tokenCount.outputTokens') o
            FROM cursorDiskKV WHERE key LIKE 'bubbleId:%'
              AND (json_extract(value,'$.type') = 1
                   OR json_extract(value,'$.tokenCount.inputTokens') > 0)""").fetchall()
        comps = {c for c, *_ in rows}
        started = {}
        for c in comps:
            r = con.execute("SELECT json_extract(value,'$.createdAt'), json_extract(value,'$.name')"
                            " FROM cursorDiskKV WHERE key=?", ("composerData:" + c,)).fetchone()
            if r:
                started[c] = (_iso_ms(r[0]), r[1])
        con.close()
        pid = self.project("cursor", None, "Cursor IDE chats")
        seen, prompt_for = set(), {}
        for c, typ, ts, text, i, o in rows:
            ts = ts or (started.get(c) or (None,))[0]
            if not ts:
                continue
            sid = "cursor-ide-" + c
            if sid not in seen:
                self.session("cursor", sid, pid, db, title=(started.get(c) or (None, None))[1])
                seen.add(sid)
            if typ == 1:
                if (text or "").strip():
                    prompt_for[sid] = self.prompt("cursor", sid, pid, ts, text)
            elif i:
                self.request("cursor", sid, pid, prompt_for.get(sid), ts, "cursor",
                             int(i or 0), int(o or 0))
