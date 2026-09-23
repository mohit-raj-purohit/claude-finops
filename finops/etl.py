"""Parse Claude Code JSONL transcripts into a normalized SQLite warehouse.

Entities: account -> billing_period -> project -> session -> prompt -> request
(UsageEvent) -> tool_call. Nothing is synthesized: fields absent from the source
transcripts are stored as NULL and rendered as "Unavailable from connected Claude
data" by the UI.
"""
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

from .classify import classify
from .pricing import Pricing

from .paths import ROOT, DB_PATH
DEFAULT_SOURCE = os.path.expanduser("~/.claude/projects")

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE projects (
  id INTEGER PRIMARY KEY,
  slug TEXT UNIQUE,          -- transcript directory name
  path TEXT,                 -- real cwd observed in the transcript
  name TEXT,
  is_sandbox INTEGER DEFAULT 0,
  agent TEXT DEFAULT 'claude'
);

CREATE TABLE sessions (
  id TEXT PRIMARY KEY,
  project_id INTEGER REFERENCES projects(id),
  source_file TEXT,
  title TEXT,
  git_branch TEXT,
  cli_version TEXT,
  started_at TEXT, ended_at TEXT, duration_s REAL,
  prompt_count INTEGER DEFAULT 0,
  request_count INTEGER DEFAULT 0,
  tool_call_count INTEGER DEFAULT 0,
  input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
  thinking_tokens INTEGER DEFAULT 0,
  cache_read_tokens INTEGER DEFAULT 0, cache_write_tokens INTEGER DEFAULT 0,
  billable_tokens INTEGER DEFAULT 0, total_tokens INTEGER DEFAULT 0,
  est_cost_usd REAL DEFAULT 0,
  max_context_tokens INTEGER DEFAULT 0, avg_context_tokens REAL DEFAULT 0,
  models TEXT, files_touched INTEGER DEFAULT 0, is_sidechain_only INTEGER DEFAULT 0,
  agent TEXT DEFAULT 'claude'
);

CREATE TABLE prompts (
  id INTEGER PRIMARY KEY,
  uuid TEXT, session_id TEXT REFERENCES sessions(id),
  project_id INTEGER REFERENCES projects(id),
  ts TEXT, day TEXT,
  text TEXT, char_len INTEGER, word_len INTEGER,
  category TEXT, category_confidence REAL, category_evidence TEXT,
  source TEXT,               -- typed / slash-command / queued ...
  request_count INTEGER DEFAULT 0,
  input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
  cache_read_tokens INTEGER DEFAULT 0, cache_write_tokens INTEGER DEFAULT 0,
  billable_tokens INTEGER DEFAULT 0, total_tokens INTEGER DEFAULT 0,
  est_cost_usd REAL DEFAULT 0,
  tool_calls INTEGER DEFAULT 0, files_touched INTEGER DEFAULT 0,
  latency_ms REAL, models TEXT, max_context_tokens INTEGER DEFAULT 0,
  norm_hash TEXT,
  agent TEXT DEFAULT 'claude'
);

CREATE TABLE requests (
  id INTEGER PRIMARY KEY,
  uuid TEXT, request_id TEXT,
  session_id TEXT REFERENCES sessions(id),
  project_id INTEGER REFERENCES projects(id),
  prompt_id INTEGER REFERENCES prompts(id),
  ts TEXT, day TEXT, hour INTEGER,
  model TEXT, model_known INTEGER, effort TEXT, service_tier TEXT,
  stop_reason TEXT,
  input_tokens INTEGER, output_tokens INTEGER, thinking_tokens INTEGER,
  cache_read_tokens INTEGER, cache_write_5m INTEGER, cache_write_1h INTEGER,
  cache_write_tokens INTEGER,
  billable_tokens INTEGER,   -- input + output + cache read + cache write
  context_tokens INTEGER,    -- input + cache read + cache write  (prompt side)
  est_cost_usd REAL,
  est_cost_no_cache_usd REAL,
  priced_as TEXT,            -- price list used; differs from model for long context
  unpriced_long_context INTEGER DEFAULT 0,  -- over the window with no [1m] price to use
  latency_ms REAL,
  tool_call_count INTEGER DEFAULT 0,
  is_sidechain INTEGER DEFAULT 0,
  agent_id TEXT, agent_type TEXT, agent_desc TEXT,  -- set for subagent requests
  agent TEXT DEFAULT 'claude'                        -- claude / codex / gemini / cursor
);

