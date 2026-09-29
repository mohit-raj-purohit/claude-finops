"""A statusline inside Claude Code itself, showing model and context usage.

  claude-finops --statusline    reads a statusline payload on stdin, prints model + context %
  claude-finops --install-statusline   wire it into settings
  claude-finops --hook          retired no-op, kept so existing installs do not error
  claude-finops --install-hook  prints that the hook is retired and does nothing else
  claude-finops --install-guard wire the session guard (finops/guard.py) into settings

The prompt hook that used to "suggest a cheaper model" is retired: that
suggestion had no basis — it would have meant repricing work that never ran.
The statusline is on the path of every prompt, so it is built to be boring:
fail silent, never block, never take long.
"""
import json
import os
import shlex
import shutil
import sys

SETTINGS = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")


def _stdin_json():
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        return {}


def _dig(d, *paths, default=None):
    """First present value among dotted paths.

    The payload shapes are not identical across Claude Code versions, and a
    statusline that breaks on upgrade is worse than one that misses a field, so
    every read tries the spellings we know of.
    """
    for path in paths:
        cur = d
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                cur = None
                break
            cur = cur[part]
        if cur not in (None, ""):
            return cur
    return default


# -------------------------------------------------------------------- hook ----

def hook():
    """UserPromptSubmit: kept as a no-op so existing installed hooks do not error."""
    try:
        _stdin_json()
    except Exception:
        pass
    return 0


# -------------------------------------------------------------- statusline ----

def statusline():
    """One line, refreshed constantly: model, context usage and the 5-hour limit."""
    try:
        d = _stdin_json()
        pct = _dig(d, "context_window.used_percentage", "context.percentUsed", "context.percent_used")
        five = _dig(d, "rate_limits.five_hour.used_percentage")
        name = _dig(d, "model.display_name", "session.model", "model") or "claude"
        bits = [str(name)]
        if isinstance(pct, (int, float)):
            bits.append(f"{pct:.0f}% ctx")
        if isinstance(five, (int, float)):
            bits.append(f"{five:.0f}% 5h")
        print(" · ".join(bits))
    except Exception:
        print("")     # an empty statusline beats a stack trace under the prompt
    return 0


# --------------------------------------------------------------- installing ----

def _command():
    """How to invoke us from settings: the installed CLI if it is on PATH."""
    exe = shutil.which("claude-finops")
    if exe:
        return exe
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # settings run this through a shell: an install path with a space would split
    return f"{shlex.quote(sys.executable)} {shlex.quote(os.path.join(root, 'run.py'))}"


def _guard_command():
    """The hook command: this installation's own code, and never a blocking exit.

    Not the claude-finops on PATH: that can be an older release without --guard,
    and its "unknown option" exit code 2 is what Claude Code reads as "block this
    tool call". `|| true` keeps any failure (missing Python, a moved install) from
    ever blocking; a successful run's JSON on stdout passes through unchanged.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    run = f"{shlex.quote(sys.executable)} {shlex.quote(os.path.join(root, 'run.py'))}"
    return f"{run} --guard || true"


def _load_settings():
    try:
        with open(SETTINGS) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_settings(data):
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    if os.path.exists(SETTINGS):
        # Their settings file is not ours to lose.
        shutil.copy2(SETTINGS, SETTINGS + ".finops-backup")
    with open(SETTINGS, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


def install_hook(remove=False):
    """The prompt hook is retired: it never had a basis for its "cheaper model"
    suggestion (that would mean repricing work that had not run yet). It is kept
    as a no-op (see hook() above) so existing installs do not error, but nothing
    new is installed here.
    """
    print("The finops prompt hook has been retired — it made suggestions with no "
          "basis in your data. Nothing was installed or changed.")
    print("The statusline (claude-finops --install-statusline) still shows model and context %.")


def statusline_state():
    """"ours" when our statusline is installed, "other" for someone else's, else None."""
    cur = _load_settings().get("statusLine")
    if not cur:
        return None
    return "ours" if "finops" in json.dumps(cur) else "other"


def install_statusline(remove=False):
    """Wire the statusline into ~/.claude/settings.json. Returns {"ok", "message"}.

    Claude Code wants {"type": "command", "command": ...}; an existing statusline that
    isn't ours is left alone.
    """
    s = _load_settings()
    if remove:
        if statusline_state() == "ours":
            s.pop("statusLine", None)
            _save_settings(s)
        return {"ok": True, "message": f"Removed the statusline from {SETTINGS}"}
    if statusline_state() == "other":
        return {"ok": False, "message": "You already have a statusline configured "
                f"({json.dumps(s['statusLine'])}). Left it alone: remove it first if you want ours."}
    s["statusLine"] = {"type": "command", "command": f"{_command()} --statusline"}
    _save_settings(s)
    return {"ok": True, "message": f"Installed the statusline in {SETTINGS}. It shows the model, "
                                   "context % and 5-hour limit % under every prompt. "
                                   "Undo: claude-finops --uninstall-statusline"}


# ------------------------------------------------------------------- guard ----

def _is_guard(h):
    cmd = h.get("command") if isinstance(h, dict) else None
    return isinstance(cmd, str) and "--guard" in cmd.split() and "finops" in cmd


def guard_state():
    """"installed" when our PreToolUse guard hook is in settings, else None."""
    groups = ((_load_settings().get("hooks") or {}).get("PreToolUse")) or []
    for g in groups if isinstance(groups, list) else []:
        if isinstance(g, dict) and any(_is_guard(h) for h in g.get("hooks") or []):
            return "installed"
    return None


def install_guard(remove=False):
    """Add (or remove) our PreToolUse hook. Returns {"ok", "message"}.

    Only our own entry is ever touched: other PreToolUse hooks, and other hook
    events, are left exactly as they were.
    """
    s = _load_settings()
    hooks = s.get("hooks") if isinstance(s.get("hooks"), dict) else {}
    groups = hooks.get("PreToolUse") if isinstance(hooks.get("PreToolUse"), list) else []
    if remove:
        if guard_state() is None:
            return {"ok": True, "message": "The session guard is not installed; nothing changed."}
        kept = []
        for g in groups:
            if isinstance(g, dict) and isinstance(g.get("hooks"), list):
                g = dict(g, hooks=[h for h in g["hooks"] if not _is_guard(h)])
                if not g["hooks"]:
                    continue
            kept.append(g)
        if kept:
            hooks["PreToolUse"] = kept
        else:
            hooks.pop("PreToolUse", None)
        if hooks:
            s["hooks"] = hooks
        else:
            s.pop("hooks", None)
        _save_settings(s)
        return {"ok": True, "message": f"Removed the session guard from {SETTINGS}"}
    if guard_state() == "installed":
        return {"ok": True, "message": "The session guard is already installed."}
    groups.append({"matcher": "", "hooks": [
        {"type": "command", "command": _guard_command(), "timeout": 10}]})
    hooks["PreToolUse"] = groups
    s["hooks"] = hooks
    _save_settings(s)
    msg = (f"Installed the session guard in {SETTINGS}. It applies to new Claude Code sessions. "
           "Undo: claude-finops --uninstall-guard")
    try:
        from .guard import load_guard_settings
        cfg = load_guard_settings()
        if not cfg.get("session_tokens") and not any(
                o.get("session_tokens") for o in (cfg.get("projects") or {}).values()):
            msg += (" It stays inactive until you set a per-session token budget "
                    "(Budgets page, or guard.session_tokens in settings.local.json).")
    except Exception:
        pass
    return {"ok": True, "message": msg}
