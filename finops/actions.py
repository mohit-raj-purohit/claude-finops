"""Actions that change this machine: sync, free-model launchers, skills and MCP servers.

Every action is two steps: plan() says what will happen and what it needs from the user
(consent, an API key); run() only proceeds with that consent. Long steps run as jobs whose
log the UI polls.
"""
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from collections import defaultdict

from .analytics import DB_PATH, ROOT
from .etl import DEFAULT_SOURCE, Loader

HOME = os.path.expanduser("~")
BIN_DIR = os.path.join(HOME, ".local", "bin")
KEY_DIR = os.path.join(HOME, ".config", "claude-free-models")
SKILLS_DIR = os.path.join(HOME, ".claude", "skills")
OLLAMA_URL = "http://localhost:11434"
IS_WIN, IS_MAC = os.name == "nt", sys.platform == "darwin"
OS_NAME = "Windows" if IS_WIN else "macOS" if IS_MAC else "Linux"


def _launcher_path(command):
    return os.path.join(BIN_DIR, command + (".cmd" if IS_WIN else ""))


def _ollama_bin():
    """`ollama` on PATH, or where the Windows installer puts it (PATH isn't refreshed
    for an already-running server)."""
    found = shutil.which("ollama")
    if found or not IS_WIN:
        return found
    p = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")
    return p if os.path.exists(p) else None


def _ollama_install_step():
    """How Ollama gets installed here: (step, command) or (None, manual instructions)."""
    if IS_MAC and shutil.which("brew"):
        return ({"do": "Install Ollama (free, open source) with Homebrew: brew install ollama",
                 "consent": True}, ["brew", "install", "ollama"])
    if IS_WIN and shutil.which("winget"):
        return ({"do": "Install Ollama (free, open source) with winget: "
                       "winget install -e --id Ollama.Ollama", "consent": True},
                ["winget", "install", "-e", "--id", "Ollama.Ollama",
                 "--accept-source-agreements", "--accept-package-agreements"])
    if IS_MAC or IS_WIN:
        return None, (f"Ollama isn't installed. Download it from https://ollama.com/download "
                      f"({OS_NAME}), install it, then click Add again.")
    # Linux installer needs sudo, which a web page can't type for you
    return None, ("Ollama isn't installed. Linux needs your sudo password, so run this in a "
                  "terminal, then click Add again:\n  curl -fsSL https://ollama.com/install.sh | sh")


def _path_hint():
    if IS_WIN:
        return (f'{BIN_DIR} is not on your PATH. Run in PowerShell: '
                f'[Environment]::SetEnvironmentVariable("Path", $env:Path + ";{BIN_DIR}", "User") '
                f'then open a new terminal.')
    rc = "~/.zshrc" if IS_MAC else "~/.bashrc"
    return f'{BIN_DIR} is not on your PATH. Add export PATH="$HOME/.local/bin:$PATH" to {rc}.'


ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
JOBS = {}
_jobs_lock = threading.Lock()


def _job(fn, *args):
    jid = uuid.uuid4().hex[:10]
    job = {"id": jid, "state": "running", "log": [], "result": None}
    with _jobs_lock:
        JOBS[jid] = job

    def log(line):
        job["log"].append(line)

    def run():
        try:
            job["result"] = fn(log, *args)
            job["state"] = "done"
        except Exception as exc:
            log(f"Failed: {exc}")
            job["state"] = "failed"
    threading.Thread(target=run, daemon=True).start()
    return job


def job_status(jid):
    j = JOBS.get(jid)
    return {"id": jid, "state": j["state"], "log": j["log"][-40:], "result": j["result"]} if j \
        else {"id": jid, "state": "missing", "log": []}


