"""Session guard: a PreToolUse hook that keeps a human in the loop on token-heavy sessions.

  claude-finops --guard            invoked by Claude Code before every tool call
  claude-finops --install-guard    wire it into ~/.claude/settings.json (see integrate.py)

It counts the session's billable tokens (input + output + cache read + cache
write, the same figure as the dashboard's Tokens column) straight from the
transcript, reading only what was appended since the last call. Below the
budget it warns once at each configured percentage; at the budget it asks the
user to approve the next tool call, then again every step_pct (or never, in
"once" mode).

A hook cannot end a session, and this one never blocks on its own failure:
anything unexpected means exit 0 with no output, so Claude Code carries on as
if the guard were not installed.
"""
import glob
import json
import os
import sys
import time

from .paths import DATA_DIR, SETTINGS_PATH, LOCAL_SETTINGS_PATH

try:
    import fcntl
except ImportError:          # Windows: no advisory locks, concurrent calls are rare there
    fcntl = None

STATE_DIR = os.path.join(DATA_DIR, "guard")
MAX_READ = 64 * 1024 * 1024      # new bytes read per call; the rest waits for the next call
STALE_S = 30 * 86400
SWEEP_S = 3600
REJECTED = ("User rejected tool use", "The user doesn't want to proceed with this tool use")


# ---------------------------------------------------------------- settings ----

def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_guard(g, default):
    """Clean a guard block. Returns (cleaned, [bad paths]); bad leaves use the default."""
    default = default or {}
    if not isinstance(g, dict):
        return json.loads(json.dumps(default)), (["guard"] if g is not None else [])
    out, bad = dict(default), []
    for k, v in g.items():
        if k.startswith("_"):
            out[k] = v
        elif k == "session_tokens":
            if v is None or (_num(v) and v > 0):
                out[k] = v
            else:
                bad.append(f"guard.{k}")
        elif k == "warn_pct":
            if isinstance(v, list) and all(_num(x) and 0 < x < 100 for x in v):
                out[k] = sorted(set(v))
            else:
                bad.append(f"guard.{k}")
        elif k == "after_approval":
            if v in ("step", "once"):
                out[k] = v
            else:
                bad.append(f"guard.{k}")
        elif k == "step_pct":
            if _num(v) and 1 <= v <= 1000:
                out[k] = v
            else:
                bad.append(f"guard.{k}")
        elif k == "projects":
            if not isinstance(v, dict):
                bad.append(f"guard.{k}")
                continue
            projects = {}
            for path, o in v.items():
                if isinstance(o, dict) and o.get("off") is True:
                    projects[path] = {"off": True}
                elif isinstance(o, dict) and _num(o.get("session_tokens")) and o["session_tokens"] > 0:
                    projects[path] = {"session_tokens": o["session_tokens"]}
                else:
                    bad.append(f"guard.projects.{path}")
            out[k] = projects
        else:
            out[k] = v
    return out, bad


def load_guard_settings():
    """The guard block only: shipped default + settings.local.json, validated.

    Deliberately not analytics.load_settings(): that also detects the account
    from ~/.claude.json, which the hook has no use for on every tool call.
    """
    with open(SETTINGS_PATH) as fh:
        default = json.load(fh).get("guard") or {}
    local = {}
    try:
        with open(LOCAL_SETTINGS_PATH) as fh:
            local = json.load(fh).get("guard") or {}
    except (OSError, ValueError, AttributeError):
        pass
    merged = dict(default)
    if isinstance(local, dict):
        merged.update(local)
    return validate_guard(merged, default)[0]


def _norm(p):
    return os.path.normcase(os.path.realpath(os.path.expanduser(p)))


def budget_for(cfg, cwd):
    """This project's session budget: the most specific override, else the global one."""
    best, best_len = None, -1
    if cwd:
        here = _norm(cwd)
        for path, o in (cfg.get("projects") or {}).items():
            p = _norm(path)
            if (here == p or here.startswith(p.rstrip(os.sep) + os.sep)) and len(p) > best_len:
                best, best_len = o, len(p)
    if best is not None:
        return None if best.get("off") else best.get("session_tokens")
    return cfg.get("session_tokens")


# ---------------------------------------------------------------- counting ----

def billable(u):
    cc = u.get("cache_creation") or {}
    cw = int(u.get("cache_creation_input_tokens") or 0) or (
        int(cc.get("ephemeral_5m_input_tokens") or 0) + int(cc.get("ephemeral_1h_input_tokens") or 0))
    return (int(u.get("input_tokens") or 0) + int(u.get("output_tokens") or 0)
            + int(u.get("cache_read_input_tokens") or 0) + cw)


def session_files(transcript_path, session_id):
    """The transcript plus its subagents' transcripts, which the dashboard counts too."""
    files = [transcript_path]
    sub = os.path.join(os.path.dirname(transcript_path), session_id, "subagents")
    files += sorted(glob.glob(os.path.join(sub, "*.jsonl")))
    return files


