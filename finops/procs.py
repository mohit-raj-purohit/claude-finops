"""Running agent sessions (Claude Code, Codex, Gemini CLI, Cursor) and the actions the dashboard can take on them.

Source of truth is ~/.claude/sessions/<pid>.json (written by Claude Code), checked
against the live process table. A signal is only ever sent to a PID that, at the
moment of the request, is alive, runs `claude`, and has the same start time as the
registry entry — so a recycled PID can never be hit.
"""
import glob
import json
import os
import shutil
import signal
import sys
import subprocess
import time

HOME = os.path.expanduser("~")
REG = os.path.join(HOME, ".claude", "sessions")
PROJECTS = os.path.join(HOME, ".claude", "projects")

ACTIONS = {
    "interrupt": (getattr(signal, "CTRL_C_EVENT", signal.SIGINT) if os.name == "nt" else signal.SIGINT, "Interrupted the current turn (like pressing Esc/Ctrl-C)."),
    "close": (signal.SIGTERM, "Asked the session to exit. Resume it later with `claude --resume`."),
    "kill": (getattr(signal, "SIGKILL", signal.SIGTERM), "Force-killed the process."),
}


def _ps():
    """pid -> {ppid, lstart, command, rss_kb, etime}"""
    if os.name == "nt":
        return _ps_windows()
    out = subprocess.run(["ps", "-axo", "pid=,ppid=,rss=,etime=,lstart=,command="],
                         capture_output=True, text=True,
                         env=dict(os.environ, TZ="UTC", LC_ALL="C")).stdout  # registry is UTC
    procs = {}
    for line in out.splitlines():
        parts = line.split(None, 9)
        if len(parts) < 10:
            continue
        pid, ppid, rss, etime = parts[:4]
        lstart = " ".join(parts[4:9])
        procs[int(pid)] = {"ppid": int(ppid), "rss_kb": int(rss), "etime": etime,
                           "lstart": lstart, "command": parts[9]}
    return procs