def _sh(cmd, log=None, timeout=None):
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace")
    last = 0.0
    for raw in p.stdout:
        # terminal progress bars: drop escape codes and keep the last redraw on the line
        parts = [ANSI.sub("", x).strip() for x in re.split(r"\r|\x1b\[\d*[AG]", raw)]
        parts = [x for x in parts if x and x != "pulling manifest"]
        if not parts:
            continue
        line = parts[-1]
        # progress bars redraw constantly; keep the log readable
        if log and line and (time.time() - last > 1.5 or "success" in line.lower()):
            log(line[-160:])
            last = time.time()
    p.wait(timeout)
    if p.returncode:
        raise RuntimeError(f"{' '.join(cmd[:3])} exited with {p.returncode}")


# ------------------------------------------------------------------ sync
def sync(log, source=DEFAULT_SOURCE):
    """Rebuild the warehouse into a temp file, then swap it in (the dashboard keeps serving)."""
    tmp = DB_PATH + ".sync"
    log("Reading Claude transcripts…")
    Loader(db_path=tmp, source=source).build(verbose=False)
    con = sqlite3.connect(tmp)
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    n = con.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
    con.close()
    for suffix in ("-wal", "-shm"):
        if os.path.exists(tmp + suffix):
            os.remove(tmp + suffix)
    log(f"Read {n:,} requests. Swapping in the new data…")
    return {"tmp": tmp}


def finish_sync(tmp):
    for suffix in ("-wal", "-shm"):
        if os.path.exists(DB_PATH + suffix):
            os.remove(DB_PATH + suffix)
    os.replace(tmp, DB_PATH)


# ------------------------------------------------------------------ free models
def _catalog():
    with open(os.path.join(ROOT, "config", "free_models.json")) as fh:
        return json.load(fh)["models"]


def _ram_gb():
    if IS_WIN:
        try:
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong),
                            ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong),
                            ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                            ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong),
                            ("ae", ctypes.c_ulonglong)]
            m = MS()
            m.len = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.total / 2**30
        except Exception:
            return None
    try:
        out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout
        return int(out) / 2**30
    except Exception:
        try:
            return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
        except Exception:
            return None


def _free_disk_gb():
    return shutil.disk_usage(HOME).free / 2**30


def _ollama_running():
    try:
        with urllib.request.urlopen(OLLAMA_URL + "/api/tags", timeout=1.5) as r:
            return [m["name"] for m in json.load(r).get("models", [])]
    except Exception:
        return None


def _on_path(d):
    return d in os.environ.get("PATH", "").split(os.pathsep)


def free_models():
    ram, disk = _ram_gb(), _free_disk_gb()
    pulled = _ollama_running()
    out = []
    for m in _catalog():
        launcher = _launcher_path(m["command"])
        installed = os.path.exists(launcher)
        if m["provider"] == "ollama":
            have_model = pulled is not None and any(
                n == m["model"] or n.split(":")[0] == m["model"] and ":" not in m["model"]
                or n == m["model"] + ":latest" for n in pulled)
        else:
            have_model = os.path.exists(os.path.join(KEY_DIR, "openrouter.key"))
        fits = not m.get("min_ram_gb") or ram is None or ram >= m["min_ram_gb"]
        out.append({**m, "installed": installed and have_model, "launcher": launcher,
                    "fits_ram": fits, "ram_gb": round(ram or 0), "free_disk_gb": round(disk)})
    return {"models": out, "ollama_installed": bool(_ollama_bin()), "os": OS_NAME,
            "ollama_running": pulled is not None, "bin_on_path": _on_path(BIN_DIR),
            "bin_dir": BIN_DIR,
            "how_it_works": "Adding a model creates a command such as `claude-qwen` that opens "
                            "Claude Code talking to that model. Your normal `claude` command and "
                            "your Claude subscription are untouched."}