CREATE TABLE tool_calls (
  id INTEGER PRIMARY KEY,
  request_pk INTEGER REFERENCES requests(id),
  session_id TEXT, project_id INTEGER, prompt_id INTEGER,
  ts TEXT, day TEXT, name TEXT, target TEXT,
  tool_use_id TEXT,
  kind TEXT,                 -- builtin / mcp / connector / skill / agent
  server TEXT,               -- MCP server, skill name or subagent type
  result_chars INTEGER DEFAULT 0,   -- size of what came back into context
  carry_requests INTEGER DEFAULT 0, -- later requests in the session that re-read it
  agent TEXT DEFAULT 'claude'
);

CREATE TABLE files_touched (
  id INTEGER PRIMARY KEY,
  session_id TEXT, project_id INTEGER, prompt_id INTEGER,
  path TEXT, op TEXT, ts TEXT
);

CREATE INDEX idx_req_day ON requests(day);
CREATE INDEX idx_req_model ON requests(model);
CREATE INDEX idx_req_sess ON requests(session_id);
CREATE INDEX idx_req_proj ON requests(project_id);
CREATE INDEX idx_req_prompt ON requests(prompt_id);
CREATE INDEX idx_prompt_day ON prompts(day);
CREATE INDEX idx_prompt_cat ON prompts(category);
CREATE INDEX idx_prompt_sess ON prompts(session_id);
CREATE INDEX idx_tool_name ON tool_calls(name);
CREATE INDEX idx_tool_use ON tool_calls(tool_use_id);
CREATE INDEX idx_req_sess_ts ON requests(session_id, ts);
CREATE INDEX idx_sess_proj ON sessions(project_id);
CREATE INDEX idx_req_agent ON requests(agent);
"""

FILE_TOOLS = {"Edit": "edit", "Write": "write", "Read": "read", "NotebookEdit": "edit"}
SLASH = re.compile(r"^\s*/([a-z0-9][\w:-]*)(?=\s|$)", re.I)
CMD_NAME = re.compile(r"<command-name>/?([\w:-]+)</command-name>")
WS = re.compile(r"\s+")


def _ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _text_of(content):
    """Flatten a message content payload to plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    out = []
    for c in content:
        if isinstance(c, str):
            out.append(c)
        elif isinstance(c, dict):
            if c.get("type") == "text":
                out.append(c.get("text", ""))
            elif c.get("type") == "thinking":
                continue
    return "\n".join(x for x in out if x)


IMAGE_CHARS = 1600 * 4   # an image costs ~1.6K tokens, not its base64 length


def _result_chars(body):
    n = 0
    for c in body or []:
        if isinstance(c, dict) and c.get("type") == "image":
            n += IMAGE_CHARS
        elif isinstance(c, dict):
            n += len(c.get("text") or "")
        elif isinstance(c, str):
            n += len(c)
    return n


def _slug_to_name(slug):
    p = slug.replace("-", "/")
    return os.path.basename(p.rstrip("/")) or slug


