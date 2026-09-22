"""Live advice inside Claude Code itself: a prompt hook and a statusline.

The dashboard can only advise you if you are looking at it. These two run where
the decision is actually made — the terminal you are typing in.

  claude-finops --hook          reads a UserPromptSubmit payload on stdin
  claude-finops --statusline    reads a statusline payload on stdin
  claude-finops --install-hook / --install-statusline   wire them into settings

Both are on the path of every prompt, so both are built to be boring: fail
silent, never block, never take long. A hook that erases someone's prompt
because a database was locked is a far worse bug than missing advice, so the
hook never returns a blocking exit code — it exits 0 whatever happens.
"""
import json
import os
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


def _payload_common(d):
    return (_dig(d, "model", "session.model", "model.id", "model.display_name"),
            _dig(d, "transcript_path", "session.transcript_path", "transcriptPath"))


# -------------------------------------------------------------------- hook ----

def hook():
    """UserPromptSubmit: judge the prompt in hand, before it is paid for.

    Advice goes to the user as `systemMessage`, not into Claude's context: it is
    for the person deciding which model to use, and feeding it to the model
    would just spend tokens telling it about its own price.
    """
    try:
        d = _stdin_json()
        prompt = _dig(d, "user_prompt", "prompt", default="")
        model, transcript = _payload_common(d)
        from .advisor import advise
        a = advise(model=model, transcript=transcript, prompt=prompt)
        if a:
            print(json.dumps({"systemMessage": f"finops: {a['line']}  →  {a['command']}"}))
    except Exception:
        pass          # never let advice interfere with the prompt
    return 0


# -------------------------------------------------------------- statusline ----

def statusline():
    """One line, refreshed constantly, so: what you are on, and what to try."""
    try:
        d = _stdin_json()
        model, transcript = _payload_common(d)
        pct = _dig(d, "context.percentUsed", "context.percent_used")
        name = _dig(d, "model.display_name", "session.model", "model") or "claude"
        bits = [str(name)]
        if isinstance(pct, (int, float)):
            bits.append(f"{pct:.0f}% ctx")
        from .advisor import advise
        a = advise(model=model, transcript=transcript)
        if a:
            bits.append(a["short"])
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
    return f"{sys.executable} {os.path.join(root, 'run.py')}"


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
    cmd = f"{_command()} --hook"
    s = _load_settings()
    hooks = s.setdefault("hooks", {}).setdefault("UserPromptSubmit", [])
    for group in hooks:                       # drop any earlier copy of ours
        group["hooks"] = [h for h in group.get("hooks", [])
                          if "--hook" not in str(h.get("command", ""))
                          or "finops" not in str(h.get("command", ""))]
    hooks[:] = [g for g in hooks if g.get("hooks")]
    if not remove:
        hooks.append({"matcher": "", "hooks": [{"type": "command", "command": cmd,
                                                "timeout": 10}]})
    if not hooks:
        s["hooks"].pop("UserPromptSubmit", None)
        if not s["hooks"]:
            s.pop("hooks")
    _save_settings(s)
    print(("Removed" if remove else "Installed") + f" the prompt hook in {SETTINGS}")
    if not remove:
        print("  It suggests a cheaper model when your own history backs one, before the turn runs.")
        print("  Start a new Claude Code session to pick it up.  Undo: claude-finops --uninstall-hook")


def install_statusline(remove=False):
    cmd = f"{_command()} --statusline"
    s = _load_settings()
    if remove:
        if "finops" in str(s.get("statusLine", "")):
            s.pop("statusLine", None)
    else:
        prev = s.get("statusLine")
        if prev and "finops" not in str(prev):
            print(f"You already have a statusLine configured:\n  {prev}")
            print("Leaving it alone. Remove it first if you want ours.")
            return
        s["statusLine"] = cmd
    _save_settings(s)
    print(("Removed" if remove else "Installed") + f" the statusline in {SETTINGS}")
    if not remove:
        print("  Shows the model, context pressure, and a cheaper model when one is warranted.")
        print("  Undo: claude-finops --uninstall-statusline")