def _ps_windows():
    """Windows has no `ps`; ask CIM for the same fields (lstart/etime unavailable -> blank)."""
    ps = ("Get-CimInstance Win32_Process | ForEach-Object { "
          "\"$($_.ProcessId)`t$($_.ParentProcessId)`t$([int]($_.WorkingSetSize/1024))`t$($_.CommandLine)\" }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    procs = {}
    for line in out.splitlines():
        parts = line.split("\t", 3)
        if len(parts) < 4 or not parts[0].isdigit():
            continue
        procs[int(parts[0])] = {"ppid": int(parts[1] or 0), "rss_kb": int(parts[2] or 0),
                                "etime": "", "lstart": "", "command": parts[3]}
    return procs


def _is_claude(p):
    cmd = p["command"].split()[0].strip('"') if p["command"] else ""
    base = os.path.basename(cmd.replace("\\", "/")).lower()
    return base in ("claude", "claude.exe", "claude.cmd") or cmd.endswith("/claude")


def _ancestors(pid, procs):
    seen = set()
    while pid in procs and pid not in seen and pid > 1:
        seen.add(pid)
        pid = procs[pid]["ppid"]
    return seen


def _transcript_stats(session_id, pricing):
    files = glob.glob(os.path.join(PROJECTS, "*", f"{session_id}.jsonl"))
    if not files:
        return {}
    fp = files[0]
    steps = tokens = 0
    ctx = None
    cost = 0.0
    with open(fp, errors="replace") as fh:
        for line in fh:
            if '"usage"' not in line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("type") != "assistant":
                continue
            m = r.get("message") or {}
            u = m.get("usage") or {}
            cc = u.get("cache_creation") or {}
            inp, out = u.get("input_tokens") or 0, u.get("output_tokens") or 0
            cr, cw = u.get("cache_read_input_tokens") or 0, u.get("cache_creation_input_tokens") or 0
            steps += 1
            tokens += inp + out + cr + cw
            ctx = inp + cr + cw
            c5 = cc.get("ephemeral_5m_input_tokens") or 0
            c1 = cc.get("ephemeral_1h_input_tokens") or 0
            if not (c5 or c1):
                c5 = cw
            cost += pricing.estimate(m.get("model"), inp, out, cr, c5, c1)
    return {"transcript": fp, "steps": steps, "tokens": tokens, "context": ctx,
            "est_cost_usd": cost, "last_write_s": time.time() - os.path.getmtime(fp)}


def list_sessions(pricing):
    procs = _ps()
    mine = _ancestors(os.getpid(), procs)   # the Claude session that launched this server
    out = []
    for fp in glob.glob(os.path.join(REG, "*.json")):
        try:
            with open(fp) as fh:
                reg = json.load(fh)
        except (OSError, ValueError):
            continue
        pid = reg.get("pid")
        p = procs.get(pid)
        alive = bool(p) and _is_claude(p) and (
            not reg.get("procStart") or reg["procStart"] == p["lstart"])
        if not alive:
            continue
        st = _transcript_stats(reg.get("sessionId"), pricing)
        ctx = st.get("context") or 0
        out.append({
            "pid": pid, "session_id": reg.get("sessionId"), "name": reg.get("name"),
            "cwd": reg.get("cwd"), "project": os.path.basename(reg.get("cwd") or ""),
            "status": reg.get("status"), "kind": reg.get("kind"), "version": reg.get("version"),
            "started_at": reg.get("startedAt"), "uptime": p["etime"],
            "memory_mb": round(p["rss_kb"] / 1024), "hosts_dashboard": pid in mine,
            "severity": "high" if ctx >= 300_000 else "medium" if ctx >= 150_000 else "ok",
            **st,
        })
    out.sort(key=lambda x: (-(x.get("context") or 0)))
    return out


def act(pid, action):
    if action not in ACTIONS:
        return {"ok": False, "error": f"unknown action {action}"}
    procs = _ps()
    reg_path = os.path.join(REG, f"{pid}.json")
    try:
        with open(reg_path) as fh:
            reg = json.load(fh)
    except (OSError, ValueError):
        return {"ok": False, "error": "Not a registered Claude Code session."}
    p = procs.get(pid)
    if not p or not _is_claude(p):
        return {"ok": False, "error": "Process is no longer running."}
    if reg.get("procStart") and reg["procStart"] != p["lstart"]:
        return {"ok": False, "error": "PID was reused by another process; refusing."}
    sig, msg = ACTIONS[action]
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        return {"ok": False, "error": "Process already exited."}
    except PermissionError:
        return {"ok": False, "error": "Not permitted to signal this process."}
    if action in ("close", "kill"):
        for _ in range(20):          # wait up to 2s to report the real outcome
            time.sleep(0.1)
            if pid not in _ps():
                return {"ok": True, "exited": True, "message": msg,
                        "resume": f"claude --resume {reg.get('sessionId')}"}
        return {"ok": True, "exited": False,
                "message": msg + " It is still running — use Force kill if it doesn't exit.",
                "resume": f"claude --resume {reg.get('sessionId')}"}
    return {"ok": True, "message": msg}


# ------------------------------------------------------------ other agents ----
# Codex, Gemini CLI and Cursor keep no pid registry, so a session counts as running
# when its transcript was written recently. A CLI session is tied to a process by
# working directory; Cursor sessions live inside the IDE and are never signalled.
LIVE_WINDOW_S = 20 * 60
BUSY_WINDOW_S = 60
AGENT_BINS = {"codex": ("codex",), "gemini": ("gemini",)}
RESUME = {"codex": "codex resume {sid}", "gemini": "gemini --resume"}


def _is_agent(p, agent):
    if agent == "claude":
        return _is_claude(p)
    words = [os.path.basename(w.strip('"').replace("\\", "/")).lower()
             for w in (p["command"] or "").split()[:3]]
    return any(w in AGENT_BINS.get(agent, ()) or w in tuple(b + ".exe" for b in AGENT_BINS.get(agent, ()))
               for w in words)


def _cwd_of(pid):
    if os.name == "nt":
        return None
    try:
        out = subprocess.run(["lsof", "-a", "-d", "cwd", "-p", str(pid), "-Fn"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return next((l[1:] for l in out.splitlines() if l.startswith("n")), None)


def _recent(pattern, now):
    out = []
    for fp in glob.glob(pattern, recursive=True):
        try:
            age = now - os.path.getmtime(fp)
        except OSError:
            continue
        if age <= LIVE_WINDOW_S:
            out.append((fp, age))
    return out


def _codex_live(fp, pricing):
    meta, model, steps, tokens, ctx, cost, prev = {}, "unknown", 0, 0, None, 0.0, None
    with open(fp, errors="replace") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            typ, p = r.get("type"), r.get("payload") or {}
            if typ == "session_meta":
                meta = p
            elif typ == "turn_context":
                model = p.get("model") or model
            elif typ == "event_msg" and p.get("type") == "token_count" and p.get("info"):
                tot = tuple(sorted(((p["info"] or {}).get("total_token_usage") or {}).items()))
                if tot == prev:
                    continue
                prev = tot
                last = (p["info"] or {}).get("last_token_usage") or {}
                cached = int(last.get("cached_input_tokens") or 0)
                inp, out = int(last.get("input_tokens") or 0), int(last.get("output_tokens") or 0)
                steps += 1
                tokens += inp + out
                ctx = inp
                if pricing.is_known(model):
                    cost += pricing.estimate(model, max(inp - cached, 0), out, cached)
    return {"session_id": meta.get("id"), "cwd": meta.get("cwd"), "version": meta.get("cli_version"),
            "model": model, "steps": steps, "tokens": tokens, "context": ctx, "est_cost_usd": cost}


def _gemini_live(fp, pricing):
    with open(fp, errors="replace") as fh:
        d = json.load(fh)
    steps, tokens, ctx, cost, model = 0, 0, None, 0.0, None
    for m in d.get("messages") or []:
        if m.get("type") != "gemini":
            continue
        t = m.get("tokens") or {}
        model = m.get("model") or model
        cached, inp = int(t.get("cached") or 0), int(t.get("input") or 0)
        out = int(t.get("output") or 0) + int(t.get("thoughts") or 0)
        steps += 1
        tokens += inp + out
        ctx = inp
        if pricing.is_known(model):
            cost += pricing.estimate(model, max(inp - cached, 0), out, cached)
    return {"session_id": d.get("sessionId"), "cwd": None, "model": model, "steps": steps,
            "tokens": tokens, "context": ctx, "est_cost_usd": cost}


def _cursor_live(fp, pricing):
    steps = 0
    with open(fp, errors="replace") as fh:
        for line in fh:
            if '"tool_use"' in line:
                steps += line.count('"tool_use"')
    slug = os.path.relpath(fp, os.path.join(HOME, ".cursor", "projects")).split(os.sep)[0]
    guess = "/" + slug.replace("-", "/")
    return {"session_id": os.path.splitext(os.path.basename(fp))[0],
            "cwd": guess if os.path.isdir(guess) else None, "project_hint": slug.rsplit("-", 1)[-1],
            "model": None, "steps": steps, "tokens": None, "context": None, "est_cost_usd": None}


LIVE_SOURCES = {
    "codex": (os.path.join(HOME, ".codex", "sessions", "**", "*.jsonl"), _codex_live),
    "gemini": (os.path.join(HOME, ".gemini", "tmp", "*", "chats", "*.json"), _gemini_live),
    "cursor": (os.path.join(HOME, ".cursor", "projects", "*", "agent-transcripts", "*", "*.jsonl"), _cursor_live),
}


def list_agent_sessions(pricing, agents=None, procs=None):
    procs = procs if procs is not None else _ps()
    now = time.time()
    cli = {a: [(pid, p) for pid, p in procs.items() if _is_agent(p, a)] for a in AGENT_BINS}
    cwds = {}
    out = []
    for agent, (pattern, parse) in LIVE_SOURCES.items():
        if agents and agent not in agents:
            continue
        for fp, age in _recent(pattern, now):
            try:
                st = parse(fp, pricing)
            except (OSError, ValueError):
                continue
            match = None
            for pid, p in cli.get(agent, []):
                if pid not in cwds:
                    cwds[pid] = _cwd_of(pid)
                if st.get("cwd") is None or cwds[pid] == st.get("cwd"):
                    match = (pid, p)
                    break
            ctx = st.get("context") or 0
            cwd = st.get("cwd")
            out.append({
                "agent": agent, "pid": match[0] if match else None,
                "signalable": bool(match), "session_id": st.get("session_id"), "name": None,
                "cwd": cwd, "project": os.path.basename(cwd or "") or st.get("project_hint") or agent,
                "status": "busy" if age <= BUSY_WINDOW_S else "idle", "version": st.get("version"),
                "model": st.get("model"), "uptime": match[1]["etime"] if match else "",
                "memory_mb": round(match[1]["rss_kb"] / 1024) if match else None,
                "hosts_dashboard": False, "transcript": fp, "last_write_s": age,
                "steps": st.get("steps"), "tokens": st.get("tokens"), "context": st.get("context"),
                "est_cost_usd": st.get("est_cost_usd"),
                "resume": RESUME.get(agent, "").format(sid=st.get("session_id") or "") or None,
                "severity": "high" if ctx >= 300_000 else "medium" if ctx >= 150_000 else "ok",
            })
    return out


def act_agent(pid, action, agent):
    """Signal a Codex / Gemini CLI process — only one the live list currently shows."""
    if action not in ACTIONS:
        return {"ok": False, "error": f"unknown action {action}"}
    if agent not in AGENT_BINS:
        return {"ok": False, "error": "This agent's sessions can't be stopped from here."}
    procs = _ps()
    p = procs.get(pid)
    if not p or not _is_agent(p, agent):
        return {"ok": False, "error": "Process is no longer running."}
    live = [s for s in list_agent_sessions(_NoPricing(), [agent], procs) if s["pid"] == pid]
    if not live:
        return {"ok": False, "error": "Not a live session the dashboard shows; refusing."}
    sig, msg = ACTIONS[action]
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        return {"ok": False, "error": "Process already exited."}
    except PermissionError:
        return {"ok": False, "error": "Not permitted to signal this process."}
    res = {"ok": True, "message": msg.replace("`claude --resume`", "its resume command"),
           "resume": live[0]["resume"]}
    if action in ("close", "kill"):
        for _ in range(20):
            time.sleep(0.1)
            if pid not in _ps():
                res["exited"] = True
                return res
        res["exited"] = False
    return res


class _NoPricing:
    def is_known(self, model):
        return False


# ---------------------------------------------------------------- handover ----
HANDOFFS = os.path.join(HOME, ".claude", "finops-handoffs")
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def _text_of(content):
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content or []
                     if isinstance(b, dict) and b.get("type") == "text")


def _clip(s, n):
    s = (s or "").strip()
    return s if len(s) <= n else s[:n].rstrip() + " …"


def brief_from_transcript(fp):
    """Everything a fresh session needs, read straight from the transcript —
    no model call, so building a handover costs nothing."""
    prompts, files, todos, last_reply = [], [], [], ""
    with open(fp, errors="replace") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            m = r.get("message") or {}
            if r.get("type") == "user" and not r.get("isMeta") and not r.get("isSidechain"):
                t = _text_of(m.get("content"))
                if t and not t.lstrip().startswith(("<command-", "<local-command", "<system-reminder", "Caveat:")):
                    prompts.append(t)
            elif r.get("type") == "assistant" and not r.get("isSidechain"):
                for b in m.get("content") or []:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text" and b.get("text", "").strip():
                        last_reply = b["text"]
                    elif b.get("type") == "tool_use":
                        inp = b.get("input") or {}
                        if b.get("name") in EDIT_TOOLS:
                            f = inp.get("file_path") or inp.get("notebook_path")
                            if f and f not in files:
                                files.append(f)
                        elif b.get("name") == "TodoWrite":
                            todos = inp.get("todos") or todos
    return {"goal": prompts[0] if prompts else "", "recent": prompts[-6:] if len(prompts) > 1 else [],
            "files": files, "todos": todos, "last_reply": last_reply, "turns": len(prompts)}


def _render_brief(reg, b, task=None, part=None):
    L = [f"# Handover from session {reg.get('sessionId')}", "",
         f"- Project: `{reg.get('cwd')}`",
         f"- Previous session had {b['turns']} prompts; its full history is available with "
         f"`claude --resume {reg.get('sessionId')}` if you truly need it — prefer this brief.", ""]
    if task:
        L += [f"## Your task{f' (part {part})' if part else ''}", "", task, "",
              "Only do this task. Other parts of the work are handled in separate sessions.", ""]
    L += ["## Original goal", "", _clip(b["goal"], 2500), ""]
    if b["recent"]:
        L += ["## Most recent requests (oldest first)", ""] + [f"- {_clip(p, 600)}" for p in b["recent"]] + [""]
    open_todos = [t for t in b["todos"] if t.get("status") != "completed"]
    if b["todos"]:
        L += ["## Task list at handover", ""] + [
            f"- [{'x' if t.get('status') == 'completed' else ' '}] {t.get('content')}" for t in b["todos"]] + [""]
    if b["files"]:
        L += ["## Files changed so far", ""] + [f"- `{f}`" for f in b["files"][-40:]] + [""]
    if b["last_reply"]:
        L += ["## Last status from the previous session", "", _clip(b["last_reply"], 3000), ""]
    L += ["## How to start", "",
          "Read the files above that matter for the task, verify the current state (it may have moved on "
          "since this brief), then continue." + (" Open items from the task list come first." if open_todos else "")]
    return "\n".join(L)


def _launch_terminal(cwd, prompt_path):
    """Open a new Terminal window running a fresh `claude` seeded with the brief."""
    q = lambda s: "'" + s.replace("'", "'\\''") + "'"
    seed = f"Read the handover brief at {prompt_path} and continue from it."
    cmd = f"cd {q(cwd)} && claude {q(seed)}"
    if sys.platform == "darwin":
        osa = 'tell application "Terminal"\nactivate\ndo script ' + json.dumps(cmd) + "\nend tell"
        r = subprocess.run(["osascript", "-e", osa], capture_output=True, text=True)
        return r.returncode == 0, (r.stderr or "").strip()
    if os.name == "nt":
        subprocess.Popen(["cmd", "/c", "start", "", "cmd", "/k", "claude", seed], cwd=cwd)
        return True, ""
    for term in (["x-terminal-emulator", "-e"], ["gnome-terminal", "--"], ["konsole", "-e"],
                 ["xfce4-terminal", "-x"], ["xterm", "-e"]):
        if shutil.which(term[0]):
            subprocess.Popen(term + ["bash", "-lc", cmd + "; exec bash"])
            return True, ""
    return False, "No terminal emulator found. Run this yourself: " + cmd


def handover(pid, opts):
    """Start fresh session(s) from a brief of this one.

    opts: tasks  – list of sub-tasks; one new session each (empty = one continuation)
          close  – close the old session afterwards (SIGTERM, resumable)
          launch – open Terminal windows (False = only write the briefs)
    """
    try:
        with open(os.path.join(REG, f"{pid}.json")) as fh:
            reg = json.load(fh)
    except (OSError, ValueError):
        return {"ok": False, "error": "Not a registered Claude Code session."}
    files = glob.glob(os.path.join(PROJECTS, "*", f"{reg.get('sessionId')}.jsonl"))
    if not files:
        return {"ok": False, "error": "Transcript not found for this session."}
    b = brief_from_transcript(files[0])
    tasks = [t.strip() for t in (opts.get("tasks") or []) if t and t.strip()][:8]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    os.makedirs(HANDOFFS, exist_ok=True)
    out, jobs = [], tasks or [None]
    for i, t in enumerate(jobs, 1):
        path = os.path.join(HANDOFFS, f"{stamp}-{reg.get('sessionId', 'x')[:8]}-{i}.md")
        with open(path, "w") as fh:
            fh.write(_render_brief(reg, b, t, f"{i} of {len(jobs)}" if len(jobs) > 1 else None))
        item = {"brief": path, "task": t, "launched": False}
        if opts.get("launch", True):
            item["launched"], err = _launch_terminal(reg.get("cwd") or HOME, path)
            if err:
                item["error"] = err
        out.append(item)
    res = {"ok": True, "sessions": out,
           "message": f"{'Started' if opts.get('launch', True) else 'Wrote briefs for'} {len(out)} "
                      f"new session{'s' * (len(out) > 1)} from a handover brief."}
    if opts.get("close") and all(x["launched"] for x in out):
        res["closed"] = act(pid, "close")
        res["message"] += " Old session closed (resumable)."
    return res


# ------------------------------------------------------------------ compact ----
# /compact is typed *into* the running session — there is no signal for it. Nothing
# can write to another process's terminal directly (TIOCSTI is off on macOS and on
# modern Linux), so the keystrokes are handed to whatever owns that terminal:
# tmux if the session runs inside one, else Terminal.app or iTerm2 via
# AppleScript, else Windows Terminal/console via SendKeys. When none of those owns
# it, the UI falls back to copying the command for you to paste.

def _tty_of(pid):
    """Controlling terminal of a pid, as a device path ('/dev/ttys004')."""
    if os.name == "nt":
        return ""
    try:
        t = subprocess.run(["ps", "-o", "tty=", "-p", str(pid)],
                           capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if not t or t in ("?", "??", "-"):
        return ""
    return t if t.startswith("/dev/") else "/dev/" + t


def _send_tmux(tty, pid, text):
    if not shutil.which("tmux"):
        return False, ""
    try:
        out = subprocess.run(["tmux", "list-panes", "-a", "-F",
                              "#{pane_tty}\t#{pane_pid}\t#{session_name}:#{window_index}.#{pane_index}"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False, ""
    if out.returncode != 0:
        return False, ""
    procs = _ps()
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        pane_tty, pane_pid, target = parts
        if pane_tty != tty and not (pane_pid.isdigit() and int(pane_pid) in _ancestors(pid, procs)):
            continue
        r = subprocess.run(["tmux", "send-keys", "-t", target, "--", text],
                           capture_output=True, text=True)
        if r.returncode:
            return False, (r.stderr or "").strip()
        subprocess.run(["tmux", "send-keys", "-t", target, "Enter"], capture_output=True)
        return True, "tmux"
    return False, ""


def _send_macos(tty, text):
    """Terminal.app / iTerm2 both expose each tab's tty, so the right one is addressable."""
    if sys.platform != "darwin" or not tty:
        return False, ""
    t = json.dumps(text)
    dev = json.dumps(tty)
    osa_terminal = f'''
      tell application "System Events" to set running_ to (exists process "Terminal")
      if running_ then
        tell application "Terminal"
          repeat with w in windows
            repeat with tb in tabs of w
              if (tty of tb) is {dev} then
                do script {t} in tb
                return "ok"
              end if
            end repeat
          end repeat
        end tell
      end if
      return "no"'''
    osa_iterm = f'''
      tell application "System Events" to set running_ to (exists process "iTerm2")
      if running_ then
        tell application "iTerm2"
          repeat with w in windows
            repeat with tb in tabs of w
              repeat with s in sessions of tb
                if (tty of s) is {dev} then
                  tell s to write text {t}
                  return "ok"
                end if
              end repeat
            end repeat
          end repeat
        end tell
      end if
      return "no"'''
    for script, who in ((osa_terminal, "Terminal.app"), (osa_iterm, "iTerm2")):
        try:
            r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=25)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0 and r.stdout.strip() == "ok":
            return True, who
        if r.returncode and "not allowed" in (r.stderr or "").lower():
            return False, ("macOS blocked the keystroke: allow this terminal under System Settings → "
                           "Privacy & Security → Automation.")
    return False, ""


def _send_windows(pid, text):
    """Windows: focus the session's console window, then SendKeys into it."""
    if os.name != "nt":
        return False, ""
    ps = ('$ErrorActionPreference="Stop";'
          'Add-Type -AssemblyName Microsoft.VisualBasic;'
          'Add-Type -AssemblyName System.Windows.Forms;'
          f'$p=Get-Process -Id {pid};'
          '$h=$p.MainWindowHandle;'
          'while($h -eq 0 -and $p.Parent){$p=$p.Parent;$h=$p.MainWindowHandle};'
          'if($h -eq 0){"no";exit};'
          '[Microsoft.VisualBasic.Interaction]::AppActivate($p.Id);'
          'Start-Sleep -Milliseconds 300;'
          f'[System.Windows.Forms.SendKeys]::SendWait({json.dumps(text)}+"{{ENTER}}");"ok"')
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False, ""
    return (r.returncode == 0 and r.stdout.strip().endswith("ok")), ""


def compact(pid, opts=None):
    """Type `/compact` into a running Claude Code session's terminal.

    opts: instructions – optional focus for the summary ("/compact <instructions>")
    """
    opts = opts or {}
    extra = " ".join((opts.get("instructions") or "").split())[:400]
    text = "/compact" + (f" {extra}" if extra else "")
    try:
        with open(os.path.join(REG, f"{pid}.json")) as fh:
            reg = json.load(fh)
    except (OSError, ValueError):
        return {"ok": False, "error": "Not a registered Claude Code session.", "copy": text}
    p = _ps().get(pid)
    if not p or not _is_claude(p):
        return {"ok": False, "error": "Process is no longer running.", "copy": text}
    if reg.get("procStart") and reg["procStart"] != p["lstart"]:
        return {"ok": False, "error": "PID was reused by another process; refusing.", "copy": text}

    tty = _tty_of(pid)
    hint = ""
    for send in (lambda: _send_tmux(tty, pid, text),
                 lambda: _send_macos(tty, text),
                 lambda: _send_windows(pid, text)):
        ok, info = send()
        if ok:
            return {"ok": True, "message": f"Sent `{text}` to the session"
                                           f"{f' via {info}' if info else ''}. "
                                           "It compacts on its next turn; watch that window.",
                    "sent": True, "copy": text}
        if info and not hint:
            hint = info
    return {"ok": False, "sent": False, "copy": text,
            "error": (hint or "Couldn't reach that session's terminal") +
                     f" — `{text}` is on your clipboard; paste it in that window."}