class Loader:
    def __init__(self, db_path=DB_PATH, source=DEFAULT_SOURCE, pricing=None, other_agents=True):
        self.other_agents = other_agents
        self.db_path = db_path
        self.source = source
        self.pricing = pricing or Pricing()
        self.projects = {}
        self.agent = None
        self.pending_skill = None

    # ---------- infrastructure ----------
    def build(self, verbose=True):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        for suffix in ("-wal", "-shm"):
            p = self.db_path + suffix
            if os.path.exists(p):
                os.remove(p)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.db = sqlite3.connect(self.db_path)
        self.db.executescript(SCHEMA)
        files = []
        for dirpath, _, names in os.walk(self.source):
            for n in names:
                if n.endswith(".jsonl"):
                    files.append(os.path.join(dirpath, n))
        files.sort()
        for i, fp in enumerate(files, 1):
            if verbose and i % 20 == 0:
                print(f"  ...{i}/{len(files)} transcripts", file=sys.stderr)
            try:
                self.load_file(fp)
            except Exception as exc:  # a corrupt transcript must not kill the load
                print(f"  ! skipped {os.path.basename(fp)}: {exc}", file=sys.stderr)
        if self.other_agents:
            from .agents import AgentLoader
            counts = AgentLoader(self, log=lambda m: print(m, file=sys.stderr)).run()
            if verbose and counts:
                print("  other agents: " + ", ".join(f"{k} {v:,} requests" for k, v in counts.items()),
                      file=sys.stderr)
        self.rollup()
        self.db.execute(
            "INSERT INTO meta VALUES (?,?)",
            ("built_at", datetime.now(timezone.utc).isoformat()),
        )
        for k, v in (
            ("source_dir", self.source),
            ("transcript_files", str(len(files))),
            ("pricing_updated", str(self.pricing.updated)),
            ("pricing_source", str(self.pricing.source)),
            ("cost_basis", "estimated"),
        ):
            self.db.execute("INSERT INTO meta VALUES (?,?)", (k, v))
        self.db.commit()
        return len(files)

    def project_id(self, slug, cwd):
        if slug in self.projects:
            pid = self.projects[slug]
            if cwd:
                self.db.execute(
                    "UPDATE projects SET path=COALESCE(path,?) WHERE id=?", (cwd, pid))
            return pid
        is_sandbox = 1 if ("sandbox" in slug or slug.startswith("-private")) else 0
        name = os.path.basename(cwd) if cwd else _slug_to_name(slug)
        cur = self.db.execute(
            "INSERT INTO projects (slug, path, name, is_sandbox) VALUES (?,?,?,?)",
            (slug, cwd, name or slug, is_sandbox))
        self.projects[slug] = cur.lastrowid
        return cur.lastrowid

    # ---------- per-transcript ----------
    def load_file(self, path):
        slug = os.path.basename(os.path.dirname(path))
        agent = None
        if slug == "subagents":
            # <project>/<session>/subagents/agent-<id>.jsonl belongs to <session>
            sess_dir = os.path.dirname(os.path.dirname(path))
            slug = os.path.basename(os.path.dirname(sess_dir))
            meta = {}
            try:
                with open(os.path.splitext(path)[0] + ".meta.json") as fh:
                    meta = json.load(fh)
            except (OSError, ValueError):
                pass
            agent = {"id": os.path.splitext(os.path.basename(path))[0],
                     "parent": os.path.basename(sess_dir),
                     "type": meta.get("agentType") or "unknown",
                     "desc": meta.get("description")}
        rows = []
        with open(path, errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        if not rows:
            return

        session_id = agent["parent"] if agent else os.path.splitext(os.path.basename(path))[0]
        self.agent = agent
        self.pending_skill = None
        cwd = next((r.get("cwd") for r in rows if r.get("cwd")), None)
        pid = self.project_id(slug, cwd)
        title = next((r.get("aiTitle") for r in rows if r.get("type") == "ai-title"), None)
        branch = next((r.get("gitBranch") for r in rows if r.get("gitBranch")), None)
        version = next((r.get("version") for r in rows if r.get("version")), None)

        self.db.execute(
            ("INSERT OR IGNORE" if agent else "INSERT OR REPLACE") +
            " INTO sessions (id, project_id, source_file, title, "
            "git_branch, cli_version) VALUES (?,?,?,?,?,?)",
            (session_id, pid, path, title, branch, version))

        cur_prompt = None
        prev_time = None
        group = None          # {"key", "lines": [...], "prev_time", "prompt_id"}

        def flush():
            nonlocal group
            if group:
                self.insert_request(group["lines"], session_id, pid, group["prompt_id"],
                                    group["prev_time"])
                group = None

        for r in rows:
            typ = r.get("type")
            ts = r.get("timestamp")
            t = _ts(ts)

            if typ == "user":
                msg = r.get("message") or {}
                self.record_results(r, msg)
                # tool results and meta lines are not human prompts, and must not
                # split a request that is still waiting on its tool results
                if r.get("toolUseResult") is not None or r.get("isMeta"):
                    prev_time = t or prev_time
                    continue
                text = _text_of(msg.get("content"))
                if agent:
                    prev_time = t or prev_time
                    continue
                if not text.strip():
                    prev_time = t or prev_time
                    continue
                flush()
                cur_prompt = self.insert_prompt(r, text, session_id, pid)
                prev_time = t or prev_time

            elif typ == "assistant":
                if agent and cur_prompt is None:
                    cur_prompt = self.parent_prompt(session_id, r.get("timestamp"))
                key = (r.get("requestId") or (r.get("message") or {}).get("id") or r.get("uuid"))
                if group and group["key"] == key:
                    group["lines"].append(r)
                else:
                    flush()
                    group = {"key": key, "lines": [r], "prev_time": prev_time,
                             "prompt_id": cur_prompt}
                prev_time = t or prev_time

            elif typ in ("attachment", "system"):
                flush()
                prev_time = t or prev_time
        flush()

    def parent_prompt(self, session_id, ts):
        row = self.db.execute("SELECT id FROM prompts WHERE session_id=? AND ts<=? "
                              "ORDER BY ts DESC LIMIT 1", (session_id, ts or "")).fetchone()
        return row[0] if row else None

    def record_results(self, r, msg):
        """Size of tool results, and of skill bodies injected as meta messages."""
        content = msg.get("content")
        if r.get("isMeta") and getattr(self, "pending_skill", None):
            self.db.execute("UPDATE tool_calls SET result_chars=result_chars+? WHERE id=?",
                            (len(_text_of(content)), self.pending_skill))
            self.pending_skill = None
            return
        if not isinstance(content, list):
            return
        for c in content:
            if isinstance(c, dict) and c.get("type") == "tool_result":
                body = c.get("content")
                n = len(body) if isinstance(body, str) else _result_chars(body)
                self.db.execute("UPDATE tool_calls SET result_chars=? WHERE tool_use_id=?",
                                (n, c.get("tool_use_id")))

    def insert_prompt(self, r, text, session_id, pid):
        cat, conf, ev = classify(text)
        src = r.get("promptSource") or r.get("origin")
        m = CMD_NAME.search(text) or SLASH.match(text)
        if m:
            src = f"slash:/{m.group(1)}"
            cat = cat if cat != "other" else "automation"
        ts = r.get("timestamp")
        norm = WS.sub(" ", text.strip().lower())[:500]
        cur = self.db.execute(
            "INSERT INTO prompts (uuid, session_id, project_id, ts, day, text, char_len,"
            " word_len, category, category_confidence, category_evidence, source, norm_hash)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (r.get("uuid"), session_id, pid, ts, (ts or "")[:10], text, len(text),
             len(text.split()), cat, conf, json.dumps(ev), src, str(hash(norm))))
        return cur.lastrowid

    def insert_request(self, lines, session_id, pid, prompt_id, prev_time):
        first, last = lines[0], lines[-1]
        r = last                                  # stop_reason / usage from the final line
        msg = r.get("message") or {}
        u = msg.get("usage") or {}
        model = msg.get("model") or "unknown"
        speed = u.get("speed")
        inp = int(u.get("input_tokens") or 0)
        out = int(u.get("output_tokens") or 0)
        think = int((u.get("output_tokens_details") or {}).get("thinking_tokens") or 0)
        cr = int(u.get("cache_read_input_tokens") or 0)
        cc = u.get("cache_creation") or {}
        c5 = int(cc.get("ephemeral_5m_input_tokens") or 0)
        c1 = int(cc.get("ephemeral_1h_input_tokens") or 0)
        cw = int(u.get("cache_creation_input_tokens") or (c5 + c1))
        if c5 + c1 == 0 and cw:      # older transcripts omit the breakdown
            c5 = cw
        billable = inp + out + cr + cw
        context = inp + cr + cw

        # Price against the variant the context proves was used, not just the name in
        # the transcript: anything above the standard window was the long-context
        # variant and is billed at a premium.
        priced_as, unpriced_long = self.pricing.effective_model(model, context, speed=speed)
        cost = self.pricing.estimate(priced_as, inp, out, cr, c5, c1)
        no_cache_part, cache_part = self.pricing.uncached_baseline(priced_as, cr, c5, c1)
        cost_no_cache = cost - cache_part + no_cache_part

        tools, seen = [], set()
        for ln in lines:
            for c in ((ln.get("message") or {}).get("content") or []):
                if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("id") not in seen:
                    seen.add(c.get("id"))
                    tools.append(c)

        if billable == 0 and not tools:
            return

        ts = first.get("timestamp")               # the request started at its first line
        t = _ts(ts)
        latency = None
        if t and prev_time:
            d = (t - prev_time).total_seconds() * 1000.0
            if 0 <= d <= 900_000:    # ignore idle gaps > 15 min, they are not latency
                latency = d

        cur = self.db.execute(
            "INSERT INTO requests (uuid, request_id, session_id, project_id, prompt_id,"
            " ts, day, hour, model, model_known, effort, service_tier, stop_reason,"
            " input_tokens, output_tokens, thinking_tokens, cache_read_tokens,"
            " cache_write_5m, cache_write_1h, cache_write_tokens, billable_tokens,"
            " context_tokens, est_cost_usd, est_cost_no_cache_usd,"
            " priced_as, unpriced_long_context, latency_ms,"
            " tool_call_count, is_sidechain, agent_id, agent_type, agent_desc)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (first.get("uuid"), first.get("requestId") or msg.get("id"), session_id, pid,
             prompt_id, ts, (ts or "")[:10], (t.hour if t else None), model,
             1 if self.pricing.is_known(model) else 0, first.get("effort"),
             u.get("service_tier"), msg.get("stop_reason"), inp, out, think, cr,
             c5, c1, cw, billable, context, cost, cost_no_cache,
             priced_as, 1 if unpriced_long else 0, latency,
             len(tools), 1 if (first.get("isSidechain") or self.agent) else 0,
             *((self.agent["id"], self.agent["type"], self.agent["desc"]) if self.agent
               else (None, "inline" if first.get("isSidechain") else None, None))))
        rpk = cur.lastrowid

        day = (ts or "")[:10]
        for c in tools:
            name = c.get("name")
            args = c.get("input") or {}
            target = None
            kind, server = "builtin", None
            if isinstance(name, str) and name.startswith("mcp__"):
                server = name.split("__")[1]
                kind = "connector" if server.startswith("claude_ai_") else "mcp"
            elif name == "Skill" and isinstance(args, dict):
                kind, server = "skill", args.get("skill")
            elif name in ("Agent", "Task") and isinstance(args, dict):
                kind, server = "agent", args.get("subagent_type") or "general-purpose"
            if isinstance(args, dict):
                target = (args.get("file_path") or args.get("path")
                          or args.get("pattern") or args.get("command")
                          or args.get("description"))
                if isinstance(target, str):
                    target = target[:300]
                else:
                    target = None
            self.db.execute(
                "INSERT INTO tool_calls (request_pk, session_id, project_id, prompt_id,"
                " ts, day, name, target, tool_use_id, kind, server)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (rpk, session_id, pid, prompt_id, ts, day, name, target, c.get("id"),
                 kind, server))
            if kind == "skill":
                self.pending_skill = self.db.execute("SELECT last_insert_rowid()").fetchone()[0]
            if name in FILE_TOOLS and isinstance(args, dict) and args.get("file_path"):
                self.db.execute(
                    "INSERT INTO files_touched (session_id, project_id, prompt_id, path,"
                    " op, ts) VALUES (?,?,?,?,?,?)",
                    (session_id, pid, prompt_id, args["file_path"], FILE_TOOLS[name], ts))

    # ---------- aggregates ----------
    def rollup(self):
        d = self.db
        d.execute("""
        UPDATE prompts SET
          request_count = (SELECT COUNT(*) FROM requests r WHERE r.prompt_id=prompts.id),
          input_tokens  = COALESCE((SELECT SUM(input_tokens) FROM requests r WHERE r.prompt_id=prompts.id),0),
          output_tokens = COALESCE((SELECT SUM(output_tokens) FROM requests r WHERE r.prompt_id=prompts.id),0),
          cache_read_tokens = COALESCE((SELECT SUM(cache_read_tokens) FROM requests r WHERE r.prompt_id=prompts.id),0),
          cache_write_tokens = COALESCE((SELECT SUM(cache_write_tokens) FROM requests r WHERE r.prompt_id=prompts.id),0),
          billable_tokens = COALESCE((SELECT SUM(billable_tokens) FROM requests r WHERE r.prompt_id=prompts.id),0),
          est_cost_usd = COALESCE((SELECT SUM(est_cost_usd) FROM requests r WHERE r.prompt_id=prompts.id),0),
          tool_calls = COALESCE((SELECT SUM(tool_call_count) FROM requests r WHERE r.prompt_id=prompts.id),0),
          latency_ms = (SELECT AVG(latency_ms) FROM requests r WHERE r.prompt_id=prompts.id),
          max_context_tokens = COALESCE((SELECT MAX(context_tokens) FROM requests r WHERE r.prompt_id=prompts.id),0),
          models = (SELECT GROUP_CONCAT(DISTINCT r.model) FROM requests r WHERE r.prompt_id=prompts.id),
          files_touched = COALESCE((SELECT COUNT(DISTINCT path) FROM files_touched f WHERE f.prompt_id=prompts.id),0)
        """)
        d.execute("UPDATE prompts SET total_tokens = billable_tokens")
        # how many later main-thread requests re-read each tool result (upper bound:
        # /compact and /clear are not visible, so this over-counts after a compaction)
        d.execute("""
        UPDATE tool_calls SET carry_requests = (
          SELECT COUNT(*) FROM requests r WHERE r.session_id=tool_calls.session_id
            AND r.ts > tool_calls.ts AND r.is_sidechain=0
            AND (SELECT is_sidechain FROM requests q WHERE q.id=tool_calls.request_pk)=0)
        """)
        d.execute("""
        UPDATE sessions SET
          prompt_count  = COALESCE((SELECT COUNT(*) FROM prompts p WHERE p.session_id=sessions.id),0),
          request_count = COALESCE((SELECT COUNT(*) FROM requests r WHERE r.session_id=sessions.id),0),
          tool_call_count = COALESCE((SELECT COUNT(*) FROM tool_calls t WHERE t.session_id=sessions.id),0),
          input_tokens  = COALESCE((SELECT SUM(input_tokens) FROM requests r WHERE r.session_id=sessions.id),0),
          output_tokens = COALESCE((SELECT SUM(output_tokens) FROM requests r WHERE r.session_id=sessions.id),0),
          thinking_tokens = COALESCE((SELECT SUM(thinking_tokens) FROM requests r WHERE r.session_id=sessions.id),0),
          cache_read_tokens = COALESCE((SELECT SUM(cache_read_tokens) FROM requests r WHERE r.session_id=sessions.id),0),
          cache_write_tokens = COALESCE((SELECT SUM(cache_write_tokens) FROM requests r WHERE r.session_id=sessions.id),0),
          billable_tokens = COALESCE((SELECT SUM(billable_tokens) FROM requests r WHERE r.session_id=sessions.id),0),
          est_cost_usd = COALESCE((SELECT SUM(est_cost_usd) FROM requests r WHERE r.session_id=sessions.id),0),
          max_context_tokens = COALESCE((SELECT MAX(context_tokens) FROM requests r WHERE r.session_id=sessions.id),0),
          avg_context_tokens = (SELECT AVG(context_tokens) FROM requests r WHERE r.session_id=sessions.id),
          models = (SELECT GROUP_CONCAT(DISTINCT r.model) FROM requests r WHERE r.session_id=sessions.id),
          files_touched = COALESCE((SELECT COUNT(DISTINCT path) FROM files_touched f WHERE f.session_id=sessions.id),0),
          started_at = (SELECT MIN(ts) FROM requests r WHERE r.session_id=sessions.id),
          ended_at   = (SELECT MAX(ts) FROM requests r WHERE r.session_id=sessions.id)
        """)
        d.execute("UPDATE sessions SET total_tokens = billable_tokens")
        d.execute("""
        UPDATE sessions SET duration_s =
          (julianday(ended_at) - julianday(started_at)) * 86400.0
          WHERE started_at IS NOT NULL AND ended_at IS NOT NULL
        """)
        d.execute("DELETE FROM sessions WHERE request_count = 0 AND prompt_count = 0")
        d.commit()


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SOURCE
    print(f"Loading Claude transcripts from {src}", file=sys.stderr)
    n = Loader(source=src).build()
    con = sqlite3.connect(DB_PATH)
    q = lambda s: con.execute(s).fetchone()[0]
    print(f"\nLoaded {n} transcripts -> {DB_PATH}")
    print(f"  projects  {q('SELECT COUNT(*) FROM projects')}")
    print(f"  sessions  {q('SELECT COUNT(*) FROM sessions')}")
    print(f"  prompts   {q('SELECT COUNT(*) FROM prompts')}")
    print(f"  requests  {q('SELECT COUNT(*) FROM requests')}")
    print(f"  tools     {q('SELECT COUNT(*) FROM tool_calls')}")
    print(f"  tokens    {q('SELECT SUM(billable_tokens) FROM requests'):,}")
    print(f"  est cost  ${q('SELECT SUM(est_cost_usd) FROM requests'):,.2f} (ESTIMATED)")


if __name__ == "__main__":
    main()
