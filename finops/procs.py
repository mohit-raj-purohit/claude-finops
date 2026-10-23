"""Running Claude Code sessions and the actions the dashboard can take on them.

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

ACTIONS = {}
if os.name != "nt":
    # On Windows, CTRL_C_EVENT signals the whole console process group, not just
    # the target process, so there is no safe way to interrupt just one session.
    ACTIONS["interrupt"] = (signal.SIGINT, "Interrupted the current turn (like pressing Esc/Ctrl-C).")
ACTIONS["close"] = (signal.SIGTERM, "Asked the session to exit. Resume it later with `claude --resume`.")
ACTIONS["kill"] = (getattr(signal, "SIGKILL", signal.SIGTERM), "Force-killed the process.")


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


def _proc_starttime(pid):
    """Linux: a process's start time in clock ticks since boot (field 22 of /proc/<pid>/stat)."""
    try:
        with open(f"/proc/{int(pid)}/stat") as fh:
            stat = fh.read()
    except (OSError, TypeError, ValueError):
        return None
    fields = stat[stat.rfind(")") + 2:].split()   # the name in (...) may contain spaces
    return fields[19] if len(fields) > 19 else None


def _same_start(reg, p):
    """True unless the registry's procStart names a different start time than ps reports.
    Whitespace is normalised: the registry pads single-digit days ("Oct  9"), ps output is re-joined ("Oct 9").
    On Linux, Claude Code records clock ticks since boot ("119623") instead, read from /proc."""
    start = reg.get("procStart")
    if not start:
        return True
    if start.strip().isdigit():
        return _proc_starttime(reg.get("pid")) == start.strip()
    return start.split() == p["lstart"].split()


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
    model = None          # the model of the most recent assistant turn: what you are on now
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
            model = m.get("model") or model
    return {"transcript": fp, "steps": steps, "tokens": tokens, "context": ctx, "model": model,
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
        alive = bool(p) and _is_claude(p) and _same_start(reg, p)
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
    if not _same_start(reg, p):
        return {"ok": False, "error": "PID was reused by another process; refusing."}
    sig, msg = ACTIONS[action]
    if action == "interrupt":
        pane = _herdr_pane(pid)
        if pane and subprocess.run(["herdr", "pane", "send-keys", pane["pane_id"], "esc"],
                                   capture_output=True, timeout=10).returncode == 0:
            return {"ok": True, "message": "Pressed Esc in that session's herdr pane: the current turn stops."}
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


def _launch_terminal(cwd, prompt_path, herdr_pane=None):
    """Open a new Terminal window running a fresh `claude` seeded with the brief.
    herdr_pane: the old session's herdr pane; the new session opens as a tab next to it."""
    q = lambda s: "'" + s.replace("'", "'\\''") + "'"
    seed = f"Read the handover brief at {prompt_path} and continue from it."
    cmd = f"cd {q(cwd)} && claude {q(seed)}"
    if herdr_pane:
        tab = _herdr("tab", "create", "--workspace", herdr_pane.get("workspace_id") or "",
                     "--cwd", cwd, "--label", "handover", "--focus")
        new = (tab.get("root_pane") or {}).get("pane_id")
        if new and subprocess.run(["herdr", "pane", "run", new, cmd],
                                  capture_output=True, timeout=10).returncode == 0:
            return True, ""
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
    pane = _herdr_pane(pid) if opts.get("launch", True) else None
    for i, t in enumerate(jobs, 1):
        path = os.path.join(HANDOFFS, f"{stamp}-{reg.get('sessionId', 'x')[:8]}-{i}.md")
        with open(path, "w") as fh:
            fh.write(_render_brief(reg, b, t, f"{i} of {len(jobs)}" if len(jobs) > 1 else None))
        item = {"brief": path, "task": t, "launched": False}
        if opts.get("launch", True):
            item["launched"], err = _launch_terminal(reg.get("cwd") or HOME, path, pane)
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
# tmux or herdr if the session runs inside one, else Terminal.app or iTerm2 via
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


def _herdr(*args):
    """Run a herdr CLI call and return its `result` ({} on any failure)."""
    try:
        r = subprocess.run(["herdr", *args], capture_output=True, text=True, timeout=10)
        return (json.loads(r.stdout).get("result") or {}) if r.returncode == 0 else {}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}


