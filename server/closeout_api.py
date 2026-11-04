#!/usr/bin/env python3
"""Team Mission Control closeout API (stdlib only). Listens on 127.0.0.1:8089; Caddy proxies /api/* to it
AFTER the sign-in check, so only the signed-in owner reaches it. Runs as teamdash via team-dashboard-api.service.
  POST /api/closeout  {"job": str<300, "owner": str<300, "answer": str<2000 (optional), "requires_answer": bool (optional)}
       -> appends {id, ts, job, owner, action:"done", answer, requires_answer, source:"dashboard"} to QUEUE (one JSON line)
  GET  /api/closeouts -> the last 48h of queued closeouts (so the UI can show "Closing..." after a reload)
The box pulls QUEUE over SSH (watch_log.py) and writes the team-log entries; this service never touches the log."""
import json, os, fcntl, uuid, datetime, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
QUEUE = os.environ.get("CLOSEOUT_QUEUE", "/opt/team-dashboard/queue/closeouts.jsonl")
HOSTS = {"127.0.0.1:8088", (os.environ.get("DASH_HOST") or "").strip().lower()} - {""}  # DASH_HOST: /opt/team-dashboard/host.env
MAX_BODY = 8192
FIELDS = {"job", "owner", "answer", "requires_answer"}
try:
    from zoneinfo import ZoneInfo
    AZ = ZoneInfo(os.environ.get("DASH_TZ") or "UTC")   # owner's time zone, from host.env
except Exception:
    AZ = datetime.timezone.utc

def validate(d):
    if not isinstance(d, dict): return "body must be a JSON object"
    extra = set(d) - FIELDS
    if extra: return "unknown field(s): " + ", ".join(sorted(extra))
    for k in ("job", "owner"):
        v = d.get(k)
        if not isinstance(v, str) or not v.strip(): return f"{k} must be a non-empty string"
        if len(v) >= 300: return f"{k} must be under 300 characters"
    a = d.get("answer", "")
    if a is None: a = ""
    if not isinstance(a, str): return "answer must be a string"
    if len(a) >= 2000: return "answer must be under 2000 characters"
    ra = d.get("requires_answer", False)
    if not isinstance(ra, bool): return "requires_answer must be true or false"
    if ra and not a.strip(): return "this item needs your answer / decision before it can be closed"
    return None

class H(BaseHTTPRequestHandler):
    server_version = "closeout"; sys_version = ""
    def log_message(self, fmt, *a): pass  # Caddy already keeps the access log
    def send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path.split("?")[0] != "/api/closeouts": return self.send(404, {"error": "not found"})
        cut = time.time() - 48 * 3600; out = []
        try:
            with open(QUEUE) as f:
                for ln in f:
                    try: e = json.loads(ln)
                    except ValueError: continue
                    try:
                        if datetime.datetime.fromisoformat(e["ts"]).timestamp() < cut: continue
                    except Exception: continue
                    out.append({k: e.get(k) for k in ("id", "ts", "job", "owner")})
        except FileNotFoundError: pass
        self.send(200, {"closeouts": out[-200:]})
    def do_POST(self):
        if self.path.split("?")[0] != "/api/closeout": return self.send(404, {"error": "not found"})
        # CSRF guard: JSON content type + custom header force a CORS preflight that nobody answers; same-origin only
        if self.headers.get("X-Dashboard") != "1" or not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.send(400, {"error": "bad request"})
        org = self.headers.get("Origin")
        if org and org.split("://", 1)[-1] not in HOSTS: return self.send(403, {"error": "bad origin"})
        try: n = int(self.headers.get("Content-Length") or 0)
        except ValueError: n = -1
        if n <= 0 or n > MAX_BODY: return self.send(413 if n > MAX_BODY else 400, {"error": "bad body size"})
        try: d = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception: return self.send(400, {"error": "invalid JSON"})
        err = validate(d)
        if err: return self.send(400, {"error": err})
        rec = {"id": uuid.uuid4().hex, "ts": datetime.datetime.now(AZ).isoformat(timespec="seconds"),
               "job": d["job"].strip(), "owner": d["owner"].strip(), "action": "done",
               "answer": (d.get("answer") or "").strip(), "requires_answer": bool(d.get("requires_answer")), "source": "dashboard"}
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with open(QUEUE, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX); f.write(line); f.flush(); os.fsync(f.fileno())
        self.send(200, {"ok": True, "id": rec["id"], "ts": rec["ts"]})

if __name__ == "__main__":
    os.umask(0o022)
    ThreadingHTTPServer(("127.0.0.1", 8089), H).serve_forever()
