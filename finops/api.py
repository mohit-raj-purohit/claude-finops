"""Stdlib HTTP server: JSON API + static dashboard. Binds to localhost only —
the warehouse contains your full prompt text.
"""
import json
import io
import csv
import os
import sqlite3
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from .analytics import Analytics, load_settings
from .paths import DB_PATH, LOCAL_SETTINGS_PATH, LOGFILE, PIDFILE, WEB_DIR, ensure_dirs

WEB = WEB_DIR
_lock = threading.Lock()
A = None

MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".json": "application/json",
        ".svg": "image/svg+xml", ".ico": "image/x-icon"}


def filters_from(qs):
    def lst(k):
        v = qs.get(k, [])
        out = []
        for item in v:
            out += [x for x in item.split(",") if x]
        return out
    f = {
        "start": qs.get("start", [None])[0] or None,
        "end": qs.get("end", [None])[0] or None,
        "agents": lst("agents"), "models": lst("models"), "projects": lst("projects"),
        "sessions": lst("sessions"), "categories": lst("categories"),
        "include_sandbox": qs.get("include_sandbox", ["1"])[0] != "0",
        "min_cost": qs.get("min_cost", [None])[0] or None,
        "max_cost": qs.get("max_cost", [None])[0] or None,
        "min_tokens": qs.get("min_tokens", [None])[0] or None,
    }
    return f


def flatten(rows):
    keys = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    buf = io.StringIO()
    wtr = csv.DictWriter(buf, fieldnames=keys, extrasaction="ignore")
    wtr.writeheader()
    for r in rows:
        wtr.writerow({k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
                      for k, v in r.items()})
    return buf.getvalue()


# A browser that navigates away, reloads, or is closed mid-response resets the
# socket. That is the client's normal behaviour, not our error, but socketserver
# prints a full traceback for it — pages of noise in server.log that look like a
# crash. These two are the only disconnect shapes it produces.
DISCONNECTS = (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)