def _herdr_pane(pid):
    """The herdr pane (terminal workspace for AI agents) whose processes include this
    session, or None when herdr isn't installed or the session runs elsewhere."""
    if not shutil.which("herdr"):
        return None
    mine = _ancestors(pid, _ps()) | {pid}
    for pane in _herdr("pane", "list").get("panes") or []:
        if not pane.get("pane_id"):
            continue
        info = _herdr("pane", "process-info", "--pane", pane["pane_id"]).get("process_info") or {}
        pids = {fp.get("pid") for fp in info.get("foreground_processes") or []} | {info.get("shell_pid")}
        if pid in pids or info.get("shell_pid") in mine:
            return pane
    return None


def _send_herdr(pid, text):
    """herdr: type the text and Enter into the session's pane."""
    pane = _herdr_pane(pid)
    if not pane:
        return False, ""
    try:
        r = subprocess.run(["herdr", "pane", "run", pane["pane_id"], text],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False, ""
    return (True, "herdr") if r.returncode == 0 else (False, (r.stderr or "").strip())


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
    if not _same_start(reg, p):
        return {"ok": False, "error": "PID was reused by another process; refusing.", "copy": text}

    tty = _tty_of(pid)
    hint = ""
    for send in (lambda: _send_tmux(tty, pid, text),
                 lambda: _send_herdr(pid, text),
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


def rename(pid, opts=None):
    """Type `/rename <name>` into a running Claude Code session's terminal, so Claude Code
    saves the name itself (registry and transcript), exactly as if you typed it there.

    opts: name – the new name (one line, at most 80 characters)
    """
    name = " ".join("".join(c if c.isprintable() else " "
                            for c in str((opts or {}).get("name") or "")).split())[:80]
    if not name:
        return {"ok": False, "error": "Type a name first."}
    text = f"/rename {name}"
    try:
        with open(os.path.join(REG, f"{pid}.json")) as fh:
            reg = json.load(fh)
    except (OSError, ValueError):
        return {"ok": False, "error": "Not a registered Claude Code session.", "copy": text}
    p = _ps().get(pid)
    if not p or not _is_claude(p):
        return {"ok": False, "error": "Process is no longer running.", "copy": text}
    if not _same_start(reg, p):
        return {"ok": False, "error": "PID was reused by another process; refusing.", "copy": text}

    tty = _tty_of(pid)
    hint = ""
    for send in (lambda: _send_tmux(tty, pid, text),
                 lambda: _send_herdr(pid, text),
                 lambda: _send_macos(tty, text),
                 lambda: _send_windows(pid, text)):
        ok, info = send()
        if ok:
            pane = _herdr_pane(pid) if info == "herdr" else None
            if pane:   # herdr's agents sidebar keeps its own label; name it the same
                _herdr("agent", "rename", pane["pane_id"], name)
            return {"ok": True, "message": f"Sent `{text}` to the session"
                                           f"{f' via {info}' if info else ''}. "
                                           "The new name shows here once that session takes it.",
                    "sent": True, "copy": text, "name": name}
        if info and not hint:
            hint = info
    return {"ok": False, "sent": False, "copy": text,
            "error": (hint or "Couldn't reach that session's terminal") +
                     f" — `{text}` is on your clipboard; paste it in that window."}


def session_prompts(fp, n=5):
    """The first prompt and the last few, so you can tell which session is which."""
    b = brief_from_transcript(fp)
    recent = b["recent"][1:] if b["turns"] <= 6 else b["recent"]
    return {"first": _clip(b["goal"], 1000), "recent": [_clip(t, 1000) for t in recent[-n:]],
            "turns": b["turns"]}