def free_model_plan(mid):
    m = next((x for x in _catalog() if x["id"] == mid), None)
    if not m:
        raise KeyError(mid)
    steps, needs = [], []
    if m["provider"] == "ollama":
        if not _ollama_bin():
            step, how = _ollama_install_step()
            if not step:
                return {"model": m, "blocked": how}
            steps.append(step)
        if _ollama_running() is None:
            steps.append({"do": "Start the Ollama background service", "consent": True})
        ram = _ram_gb()
        if ram and m.get("min_ram_gb") and ram < m["min_ram_gb"]:
            steps.append({"do": f"Warning: this model wants {m['min_ram_gb']} GB RAM and this "
                                f"machine has {ram:.0f} GB. It may be very slow.", "warn": True})
        if m.get("download_gb") and _free_disk_gb() < m["download_gb"] + 2:
            return {"model": m, "blocked": f"Needs ~{m['download_gb']} GB free disk; only "
                    f"{_free_disk_gb():.0f} GB free."}
        steps.append({"do": f"Download {m['model']} (~{m['download_gb']} GB) with ollama pull",
                      "consent": True})
    else:
        if not os.path.exists(os.path.join(KEY_DIR, "openrouter.key")):
            needs.append({"field": "api_key", "label": "OpenRouter API key",
                          "url": "https://openrouter.ai/settings/keys",
                          "steps": [
                              "Open openrouter.ai and sign in (Google or GitHub works). It's free, no card needed.",
                              "Go to Settings → Keys (the button below opens it).",
                              "Click \"Create API Key\", name it e.g. claude-free, leave the credit limit empty.",
                              "Copy the key. It starts with sk-or-v1- and is shown only once.",
                              "Paste it below and click Add.",
                          ],
                          "pattern": "^sk-or-",
                          "help": "Stored only on this machine, readable by you only. Free models "
                                  "cost nothing; the key just identifies you for rate limits."})
        steps.append({"do": f"Save the key to {os.path.join(KEY_DIR, 'openrouter.key')}"
                            + ("" if IS_WIN else " (permissions 600)"),
                      "consent": False})
    steps.append({"do": f"Create the command {m['command']} in {BIN_DIR}", "consent": False})
    if not _on_path(BIN_DIR):
        steps.append({"do": _path_hint(), "warn": True})
    return {"model": m, "steps": steps, "needs": needs,
            "consent_required": any(s.get("consent") for s in steps)}


LAUNCHER_CMD = """@echo off
rem Created by Claude FinOps: Claude Code on {name}. Delete this file to remove it.
{key_line}set "ANTHROPIC_BASE_URL={base}"
set "ANTHROPIC_API_KEY="
claude --model "{model}" %*
"""

LAUNCHER = """#!/usr/bin/env bash
# Created by Claude FinOps: Claude Code on {name}. Delete this file to remove it.
{key_line}export ANTHROPIC_BASE_URL="{base}"
export ANTHROPIC_API_KEY=""
{ctx_line}exec claude --model "{model}" "$@"
"""