class Server(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        import sys
        if not isinstance(sys.exception(), DISCONNECTS):
            super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def handle_one_request(self):
        # The reset can also land while we are still writing the response, past
        # the point handle_error covers.
        try:
            super().handle_one_request()
        except DISCONNECTS:
            self.close_connection = True

    def send_json(self, obj, code=200):
        body = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, body, ctype, filename=None, code=200, no_store=False):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if no_store:
            self.send_header("Cache-Control", "no-store, must-revalidate")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(n) or b"{}")
        path = urlparse(self.path).path
        try:
            if path.startswith("/api/live/"):
                self._payload = payload
                return self.session_action(path)
            if path == "/api/trial/run":
                # Spends real money and drives a real agent, so it is guarded like
                # the other side-effecting routes and only ever reached by a click.
                if not self._same_origin():
                    return self.send_json({"ok": False, "error": "forbidden"}, 403)
                from .trial import run
                return self.send_json(run(payload.get("category"), payload.get("model"),
                                          payload.get("prompts") or [],
                                          cwd=payload.get("cwd") or None))
            if path.startswith("/api/do/"):
                if not self._same_origin():
                    return self.send_json({"ok": False, "error": "forbidden"}, 403)
                return self.do_action(path[len("/api/do/"):].strip("/").split("/"), payload)
            if path == "/api/settings":
                # UI edits go to the gitignored per-machine file, never the shared defaults
                local = {}
                if os.path.exists(LOCAL_SETTINGS_PATH):
                    with open(LOCAL_SETTINGS_PATH) as fh:
                        local = json.load(fh)
                for k, v in payload.items():
                    if k in ("budgets", "limits", "alert_thresholds_pct", "waste_rules",
                             "anomaly", "scorecard", "account", "billing_period"):
                        if isinstance(v, dict) and isinstance(local.get(k), dict):
                            local[k].update(v)
                        else:
                            local[k] = v
                with open(LOCAL_SETTINGS_PATH, "w") as fh:
                    json.dump(local, fh, indent=2)
                cur = load_settings()
                with _lock:
                    A.settings = cur
                return self.send_json({"ok": True, "settings": cur})
            self.send_json({"error": "unknown endpoint"}, 404)
        except Exception:
            self.send_json({"error": traceback.format_exc()}, 500)

    def _same_origin(self):
        origin = self.headers.get("Origin")
        return self.headers.get("X-FinOps-Action") == "1" and (
            not origin or origin == f"http://{self.headers.get('Host', '')}")

    def do_action(self, parts, payload):
        """POST /api/do/...: actions that change this machine. Always user-initiated."""
        from . import actions as X
        if parts == ["sync"]:
            cur = X.JOBS.get(_SYNC["job"] or "")
            if not cur or cur["state"] != "running":
                _SYNC["job"] = X._job(_sync_job)["id"]
            return self.send_json({"job": _SYNC["job"]})
        if len(parts) == 3 and parts[0] == "free_model" and parts[2] == "install":
            j = X._job(X.free_model_install, parts[1], bool(payload.get("consent")),
                       payload.get("api_key"))
            return self.send_json({"job": j["id"]})
        if len(parts) == 3 and parts[0] == "free_model" and parts[2] == "test":
            return self.send_json({"job": X._job(X.free_model_test, parts[1])["id"]})
        if len(parts) == 3 and parts[0] == "free_model" and parts[2] == "remove":
            return self.send_json(X.free_model_remove(parts[1]))
        if parts == ["cloud_sync"]:
            from . import cloud
            j = X._job(lambda log: cloud.sync(int(payload.get("days") or 30), log))
            return self.send_json({"job": j["id"]})
        if parts == ["skill"]:
            return self.send_json(X.create_skill(payload))
        if len(parts) == 2 and parts[0] == "mcp":
            return self.send_json(X.add_mcp(parts[1], payload))
        return self.send_json({"error": "unknown action"}, 404)

    def actions_get(self, route):
        from . import actions as X
        parts = route.strip("/").split("/")
        if parts == ["free_models"]:
            return self.send_json(X.free_models())
        if len(parts) == 3 and parts[0] == "free_models" and parts[2] == "plan":
            return self.send_json(X.free_model_plan(parts[1]))
        if parts == ["compare"]:
            with _lock:
                a = A
            qs = parse_qs(urlparse(self.path).query)
            ags = [x for x in (qs.get("agents", [""])[0]).split(",") if x]
            return self.send_json(X.compare(a, ags or None))
        if parts == ["suggestions"]:
            with _lock:
                a = A
            return self.send_json(X.suggestions(a))
        if parts[0] == "job" and len(parts) == 2:
            return self.send_json(X.job_status(parts[1]))
        if parts == ["sync"]:
            with _lock:
                a = A
            built = a.q("SELECT value FROM meta WHERE key='built_at'")
            j = X.job_status(_SYNC["job"]) if _SYNC["job"] else None
            return self.send_json({"built_at": built[0]["value"] if built else None, "job": j})
        return self.send_json({"error": "unknown route"}, 404)

    def session_action(self, path):
        """POST /api/live/<pid>/<interrupt|close|kill|compact|handover>.

        Guarded against cross-site requests: a custom header can't be sent by another
        origin without a CORS preflight (which this server never approves), and any
        Origin present must be this dashboard.
        """
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        if self.headers.get("X-FinOps-Action") != "1" or (
                origin and origin not in (f"http://{host}",)):
            return self.send_json({"ok": False, "error": "forbidden"}, 403)
        from .procs import act
        parts = path.strip("/").split("/")
        if len(parts) != 4 or not parts[2].isdigit():
            return self.send_json({"ok": False, "error": "bad request"}, 400)
        agent = (self._payload or {}).get("agent") or "claude"
        if agent != "claude":
            from .procs import act_agent
            if parts[3] in ("handover", "compact"):
                return self.send_json({"ok": False,
                                       "error": f"{parts[3].capitalize()} is only available for Claude Code."})
            return self.send_json(act_agent(int(parts[2]), parts[3], agent))
        if parts[3] == "handover":
            from .procs import handover
            return self.send_json(handover(int(parts[2]), self._payload))
        if parts[3] == "compact":
            from .procs import compact
            return self.send_json(compact(int(parts[2]), self._payload))
        return self.send_json(act(int(parts[2]), parts[3]))

    def do_GET(self):
        u = urlparse(self.path)
        path, qs = u.path, parse_qs(u.query)
        try:
            if path.startswith("/api/"):
                return self.api(path[5:], qs)
            rel = "index.html" if path in ("/", "") else path.lstrip("/")
            fp = os.path.normpath(os.path.join(WEB, rel))
            if not fp.startswith(WEB) or not os.path.isfile(fp):
                return self.send_text("not found", "text/plain", code=404)
            ext = os.path.splitext(fp)[1]
            with open(fp, "rb") as fh:
                # Served from disk on every request and revalidated every time: this is
                # localhost, so the fetch is free, and a stale cached app.js after an
                # upgrade looks exactly like "the new feature is missing".
                self.send_text(fh.read(), MIME.get(ext, "application/octet-stream"),
                               no_store=True)
        except BrokenPipeError:
            pass
        except Exception:
            self.send_json({"error": traceback.format_exc()}, 500)

    def api(self, route, qs):
        if route == "update":
            from .update import check
            return self.send_json(check(force=qs.get("refresh", [""])[0] == "1"))
        if route == "usage":
            from .limits import usage
            return self.send_json(usage(force=qs.get("refresh", [""])[0] == "1"))
        if route.startswith(("free_models", "suggestions", "job/", "sync", "compare")):
            return self.actions_get(route)
        f = filters_from(qs)
        g = lambda k, d=None: qs.get(k, [d])[0]
        with _lock:
            a = A
        if route == "options":
            from .cloud import configured
            o = a.options()
            o["cloud"] = configured()          # nav hides "Billed vs local" until a key exists
            return self.send_json(o)
        if route == "by_agent":
            return self.send_json(a.by_agent(f))
        if route == "overview":
            return self.send_json(a.overview(f))
        if route == "burn":
            return self.send_json(a.burn(f))
        if route == "timeline":
            return self.send_json(a.timeline(f, g("grain", "day")))
        if route == "models":
            return self.send_json(a.models(f))
        if route == "projects":
            return self.send_json(a.projects(f))
        if route == "sessions":
            return self.send_json(a.sessions(f, int(g("limit", 200)), g("order", "cost")))
        if route == "prompts":
            return self.send_json(a.prompts(f, int(g("limit", 200)), int(g("offset", 0)),
                                            g("order", "cost"), g("q")))
        if route == "leaderboards":
            return self.send_json(a.leaderboards(f, int(g("n", 20))))
        if route == "categories":
            return self.send_json(a.categories(f))
        if route == "efficiency":
            return self.send_json(a.efficiency(f))
        if route == "long_context_pricing":
            return self.send_json(a.long_context_pricing(f))
        if route == "ttl_replay":
            return self.send_json(a.ttl_replay(f))
        if route == "hygiene":
            return self.send_json(a.hygiene(f, top=int(g("top", 12))))
        if route == "context":
            return self.send_json(a.context_analysis(f))
        if route == "waste":
            return self.send_json(a.waste(f))
        if route == "trial":
            from .trial import samples, available
            return self.send_json({"available": available(),
                                   "samples": samples(qs.get("category", [""])[0],
                                                      int(qs.get("limit", ["3"])[0]))})
        if route == "model_evidence":
            return self.send_json(a.model_evidence(f))
        if route == "context_window_fit":
            return self.send_json(a.context_window_fit(f))
        if route == "recommendations":
            return self.send_json(a.recommendations(f))
        if route == "forecast":
            return self.send_json(a.forecast(f))
        if route == "budgets":
            return self.send_json(a.budgets(f))
        if route == "anomalies":
            return self.send_json(a.anomalies(f))
        if route == "scorecard":
            return self.send_json(a.scorecard(f))
        if route == "advisor":
            return self.send_json(a.advisor(f))
        if route == "diagnose":
            from .diagnose import Diagnoser
            return self.send_json(Diagnoser(a).run(f))
        if route == "breakdown":
            from .diagnose import Diagnoser
            return self.send_json(Diagnoser(a).breakdown(f))
        if route == "live":
            from .procs import list_sessions, list_agent_sessions
            want = [x for x in (g("agents", "") or "").split(",") if x]
            claude = [dict(s, agent="claude", signalable=True,
                           resume=f"claude --resume {s.get('session_id')}")
                      for s in list_sessions(a.pricing)] if not want or "claude" in want else []
            others = list_agent_sessions(a.pricing, [x for x in want if x != "claude"] or None) \
                if not want or any(x != "claude" for x in want) else []
            rows = sorted(claude + others, key=lambda x: -(x.get("context") or 0))
            # Live model advice, while the session can still act on it. One cached
            # evidence read for the whole list, and never fatal to the view.
            try:
                from .advisor import advise, evidence
                ev = evidence()
                for r in rows:
                    r["advice"] = advise(model=r.get("model"), transcript=r.get("transcript"),
                                         ev=ev) if ev else None
            except Exception:
                for r in rows:
                    r.setdefault("advice", None)
            return self.send_json({"sessions": rows})
        if route == "cloud":
            from .cloud import report
            return self.send_json(report(a, int(g("days", 30))))
        if route == "developer":
            return self.send_json(a.developer(f))
        if route == "search":
            return self.send_json(a.search(g("q", ""), int(g("limit", 40))))
        if route.startswith("prompt/"):
            return self.send_json(a.prompt_detail(int(route.split("/")[1])))
        if route.startswith("session/"):
            return self.send_json(a.session_detail(route.split("/", 1)[1]))
        if route == "bundle":
            return self.send_json({
                "overview": a.overview(f), "burn": a.burn(f), "timeline": a.timeline(f),
                "models": a.models(f), "projects": a.projects(f)[:40],
                "categories": a.categories(f), "efficiency": a.efficiency(f),
                "context": a.context_analysis(f), "waste": a.waste(f),
                "recommendations": a.recommendations(f), "forecast": a.forecast(f),
                "budgets": a.budgets(f), "anomalies": a.anomalies(f),
                "scorecard": a.scorecard(f), "advisor": a.advisor(f),
                "leaderboards": a.leaderboards(f), "developer": a.developer(f),
            })
        if route.startswith("export/"):
            return self.export(route.split("/", 1)[1], f, g)
        self.send_json({"error": f"unknown route {route}"}, 404)

    def export(self, what, f, g):
        with _lock:
            a = A
        fmt = g("format", "csv")
        data = {
            "prompts": lambda: a.prompts(dict(f, _full_text=True), limit=100000, order="cost"),
            "sessions": lambda: a.sessions(f, limit=100000, order="cost"),
            "usage": lambda: a.timeline(f),
            "models": lambda: a.models(f)["rows"],
            "projects": lambda: a.projects(f),
            "costs": lambda: a.timeline(f),
            "waste": lambda: [dict(x, evidence=len(x["evidence"])) for x in a.waste(f)["findings"]],
            "recommendations": lambda: a.recommendations(f)["recommendations"],
            "forecast": lambda: [dict(name=k, **v) for k, v in
                                 (a.forecast(f).get("scenarios") or {}).items()],
        }
        if what == "report":
            return self.report(a, f)
        if what not in data:
            return self.send_json({"error": "unknown export"}, 404)
        rows = data[what]()
        if fmt == "json":
            return self.send_text(json.dumps(rows, indent=2, default=str),
                                  "application/json", f"claude-finops-{what}.json")
        return self.send_text(flatten(rows), "text/csv", f"claude-finops-{what}.csv")

    def report(self, a, f):
        """Self-contained printable HTML report (Cmd/Ctrl+P -> PDF)."""
        from .report import build_report
        self.send_text(build_report(a, f), "text/html; charset=utf-8")





