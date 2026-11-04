#!/usr/bin/env python3
"""Watch team-log/updates.jsonl (also the only source for Upcoming deadlines) and each bot's avatar shape/color
(agents/*/profile.json); when either changes, wait for writes to settle (30s) then run build_and_deploy.sh (data-only).
Also (every ~60s) pulls dashboard Done-button closeouts from the server queue over SSH
(/opt/team-dashboard/queue/closeouts.jsonl, read from a saved byte offset) and turns each one into a team-log entry
(state done) + a decisions.jsonl entry when it carries an answer, then refreshes right away.
Each closeout is applied once: its id is stored as closeout_id in the log line and in closeouts_processed.jsonl.
When a cycle writes new decisions.jsonl lines, POSTs once to the Grok Bot routine webhook
(DECISIONS_WEBHOOK_URL / DECISIONS_WEBHOOK_KEY from env, else the config.json "webhook_env" file or box/.webhook.env;
Authorization: Bearer) -> a relay routine passes the answers to the owning bots. Optional.
Single instance via lock file. Log: watch_log.log. Start: nohup python3 watch_log.py >/dev/null 2>&1 &"""
import os, sys, time, subprocess, fcntl, datetime, json, urllib.request, urllib.error
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mc_config as CFG
TEAMLOG = CFG.LOG_DIR
LOG = os.path.join(TEAMLOG, "updates.jsonl")
DECISIONS = os.path.join(TEAMLOG, "decisions.jsonl")
OUT = os.path.join(HERE, "watch_log.log")
PROCESSED = os.path.join(HERE, "closeouts_processed.jsonl")
STATE = os.path.join(HERE, ".closeouts_offset")
QUEUE = "/opt/team-dashboard/queue/closeouts.jsonl"
PULL_EVERY = 60
AZ = CFG.TZ
TZL = CFG.tz_label()
WHO = CFG.OWNER or "the owner"
def load_webhook():
    """Env vars win; otherwise read KEY="value" lines from the chmod-600 .webhook.env. Values are never logged."""
    cfg = {}
    try:
        for l in open(CFG.C.get("webhook_env") or os.path.join(HERE, ".webhook.env")):
            k, sep, v = l.strip().partition("=")
            if sep and k in ("DECISIONS_WEBHOOK_URL", "DECISIONS_WEBHOOK_KEY"):
                try: v = json.loads(v)
                except ValueError: v = v.strip("'\"")
                cfg[k] = v
    except FileNotFoundError: pass
    for k in ("DECISIONS_WEBHOOK_URL", "DECISIONS_WEBHOOK_KEY"):
        if os.environ.get(k): cfg[k] = os.environ[k]
    return cfg.get("DECISIONS_WEBHOOK_URL"), cfg.get("DECISIONS_WEBHOOK_KEY")
WH_URL, WH_KEY = load_webhook()
lock = open(os.path.join(HERE, ".watch_log.lock"), "w")
try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError: raise SystemExit("already running")
lock.write(str(os.getpid())); lock.flush()
def note(m):
    with open(OUT, "a") as f: f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {m}\n")
AGENTS = CFG.AGENTS_DIR
def looks():
    """Each bot's avatarShape/avatarColor from its profile.json, so a changed look also triggers a refresh."""
    out = []
    try: ids = sorted(os.listdir(AGENTS))
    except FileNotFoundError: return ()
    for a in ids:
        try: p = json.load(open(os.path.join(AGENTS, a, "profile.json")))
        except Exception: continue
        out.append((a, p.get("avatarShape") or "", p.get("avatarColor") or ""))
    return tuple(out)
def mt():
    try: m = os.stat(LOG).st_mtime
    except FileNotFoundError: m = 0
    return (m, looks())
def append(path, obj):
    with open(path, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX); f.write(json.dumps(obj, ensure_ascii=False) + "\n"); f.flush()

def seen_ids():
    ids = set()
    for p in (PROCESSED, LOG):
        try:
            for l in open(p, encoding="utf-8", errors="replace"):
                if "closeout" not in l: continue
                try: o = json.loads(l)
                except ValueError: continue
                i = o.get("closeout_id") if p == LOG else o.get("id")
                if i: ids.add(i)
        except FileNotFoundError: pass
    return ids

def az(ts):
    try: t = datetime.datetime.fromisoformat(ts).astimezone(AZ)
    except Exception: t = datetime.datetime.now(AZ)
    return t, t.strftime("%b %-d, %-I:%M %p") + " " + TZL