def free_model_install(log, mid, consent, api_key=None):
    plan = free_model_plan(mid)
    if plan.get("blocked"):
        raise RuntimeError(plan["blocked"])
    if plan["consent_required"] and not consent:
        raise RuntimeError("Not confirmed")
    m = plan["model"]
    if m["provider"] == "ollama":
        if not _ollama_bin():
            step, cmd = _ollama_install_step()
            if not step:
                raise RuntimeError(cmd)
            log(f"Installing Ollama ({' '.join(cmd[:2])})…")
            _sh(cmd, log)
        ollama = _ollama_bin()
        if not ollama:
            raise RuntimeError("Ollama installed but not found yet. Open a new terminal, "
                               "then click Add again.")
        if _ollama_running() is None:
            log("Starting Ollama…")
            if IS_MAC and shutil.which("brew") and subprocess.run(
                    ["brew", "services", "start", "ollama"], capture_output=True).returncode == 0:
                pass
            elif not IS_WIN and shutil.which("systemctl") and subprocess.run(
                    ["systemctl", "--user", "start", "ollama"], capture_output=True).returncode == 0:
                pass
            else:
                kw = ({"creationflags": 0x00000008 | 0x00000200} if IS_WIN   # detached, new group
                      else {"start_new_session": True})
                subprocess.Popen([ollama, "serve"], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, **kw)
            for _ in range(30):
                if _ollama_running() is not None:
                    break
                time.sleep(1)
            else:
                raise RuntimeError("Ollama did not start")
        log(f"Downloading {m['model']}… (this can take a while)")
        _sh([ollama, "pull", m["model"]], log)
        base = OLLAMA_URL
        key_line = ('set "ANTHROPIC_AUTH_TOKEN=ollama"\n' if IS_WIN
                    else 'export ANTHROPIC_AUTH_TOKEN="ollama"\n')
        ctx_line = ""
    else:
        keyf = os.path.join(KEY_DIR, "openrouter.key")
        if api_key and not api_key.strip().startswith("sk-or-"):
            raise RuntimeError("That doesn't look like an OpenRouter key (it should start with sk-or-)")
        if api_key:
            os.makedirs(KEY_DIR, mode=0o700, exist_ok=True)
            with open(keyf, "w") as fh:
                fh.write(api_key.strip())
            if not IS_WIN:
                os.chmod(keyf, 0o600)
            log("Saved API key.")
        if not os.path.exists(keyf):
            raise RuntimeError("An OpenRouter API key is required")
        base = "https://openrouter.ai/api"
        key_line = (f'set /p ANTHROPIC_AUTH_TOKEN=<"{keyf}"\n' if IS_WIN
                    else f'export ANTHROPIC_AUTH_TOKEN="$(cat "{keyf}")"\n')
        ctx_line = ""
    os.makedirs(BIN_DIR, exist_ok=True)
    path = _launcher_path(m["command"])
    tpl = LAUNCHER_CMD if IS_WIN else LAUNCHER
    with open(path, "w", newline="\r\n" if IS_WIN else "\n") as fh:
        fh.write(tpl.format(name=m["name"], key_line=key_line, base=base,
                            ctx_line=ctx_line, model=m["model"]))
    if not IS_WIN:
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    log(f"Done. Run `{m['command']}` in any project to use {m['name']}.")
    return {"command": m["command"], "path": path}


def free_model_remove(mid):
    m = next((x for x in _catalog() if x["id"] == mid), None)
    p = _launcher_path(m["command"]) if m else None
    if p and os.path.exists(p):
        os.remove(p)
    return {"ok": True, "note": "Launcher removed. Downloaded Ollama models stay; remove with "
                                "`ollama rm <model>` to free disk."}


# ------------------------------------------------------------------ skill / MCP suggestions
# (id, name, what it gives, keyword regex over prompts + bash, `claude mcp add` args, input needed)
MCP_CATALOG = [
    ("atlassian", "Atlassian (Jira & Confluence)", "Read and update Jira tickets and Confluence pages without copy-paste.",
     r"(?i:\b(jira|confluence)\b)|\b[A-Z]{2,6}-\d{2,6}\b",
     ["--transport", "sse", "atlassian", "https://mcp.atlassian.com/v1/sse"], None),
    ("github", "GitHub", "PRs, issues, reviews and CI status from inside Claude.",
     r"\b(pull request|github|gh (pr|issue|run|api))\b",
     ["--transport", "http", "github", "https://api.githubcopilot.com/mcp/"], None),
    ("figma", "Figma", "Pull frames, tokens and specs straight from Figma designs.",
     r"\b(figma)\b", ["--transport", "http", "figma", "https://mcp.figma.com/mcp"], None),
    ("playwright", "Playwright (browser)", "Open pages, click through flows and screenshot UI to check changes.",
     r"\b(screenshot|browser|localhost:\d+|playwright|e2e|click (on|the))\b",
     ["playwright", "--", "npx", "-y", "@playwright/mcp@latest"], None),
    ("context7", "Context7 (library docs)", "Current docs for libraries and frameworks, instead of guessing APIs.",
     r"\b(docs? for|latest version|api reference|how (do|to) (i )?use|deprecated)\b",
     ["--transport", "http", "context7", "https://mcp.context7.com/mcp"], None),
    ("sentry", "Sentry", "Pull real errors and stack traces while debugging.",
     r"\b(sentry)\b", ["--transport", "http", "sentry", "https://mcp.sentry.dev/mcp"], None),
    ("notion", "Notion", "Read and write Notion pages and databases.",
     r"\b(notion)\b", ["--transport", "http", "notion", "https://mcp.notion.com/mcp"], None),
    ("linear", "Linear", "Read and update Linear issues.",
     r"(?i)linear\.app|\blinear (issue|ticket)s?\b", ["--transport", "sse", "linear", "https://mcp.linear.app/sse"], None),
    ("postgres", "Postgres", "Query your database schema and data directly.",
     r"\b(postgres|psql|pg_dump|sql query|select \* from)\b",
     ["postgres", "--", "npx", "-y", "@modelcontextprotocol/server-postgres", "{conn}"],
     {"field": "conn", "label": "Postgres connection string",
      "help": "e.g. postgresql://readonly_user:pass@localhost:5432/mydb. Use a read-only user."}),
]