def _read_new(path, fs, budget_left, on_row):
    """Feed complete lines appended since fs["offset"] to on_row. Returns bytes read."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return 0
    if size < fs.get("offset", 0):          # rewritten: start over
        fs["offset"] = 0
        fs.setdefault("requests", {}).clear()
    if size == fs.get("offset", 0) or budget_left <= 0:
        return 0
    with open(path, "rb") as fh:
        fh.seek(fs.get("offset", 0))
        chunk = fh.read(min(size - fs.get("offset", 0), budget_left))
    end = chunk.rfind(b"\n")
    if end < 0:
        return 0                               # a partial line: wait for the rest
    for raw in chunk[:end].split(b"\n"):
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict):
            on_row(row)
    fs["offset"] = fs.get("offset", 0) + end + 1
    return end + 1


def count_session(transcript_path, session_id, state, pending_tool=None):
    """Update state["files"] with newly appended lines; return (total billable, tool outcome).

    Each API request is counted once, from the last line carrying its usage (the
    ETL keys requests the same way: requestId, else message.id, else uuid). The
    outcome is "approved"/"rejected" once the result of pending_tool appears.
    """
    files = state.setdefault("files", {})
    outcome = []
    left = MAX_READ
    for path in session_files(transcript_path, session_id):
        fs = files.setdefault(path, {"offset": 0, "requests": {}})
        reqs = fs.setdefault("requests", {})

        def on_row(r):
            msg = r.get("message") if isinstance(r.get("message"), dict) else {}
            if r.get("type") == "assistant" and isinstance(msg.get("usage"), dict):
                key = r.get("requestId") or msg.get("id") or r.get("uuid")
                if key:
                    reqs[key] = billable(msg["usage"])
            elif pending_tool and r.get("type") == "user":
                for c in msg.get("content") or []:
                    if isinstance(c, dict) and c.get("type") == "tool_result" \
                            and c.get("tool_use_id") == pending_tool:
                        text = json.dumps(c.get("content")) + json.dumps(r.get("toolUseResult"))
                        outcome.append("rejected" if any(s in text for s in REJECTED) else "approved")

        left -= _read_new(path, fs, left, on_row)
    total = sum(sum(f.get("requests", {}).values()) for f in files.values())
    return total, (outcome[-1] if outcome else None)


# ---------------------------------------------------------------- deciding ----

def fmt_tokens(n):
    for div, unit in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if n >= div:
            s = f"{n / div:.1f}".rstrip("0").rstrip(".")
            return f"{s}{unit}"
    return str(int(n))


def next_ask(cfg, state):
    approved = state.get("approved_pct")
    if approved is None:
        return 100
    if cfg.get("after_approval") == "once":
        return None
    return approved + (cfg.get("step_pct") or 25)


def decide(total, budget, cfg, state, outcome=None, tool_use_id=None):
    """The hook's answer for this tool call (a dict to print), or None to stay silent."""
    if not budget:
        return None
    if state.get("asked_at") is not None and outcome == "approved":
        state["approved_pct"], state["asked_at"], state["asked_tool"] = state["asked_at"], None, None
    elif outcome == "rejected":
        state["asked_at"], state["asked_tool"] = None, None     # declined: ask again next time
    pct = 100.0 * total / budget
    level = next_ask(cfg, state)
    if level is not None and pct >= level:
        state["asked_at"], state["asked_tool"] = level, tool_use_id
        # the ask supersedes any warning it jumped past; don't replay them after approval
        state["warned"] = sorted(set(state.get("warned") or [])
                                 | {w for w in (cfg.get("warn_pct") or []) if pct >= w})
        after = next_ask(cfg, dict(state, approved_pct=level))
        hint = (f"Approving continues until {after:g}%." if after is not None
                else "Approving turns the guard off for the rest of this session.")
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason":
                f"Session guard: this session has used {fmt_tokens(total)} tokens, "
                f"{pct:.0f}% of its {fmt_tokens(budget)} budget. Allow this tool call? {hint}"}}
    warned = set(state.get("warned") or [])
    crossed = [w for w in (cfg.get("warn_pct") or []) if pct >= w and w not in warned]
    if crossed:
        state["warned"] = sorted(warned | set(crossed))
        return {"systemMessage": f"Session guard: {fmt_tokens(total)} tokens, {pct:.0f}% of "
                                 f"this session's {fmt_tokens(budget)} budget.",
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext":
                        f"This session has used {pct:.0f}% of its token budget. At a natural "
                        "break, suggest the user run /compact or start a fresh session."}}
    return None


# ------------------------------------------------------------------- state ----

def _state_path(session_id):
    safe = "".join(ch for ch in session_id if ch.isalnum() or ch in "-_")
    return os.path.join(STATE_DIR, f"{safe or 'unknown'}.json")


def _load_state(path):
    try:
        with open(path) as fh:
            s = json.load(fh)
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(path, state):
    state["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh)
    os.replace(tmp, path)


def _sweep():
    """Drop state for sessions untouched for 30 days; at most once an hour."""
    marker = os.path.join(STATE_DIR, ".swept")
    now = time.time()
    try:
        if now - os.path.getmtime(marker) < SWEEP_S:
            return
    except OSError:
        pass
    for p in glob.glob(os.path.join(STATE_DIR, "*.json")) + glob.glob(os.path.join(STATE_DIR, "*.lock")):
        try:
            if now - os.path.getmtime(p) > STALE_S:
                os.remove(p)
        except OSError:
            pass
    with open(marker, "w"):
        pass


# -------------------------------------------------------------------- hook ----

def run(payload, cfg=None):
    """Decide for one PreToolUse payload. Returns the dict to print, or None."""
    session_id = payload.get("session_id")
    transcript = payload.get("transcript_path")
    if not session_id or not transcript:
        return None
    cfg = cfg if cfg is not None else load_guard_settings()
    budget = budget_for(cfg, payload.get("cwd"))
    if not budget:
        return None
    os.makedirs(STATE_DIR, exist_ok=True)
    path = _state_path(session_id)
    lock = open(path + ".lock", "w")
    try:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX)     # parallel tool calls share one state file
        state = _load_state(path)
        total, outcome = count_session(transcript, session_id, state, state.get("asked_tool"))
        out = decide(total, budget, cfg, state, outcome, payload.get("tool_use_id"))
        _save_state(path, state)
    finally:
        lock.close()
    _sweep()
    return out


def main():
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        out = run(payload) if isinstance(payload, dict) else None
        if out:
            print(json.dumps(out))
    except Exception:
        pass          # fail open: never stand between the user and their session
    return 0