def apply(c, ids):
    cid, job, owner, ans = c.get("id"), c.get("job"), c.get("owner"), c.get("answer") or ""
    if not (isinstance(cid, str) and isinstance(job, str) and isinstance(owner, str) and isinstance(ans, str)) \
       or not job.strip() or not owner.strip() or len(job) >= 300 or len(owner) >= 300 or len(ans) >= 2000 \
       or c.get("action") != "done":
        note(f"closeout skipped (invalid): {str(c)[:200]}"); return False
    if cid in ids: return False
    t, when = az(c.get("ts"))
    ev = f"Closed by {WHO} on dashboard {when}" + (f"; answer: {ans}" if ans else "")
    append(LOG, {"timestamp": t.isoformat(timespec="seconds"), "job": job, "owner": owner, "state": "done",
                 "evidence": ev, "next_step": f"Owner bot: act on {WHO}\u2019s answer" if ans else "none",
                 "source": "dashboard", "closeout_id": cid})
    if ans:
        new_decisions[0] += 1
        append(DECISIONS, {"ts": t.isoformat(timespec="seconds"), "owner": owner, "job": job, "answer": ans,
                           "status": "new", "closeout_id": cid})
    append(PROCESSED, {"id": cid, "ts": t.isoformat(timespec="seconds"), "job": job, "owner": owner, "answer": ans,
                       "applied": datetime.datetime.now(AZ).isoformat(timespec="seconds")})
    ids.add(cid)
    note(f"closeout applied: {owner} / {job[:80]}" + (" (with answer -> decisions.jsonl)" if ans else ""))
    return True

new_decisions = [0]
def notify(count):
    """POST once to the routine webhook; retry (3x, backoff) only on network errors / 5xx. Logs status codes only."""
    if not (WH_URL and WH_KEY): note(f"decisions webhook: not configured, {count} new decision(s) not announced"); return
    body = json.dumps({"event": "new_decisions", "count": count}).encode()
    for attempt in range(4):
        if attempt: time.sleep(5 * 2 ** (attempt - 1))  # 5s, 10s, 20s
        req = urllib.request.Request(WH_URL, data=body, method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + WH_KEY})
        try:
            with urllib.request.urlopen(req, timeout=10) as r: code = r.status
        except urllib.error.HTTPError as e: code = e.code
        except Exception as e:
            note(f"decisions webhook: attempt {attempt + 1} network error ({type(e).__name__})"); continue
        if 500 <= code < 600: note(f"decisions webhook: attempt {attempt + 1} HTTP {code}"); continue
        note(f"decisions webhook: HTTP {code} ({count} new decision(s))"); return
    note("decisions webhook: gave up after 4 attempts")

_err = [None]
def pull():
    """Fetch new queue lines from the server; returns number applied."""
    try: off = int(open(STATE).read().strip() or 0)
    except Exception: off = 0
    try:
        # remote.py needs paramiko, which lives in .venv (the watcher itself runs on system python3)
        r = subprocess.run([os.path.join(HERE, ".venv", "bin", "python"), os.path.join(HERE, "remote.py"), "run",
                            f"s=$(stat -c %s {QUEUE} 2>/dev/null || echo 0); echo $s; "
                            f"if [ $s -gt {off} ]; then tail -c +{off + 1} {QUEUE} | head -c 1000000; fi"],
                           cwd=HERE, capture_output=True, timeout=60)
        if r.returncode: raise RuntimeError((r.stderr.decode(errors="replace").strip().splitlines() or ["exit %d" % r.returncode])[-1])
        raw = r.stdout.decode("utf-8", "replace")
    except Exception as ex:
        m = f"closeout pull failed: {type(ex).__name__}: {ex}"
        if m != _err[0]: note(m)
        _err[0] = m; return 0
    if _err[0]: note("closeout pull ok again"); _err[0] = None
    size_s, _, body = raw.partition("\n")
    try: size = int(size_s)
    except ValueError: return 0
    if size < off:   # queue was truncated/rotated: start over (ids keep it idempotent)
        note(f"queue shrank ({size} < {off}); rereading from 0"); open(STATE, "w").write("0"); return pull()
    if not body: return 0
    end = body.rfind("\n")
    if end < 0: return 0  # partial line still being written
    chunk = body[:end + 1]; n = 0; ids = seen_ids()
    for ln in chunk.splitlines():
        if not ln.strip(): continue
        try: n += apply(json.loads(ln), ids)
        except ValueError: note(f"closeout skipped (bad JSON): {ln[:200]}")
    open(STATE, "w").write(str(off + len(chunk.encode("utf-8"))))
    return n

def refresh(why):
    r = subprocess.run(["bash", os.path.join(HERE, "build_and_deploy.sh")], cwd=HERE, capture_output=True, text=True, timeout=600)
    tail = (r.stdout + r.stderr).strip().splitlines()[-2:]
    note(f"{why} -> refresh exit={r.returncode} {' | '.join(tail)}")

last = mt(); note("watcher started (log watch + closeout pull every %ss; decisions webhook %s)" % (PULL_EVERY, "configured" if WH_URL and WH_KEY else "NOT configured")); next_pull = 0
while True:
    time.sleep(20)
    if time.time() >= next_pull:
        next_pull = time.time() + PULL_EVERY
        new_decisions[0] = 0
        n = pull()
        if new_decisions[0]: notify(new_decisions[0])
        if n:
            refresh("dashboard closeout"); last = mt(); continue
    m = mt()
    if m == last: continue
    while True:  # debounce: wait until no change for 30s
        time.sleep(30); m2 = mt()
        if m2 == m: break
        m = m2
    last = m
    refresh("log or bot look changed")