# normalised shell commands worth a skill: first word -> how many words identify the task
CMD_WORDS = {"npm": 3, "pnpm": 3, "yarn": 2, "npx": 2, "pytest": 1,
             "make": 2, "docker": 2, "kubectl": 2, "gh": 3, "cargo": 2, "go": 2,
             "mvn": 2, "gradle": 2, "./gradlew": 2, "terraform": 2, "bun": 3, "uv": 3}


def _installed_mcp():
    try:
        with open(os.path.join(HOME, ".claude.json")) as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        cfg = {}
    names = set(cfg.get("mcpServers") or {})
    for p in (cfg.get("projects") or {}).values():
        names |= set(p.get("mcpServers") or {})
    return {n.lower() for n in names}


def _norm_cmd(t):
    t = (t or "").strip()
    t = re.sub(r"^(cd [^&;]+&&\s*)+", "", t)
    words = t.split()
    if not words:
        return None
    n = CMD_WORDS.get(words[0])
    if not n:
        return None
    words = [w for w in words[:n] if not w.startswith(("-", "<", "'", '"', "$"))
             and "/" not in w[1:] and not re.search(r"[=&|>;0-9]", w)]
    return " ".join(words) if len(words) == n else None


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "task"


def suggestions(a):
    """Skills and MCP servers worth adding, from recurring work (actual transcripts).

    Scans every prompt and shell command, so it's cached on the warehouse (reset by sync);
    installed-state is re-checked on every call.
    """
    if getattr(a, "_suggest", None) is None:
        a._suggest = _suggestions(a)
    out = a._suggest
    installed = _installed_mcp()
    existing = set(os.listdir(SKILLS_DIR)) if os.path.isdir(SKILLS_DIR) else set()
    for m in out["mcp"]:
        m["installed"] = m["installed"] or m["id"] in installed
    for sk in out["skills"]:
        sk["installed"] = sk["name"] in existing
    return out