def _under_claude():
    if os.name == "nt":
        return False
    from .procs import _ps, _ancestors, _is_claude
    procs = _ps()
    return any(_is_claude(procs[p]) for p in _ancestors(os.getpid(), procs) if p in procs)


def detach(port):
    """Re-parent the server into its own process session so closing the Claude
    session (or terminal) that launched it doesn't take the dashboard down too."""
    import signal
    ensure_dirs()
    if os.fork():
        print(f"Claude FinOps Command Center -> http://127.0.0.1:{port}  (detached)")
        print(f"  log: {LOGFILE}   stop: ./run.sh --stop")
        os._exit(0)
    os.setsid()
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    if os.fork():
        os._exit(0)
    fd = os.open(LOGFILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    with open(PIDFILE, "w") as fh:
        fh.write(str(os.getpid()))


_SYNC = {"job": None}


def _sync_job(log):
    """Rebuild, then swap the live Analytics over to the fresh warehouse."""
    global A
    from . import actions as X
    res = X.sync(log)
    with _lock:
        try:
            A.close()             # Windows can't replace a file that's still open
        except Exception:
            pass
        X.finish_sync(res["tmp"])
        A = Analytics(DB_PATH)
    log("Synced.")
    return {"built_at": A.q("SELECT value FROM meta WHERE key='built_at'")[0]["value"]}


def _notify_update():
    from .update import notify
    notify()


def serve(port=8787, db=DB_PATH, background=None):
    global A
    if not os.path.exists(db):
        raise SystemExit(f"No warehouse at {db}. Run:  python3 -m finops.etl")
    if background is None:
        background = os.environ.get("FINOPS_DETACH") == "1" or _under_claude()
    if background and hasattr(os, "fork"):
        detach(port)
    from .etl import needs_rebuild
    if needs_rebuild(db):
        print("Warehouse schema is out of date. Run:  claude-finops --rebuild", file=sys.stderr)
    A = Analytics(db)
    srv = Server(("127.0.0.1", port), Handler)
    print(f"Claude FinOps Command Center -> http://127.0.0.1:{port}")
    print(f"  warehouse: {db}")
    print(f"  data      : {A.first_day} .. {A.last_day}")
    print("  Ctrl-C to stop.")
    # Off the main thread: a slow registry must never delay the dashboard.
    threading.Thread(target=_notify_update, daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    import sys
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    bg = True if "--detach" in sys.argv else False if "--foreground" in sys.argv else None
    serve(int(args[0]) if args else 8787, background=bg)