def _suggestions(a):
    used_tools = {r["name"] for r in a.q("SELECT DISTINCT name FROM tool_calls WHERE name LIKE 'mcp__%'")}
    installed = _installed_mcp()
    corpus = a.q("""SELECT p.session_id, p.text, p.source, pj.name project FROM prompts p
                    JOIN projects pj ON pj.id=p.project_id WHERE p.text IS NOT NULL""")
    bash = a.q("""SELECT t.session_id, t.target, pj.name project FROM tool_calls t
                  JOIN projects pj ON pj.id=t.project_id WHERE t.name='Bash'""")

    mcps = []
    for mid, name, what, rx, args, need in MCP_CATALOG:
        r = re.compile(rx if mid == "atlassian" else "(?i)" + rx if not rx.startswith("(?i)") else rx)
        sess, projs, samples = set(), defaultdict(int), []
        for row in corpus:
            m = r.search(row["text"] or "")
            if m:
                sess.add(row["session_id"])
                projs[row["project"]] += 1
                if len(samples) < 3:
                    samples.append(m.group(0))
        for row in bash:
            if r.search(row["target"] or ""):
                sess.add(row["session_id"])
                projs[row["project"]] += 1
        if len(sess) < 3:
            continue
        have = mid in installed or any(mid in t.lower() for t in used_tools) or \
            (mid == "playwright" and any("chrome" in t for t in used_tools))
        mcps.append({"id": mid, "name": name, "what": what, "sessions": len(sess),
                     "projects": sorted(projs, key=projs.get, reverse=True)[:3],
                     "evidence": sorted(set(samples)), "installed": have,
                     "command": "claude mcp add -s user " + " ".join(
                         x if x != "{conn}" else "<connection-string>" for x in args),
                     "needs": [need] if need else []})
    mcps.sort(key=lambda x: (x["installed"], -x["sessions"]))

    # skills: shell commands repeated across sessions
    cmds = defaultdict(lambda: {"sessions": set(), "runs": 0, "projects": defaultdict(int), "examples": []})
    for row in bash:
        k = _norm_cmd(row["target"])
        if not k:
            continue
        c = cmds[k]
        c["sessions"].add(row["session_id"])
        c["runs"] += 1
        c["projects"][row["project"]] += 1
        if len(c["examples"]) < 3 and row["target"] not in c["examples"]:
            c["examples"].append(row["target"][:200])
    existing = set(os.listdir(SKILLS_DIR)) if os.path.isdir(SKILLS_DIR) else set()
    skills = []
    for k, c in cmds.items():
        if len(c["sessions"]) < 3 or k in ("npm install", "yarn install", "pnpm install", "go get"):
            continue
        name = _slug(k)
        top = sorted(c["projects"], key=c["projects"].get, reverse=True)
        skills.append({"id": name, "name": name, "kind": "command", "trigger": k,
                       "sessions": len(c["sessions"]), "runs": c["runs"], "projects": top[:3],
                       "examples": c["examples"], "installed": name in existing,
                       "what": f"You ran `{k}` in {len(c['sessions'])} sessions. A skill records "
                               f"the exact command, flags and what to check in the output, so "
                               f"Claude runs it right first time instead of rediscovering it."})

    # skills: prompts you keep retyping (same opening words across sessions)
    opens = defaultdict(lambda: {"sessions": set(), "examples": [], "projects": defaultdict(int)})
    for row in corpus:
        words = re.findall(r"[a-z0-9']+", (row["text"] or "").lower())
        # only what you typed yourself: sdk/system prompts are already automated
        if row["source"] != "typed" or len(words) < 5 or (row["text"] or "").startswith(("<", "/")):
            continue
        k = " ".join(words[:5])
        o = opens[k]
        o["sessions"].add(row["session_id"])
        o["projects"][row["project"]] += 1
        if len(o["examples"]) < 2:
            o["examples"].append(row["text"][:200])
    for k, o in opens.items():
        if len(o["sessions"]) < 4:
            continue
        name = _slug(" ".join(k.split()[:4]))
        skills.append({"id": name, "name": name, "kind": "prompt", "trigger": k,
                       "sessions": len(o["sessions"]), "runs": len(o["sessions"]),
                       "projects": sorted(o["projects"], key=o["projects"].get, reverse=True)[:3],
                       "examples": o["examples"], "installed": name in existing,
                       "what": f"You started {len(o['sessions'])} sessions with \"{k}…\". Save it "
                               f"as a skill and trigger it with /{name}."})
    skills.sort(key=lambda x: (x["installed"], -x["sessions"]))
    return {"mcp": mcps[:12], "skills": skills[:15], "skills_dir": SKILLS_DIR,
            "note": "Suggestions come from keywords in your prompts and the shell commands Claude "
                    "ran. Each shows the evidence; add only what you would actually reuse."}


def create_skill(s):
    name = _slug(s["name"])
    d = os.path.join(SKILLS_DIR, name)
    if os.path.exists(d):
        raise RuntimeError(f"{d} already exists")
    ex = "\n".join(f"    {e}" for e in s.get("examples", []))
    if s.get("kind") == "command":
        body = (f"---\nname: {name}\ndescription: Run `{s['trigger']}` the way this project expects. "
                f"Use when running or verifying with `{s['trigger']}`.\n---\n\n"
                f"# {name}\n\nRun this exactly as the project uses it:\n\n```sh\n{s['trigger']}\n```\n\n"
                f"Seen in these projects: {', '.join(s.get('projects', []))}.\n\n"
                f"Recent real invocations:\n\n{ex}\n\n"
                f"## What to check\n\n- Read the tail of the output first; report failures with the "
                f"first error, not the whole log.\n- Edit this section with the flags and gotchas "
                f"you care about.\n")
    else:
        body = (f"---\nname: {name}\ndescription: Reusable request, started as \"{s['trigger']}…\".\n---\n\n"
                f"# {name}\n\nYou asked for this in {s.get('sessions')} sessions. Examples:\n\n{ex}\n\n"
                f"Edit these instructions into the exact steps and output you want every time.\n")
    os.makedirs(d)
    with open(os.path.join(d, "SKILL.md"), "w") as fh:
        fh.write(body)
    return {"ok": True, "path": os.path.join(d, "SKILL.md"), "use": f"/{name}"}


def add_mcp(mid, inputs):
    item = next((x for x in MCP_CATALOG if x[0] == mid), None)
    if not item:
        raise KeyError(mid)
    args = list(item[4])
    if item[5]:
        val = (inputs or {}).get(item[5]["field"], "").strip()
        if not val:
            raise RuntimeError(f"{item[5]['label']} is required")
        args = [val if x == "{%s}" % item[5]["field"] else x for x in args]
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("`claude` CLI not found on PATH")
    pre = [os.environ.get("COMSPEC", "cmd"), "/c", exe] if exe.lower().endswith((".cmd", ".bat")) else [exe]
    r = subprocess.run(pre + ["mcp", "add", "-s", "user"] + args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip()[-400:])
    return {"ok": True, "output": r.stdout.strip()[-400:],
            "next": "Restart Claude Code. For servers that need sign-in, run /mcp and choose Authenticate."}


def free_model_test(log, mid):
    """Two checks: the model answers over the Anthropic API, then the launcher runs Claude Code end to end."""
    m = next((x for x in _catalog() if x["id"] == mid), None)
    if not m:
        raise KeyError(mid)
    launcher = _launcher_path(m["command"])
    if not os.path.exists(launcher):
        raise RuntimeError(f"{m['command']} isn't added yet")
    if m["provider"] == "ollama":
        if _ollama_running() is None:
            raise RuntimeError("Ollama isn't running. Start the Ollama app (or run `ollama serve`).")
        url, headers = OLLAMA_URL + "/v1/messages", {"x-api-key": "ollama"}
    else:
        with open(os.path.join(KEY_DIR, "openrouter.key")) as fh:
            url, headers = "https://openrouter.ai/api/v1/messages", {"Authorization": "Bearer " + fh.read().strip()}
    log(f"1/2 Asking {m['model']} directly…")
    body = json.dumps({"model": m["model"], "max_tokens": 20,
                       "messages": [{"role": "user", "content": "Reply with exactly: OK"}]}).encode()
    req = urllib.request.Request(url, body, {"content-type": "application/json",
                                             "anthropic-version": "2023-06-01", **headers})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            reply = "".join(b.get("text", "") for b in json.load(r).get("content", []))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Model API returned {e.code}: {e.read().decode()[:300]}")
    log(f"    Model replied \"{reply.strip()[:60]}\" in {time.time() - t0:.1f}s ✓")
    log(f"2/2 Running `{m['command']} -p` (real Claude Code session, may take a minute)…")
    t0 = time.time()
    cmd = ([os.environ.get("COMSPEC", "cmd"), "/c", launcher] if IS_WIN else [launcher])
    r = subprocess.run(cmd + ["-p", "Reply with exactly: OK", "--max-turns", "1"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=300, cwd=HOME)
    out = (r.stdout or r.stderr).strip()
    if r.returncode:
        raise RuntimeError(f"Claude Code exited {r.returncode}: {out[-300:]}")
    log(f"    Claude Code replied \"{out[:80]}\" in {time.time() - t0:.1f}s ✓")
    log(f"Works. Run `{m['command']}` in any project.")
    return {"ok": True, "command": m["command"]}


def compare(a, agents=None):
    """Each selected agent's priced models (with your usage) side by side; free models too
    when Claude Code is selected, since they plug into Claude Code."""
    with open(os.path.join(ROOT, "config", "model_compare.json")) as fh:
        ratings = json.load(fh)["ratings"]
    agents = agents or ["claude"]
    prov = {"claude": "anthropic", "codex": "openai", "gemini": "google"}
    want = {prov[x] for x in agents if x in prov}
    where = {"anthropic": "Anthropic cloud", "openai": "OpenAI cloud", "google": "Google cloud"}
    ph = ",".join("?" * len(agents))
    used = {r["model"]: r for r in a.q(f"""SELECT model, COUNT(*) requests, SUM(est_cost_usd) cost,
                                           SUM(billable_tokens) tokens FROM requests
                                           WHERE agent IN ({ph}) GROUP BY model""", agents)}
    rows = []
    for mid, p in a.pricing.models.items():
        pv = p.get("provider", "anthropic")
        if p.get("tier") in (None, "none", "other") or pv not in want or mid.endswith("[1m]"):
            continue
        if pv == "anthropic" and mid not in ratings:
            continue
        u = used.get(mid) or {}
        rt = ratings.get(mid) or {"tools": None, "reasoning": None, "multifile": None,
                                  "speed": "—", "best_for": p.get("tier", "").capitalize() + " tier"}
        rows.append({"id": mid, "name": p.get("display_name", mid), "kind": "claude" if pv == "anthropic" else pv,
                     "where": where.get(pv, pv), "price_in": p.get("input"), "price_out": p.get("output"),
                     "context": p.get("context_window"), "requests": u.get("requests") or 0,
                     "cost": u.get("cost") or 0, "tokens": u.get("tokens") or 0, "tier": p.get("tier"), **rt})
    fm = free_models() if "claude" in agents else {"models": [], "os": None}
    for m in fm["models"]:
        if m["id"] not in ratings:
            continue
        rows.append({"id": m["id"], "name": m["name"], "kind": "free",
                     "where": "Your machine" if m["provider"] == "ollama" else "OpenRouter cloud",
                     "price_in": 0, "price_out": 0, "command": m["command"],
                     "installed": m["installed"], "fits": m["fits_ram"], "min_ram_gb": m.get("min_ram_gb"),
                     "download_gb": m.get("download_gb"), **ratings[m["id"]]})
    rank = {"frontier": 0, "balanced": 1, "economy": 2}
    score = lambda r: ((r["tools"] or 0) + (r["reasoning"] or 0) + (r["multifile"] or 0),
                       -rank.get(r.get("tier"), 3))
    rows.sort(key=lambda r: score(r), reverse=True)
    return {"rows": rows, "os": fm["os"], "ram_gb": fm["models"][0]["ram_gb"] if fm["models"] else None,
            "agents": agents,
            "note": "Ratings are judgement, not benchmarks; edit config/model_compare.json. "
                    "Unrated models show — until you add them there. Tool use matters most: coding "
                    "agents work by calling tools, and weak tool use means loops, broken edits "
                    "and early stops."}
