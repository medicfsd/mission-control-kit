#!/usr/bin/env python3
"""Verify the dashboard. Public site = passkey/password login (auth service + Caddy forward_auth); the server's
loopback copy (127.0.0.1:8088) keeps basic auth with a separate check login that never leaves the server
(/opt/team-dashboard/check.netrc, root-only, made by install.sh). Never prints the password or cookies.
Public password sign-in checks run only if box/.dashboard_password exists (optional, chmod 600; the owner decides). Exit code 1 if any check fails.
--data-only (used by build_and_deploy.sh when only data.json was pushed): skip the public password sign-in checks.
Health checks only: services, auth, TLS, public routes, data.json/tiles, briefing totals (no UI design strings)."""
import os, sys, subprocess, ssl, socket, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from remote import connect
from urllib.parse import quote
import mc_config as CFG
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_ONLY = "--data-only" in sys.argv[1:]
LOGDIR = CFG.LOG_DIR
USERNAME = CFG.C["login_username"]
TZL = CFG.tz_label()
_pwf = os.path.join(HERE, ".dashboard_password")
pw = open(_pwf).read().strip() if os.path.exists(_pwf) else None
if pw is None and not DATA_ONLY:
    DATA_ONLY = True; print("     no box/.dashboard_password: public password sign-in checks skipped")
c = connect()
def rc(cmd, stdin=None):
    i, o, e = c.exec_command(cmd, timeout=60)
    if stdin: i.write(stdin); i.channel.shutdown_write()
    return o.read().decode().strip()
HOST = rc("sed -n 's/^DASH_HOST=//p' /opt/team-dashboard/host.env 2>/dev/null") or CFG.need("dash_host")
BAD = []
def chk(label, got, want):
    ok = got in want if isinstance(want, (tuple, list, set)) else got == want
    if not ok: BAD.append(label)
    print(f"{'ok  ' if ok else 'FAIL'} {label}: {got or 'fail'} (want {want if not isinstance(want, (tuple, list, set)) else '/'.join(want)})")
for u in ("team-dashboard", "team-dashboard-auth", "team-dashboard-api"):
    chk(f"service {u}", rc(f"systemctl is-active {u}"), "active")
chk("auth service health (local)", rc("curl -s -m 10 http://127.0.0.1:8090/auth/health | python3 -c 'import sys,json;d=json.load(sys.stdin);print(\"ok\" if d[\"ok\"] and d[\"password\"] else \"bad\")'"), "ok")
print("     passkeys registered:", rc("curl -s -m 10 http://127.0.0.1:8090/auth/health | python3 -c 'import sys,json;print(json.load(sys.stdin)[\"passkeys\"])'"))
chk("loopback no-auth", rc("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8088/"), "401")
chk("loopback basic auth data.json (check login, stays on server)", rc("sudo curl -s --netrc-file /opt/team-dashboard/check.netrc -o /dev/null -w '%{http_code}' http://127.0.0.1:8088/data.json"), "200")
cert = rc(f"sudo test -s /opt/team-dashboard/data/caddy/certificates/acme-v02.api.letsencrypt.org-directory/{HOST}/{HOST}.crt && echo yes")
chk(f"TLS certificate stored for {HOST}", cert or "no", "yes")
c.close()
B = f"https://{HOST}"
def curl(args, stdin=None):
    return subprocess.run(["curl", "-s", "-m", "15"] + args, input=stdin, capture_output=True, text=True).stdout
try:
    ctx = ssl.create_default_context()
    with socket.create_connection((HOST, 443), timeout=15) as sk, ctx.wrap_socket(sk, server_hostname=HOST) as ss:
        exp = datetime.datetime.strptime(ss.getpeercert()["notAfter"], "%b %d %H:%M:%S %Y %Z")
    days = (exp - datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)).days
    chk("public TLS valid (days left)", "ok" if days > 7 else f"{days}d", "ok"); print(f"     cert expires in {days} days (Caddy auto-renews)")
except Exception as e:
    chk("public TLS valid", f"error {type(e).__name__}", "ok")
for path in ("/", "/index.html", "/bot.html"):
    chk(f"public {path} unauth", curl(["-o", "/dev/null", "-w", "%{http_code}", "-H", "Accept: text/html", B + path]), ("302", "401"))
for path in ("/data.json", "/api/closeouts", "/login/../data.json"):
    chk(f"public {path} unauth", curl(["--path-as-is", "-o", "/dev/null", "-w", "%{http_code}", B + path]), "401")
chk("public /login page", curl(["-o", "/dev/null", "-w", "%{http_code}", B + "/login"]), "200")
chk("public /auth/health hidden", curl(["-o", "/dev/null", "-w", "%{http_code}", B + "/auth/health"]), "404")
# password sign-in -> session cookie -> data.json (cookie kept in memory only)
if DATA_ONLY:
    print("     data-only run: public password sign-in checks skipped (run without --data-only after a --full deploy)")
else:
    hdr = curl(["-o", "/dev/null", "-D", "-", "-H", f"Origin: {B}", "--data-binary", "@-", B + "/auth/password"],
               f"username={quote(USERNAME)}&password=" + quote(pw, safe="") + "&next=%2F")
    ck = next((l.split(":", 1)[1].split(";")[0].strip() for l in hdr.splitlines() if l.lower().startswith("set-cookie:") and "__Host-mc_session=" in l), "")
    chk("public password sign-in sets session", "yes" if ck else "no", "yes")
    def pw_post(extra):   # returns "session" or the redirect target; password never printed
        h = curl(["-o", "/dev/null", "-D", "-"] + sum((["-H", x] for x in extra), []) + ["--data-binary", "@-", B + "/auth/password"],
                 f"username={quote(USERNAME)}&password=" + quote(pw, safe="") + "&next=%2F")
        if any(l.lower().startswith("set-cookie:") and "__Host-mc_session=" in l for l in h.splitlines()): return "session"
        return next((l.split(":", 1)[1].strip() for l in h.splitlines() if l.lower().startswith("location:")), "?")
    chk("iPhone Safari form POST (Origin: null, Sec-Fetch-Site: same-origin) signs in", pw_post(["Origin: null", "Sec-Fetch-Site: same-origin"]), "session")
    chk("form POST with Referer only signs in", pw_post([f"Referer: {B}/login"]), "session")
    chk("password POST with no Origin/Referer/Sec-Fetch (1Password-style) signs in", pw_post([]), "session")
    chk("passkey register/options with Origin: null rejected", curl(["-o", "/dev/null", "-w", "%{http_code}", "-X", "POST", "-H", "Content-Type: application/json", "-H", "X-Requested-With: mc", "-H", "Origin: null", "-d", "{}", B + "/auth/passkey/register/options"]), "403")
    chk("cross-site form POST rejected", pw_post(["Origin: https://evil.example", "Sec-Fetch-Site: cross-site"]), "/login?e=origin&next=/")
    chk("Referrer-Policy same-origin", curl(["-o", "/dev/null", "-w", "%header{referrer-policy}", B + "/login"]), "same-origin")
    if ck:
        chk("public data.json with session", curl(["-K", "-", "-o", "/dev/null", "-w", "%{http_code}", B + "/data.json"], f'header = "Cookie: {ck}"\n'), "200")
        for pg in ("/index.html", "/bot.html"):
            chk(f"public {pg} with session", curl(["-K", "-", "-o", "/dev/null", "-w", "%{http_code}", B + pg], f'header = "Cookie: {ck}"\n'), "200")
        chk("public /api/closeouts with session", curl(["-K", "-", "-o", "/dev/null", "-w", "%{http_code}", B + "/api/closeouts"], f'header = "Cookie: {ck}"\n'), "200")
# data consistency: stat tiles == cards on the board; briefing section totals exclude done/resolved items
import json, re
D = json.load(open(os.path.join(HERE, "www", "data.json")))
for k in ("red", "yellow", "blue"):
    chk(f"tile '{k}' equals its board cards", str(D["counts"][k]), str(sum(j["cat"] == k and not j.get("closing") for j in D["open"])))
bs = (D.get("briefing") or {}).get("sections", [])
# independent of build_data's matching: latest line per (owner, job) straight from the log, still "in progress",
# minus due-date reminders (kind "deadline" without a "... by <date>" goal), minus Done-button closeouts pending
_lat = {}
for _l in open(os.path.join(LOGDIR, "updates.jsonl"), encoding="utf-8", errors="replace"):
    try: _o = json.loads(_l)
    except Exception: continue
    if isinstance(_o, dict) and _o.get("job") and _o.get("timestamp"):
        _lat[(str(_o.get("owner", "")).strip().lower(), str(_o["job"]).strip().lower()[:80])] = _o
_rem = lambda o: str(o.get("kind", "")).lower() == "deadline" and not (re.search(r"\bby\b", str(o["job"]), re.I) and not re.search(r"\b(due|payment|appointment)\b", str(o["job"]), re.I))
_ip = {k for k, o in _lat.items() if str(o.get("state", "")).strip().lower() == "in progress" and not _rem(o)}
_closing = {(j["owner"].lower(), j["job"].lower()[:80]) for j in D["open"] if j.get("closing")}
_waiting = sum(1 for j in D["open"] if j["state"] == "in progress" and j["cat"] != "blue" and not j.get("closing"))
chk("tile 'In progress' (+ in-progress jobs shown as waiting) = latest-state 'in progress' jobs in the log after closeouts",
    str(D["counts"]["blue"] + _waiting), str(len(_ip - _closing)))
bad = [x["name"] for x in bs if re.match(r"(?i)^(red|yellow|amber|orange|blue|green|grey|gray)\b", x["name"])]
chk("briefing section labels have no color words (Red:/Blue:/…)", "ok" if not bad else ", ".join(bad), "ok")
chk("live section totals exclude done items", "ok" if all(x["open_now"] == sum(not (i.get("live") or {}).get("done") for i in x["items"]) for x in bs) else "bad", "ok")
# headless render of the deployed files (www/ + data.json): tapping each state tile shows exactly as many open board cards,
# and the briefing's matching state section header shows the same open count
def _render(state, path=None):
    import shutil, tempfile, socket as _s, time as _t
    chrome = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
    if not chrome: return None
    with _s.socket() as so: so.bind(("127.0.0.1", 0)); port = so.getsockname()[1]
    srv = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory", os.path.join(HERE, "www")],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        _t.sleep(0.8)
        with tempfile.TemporaryDirectory() as ud:
            try:
                return subprocess.run([chrome, "--headless", "--no-sandbox", "--disable-gpu", "--virtual-time-budget=3000",
                                       "--dump-dom", f"http://127.0.0.1:{port}/" + (path or f"index.html#state={state}")],
                                      capture_output=True, text=True, timeout=45, stdin=subprocess.DEVNULL).stdout
            except subprocess.TimeoutExpired: return ""
    finally: srv.kill()
_SEC = {"red": CFG.NEEDS_LABEL, "yellow": "Waiting on another bot or an outside response", "blue": "In progress"}
for _k in ("red", "yellow", "blue"):
    _d = _render(_k)
    if _d is None: chk(f"headless {_k} tile = cards shown", "no chrome", "ok"); continue
    _tm = re.search(r"setF\('state','" + _k + r"'\)\}\"><b>(\d+)</b>", _d) or re.search(r"setF\('state','" + _k + r"'\)[^>]*>\s*<b>(\d+)</b>", _d)
    _kb = _d[_d.find('id="kanban"'):]; _kb = _kb[:_kb.find("</section>")] if "</section>" in _kb else _kb
    _cards = len(re.findall(r'<div class="card ' + _k + r'\b', _kb))
    _hm = re.search(r'data-sec="' + re.escape(_SEC[_k]) + r'"[^>]*>.*?<span>[^<]*?· (\d+)</span>', _d, re.S)
    _bn = int(_hm.group(1)) if _hm else 0
    chk(f"headless: '{_k}' tile = board cards shown when tapped = briefing '{_SEC[_k]}' open count",
        f"tile {_tm.group(1) if _tm else '?'}, cards {_cards}, briefing {_bn}", f"tile {_cards}, cards {_cards}, briefing {_cards}")
    _t3m = re.search(r'<div class="bsec[^"]*" data-sec="Top 3"', re.sub(r"<script>.*?</script>", "", _d, flags=re.S))   # the rendered Top 3 block only (absent when no briefing yet today)
    _ds = re.sub(r"<script>.*?</script>", "", _d, flags=re.S); _t3 = _ds[_t3m.start():] if _t3m else ""; _t3 = _t3[:_t3.find("</ol>")]
    _na = len((D.get("briefing") or {}).get("actions_live") or [])
    chk(f"headless ('{_k}' filter on): Top 3 renders all morning items, done ones struck", f"{_t3.count('<li')} items, {_t3.count('<li class=\"done')} struck",
        f"{_na} items, {sum(bool(a.get('done')) for a in (D.get('briefing') or {}).get('actions_live') or [])} struck")
# per-bot view: index.html?id=<id> renders that bot; old bot.html links redirect to it
_jb = next((b for b in D.get("bots", []) if b.get("id") and b.get("name") == CFG.C.get("coordinator")), None) or next((b for b in D.get("bots", []) if b.get("id") and not b.get("retired")), None)
if _jb:
    for _p in (f"index.html?id={_jb['id']}", f"bot.html?id={_jb['id']}"):
        _d = _render("", _p)
        if _d is None: chk(f"headless per-bot view {_p.split('?')[0]}", "no chrome", "ok"); continue
        _d = re.sub(r"<script>.*?</script>", "", _d, flags=re.S); _bv = _d[_d.find('id="bvmain"'):_d.find('id="mainview"')]
        _ok = f"<h1>{_jb['name']}</h1>" in _bv and "Open items" in _bv and 'id="mainview" hidden' in _d
        chk(f"headless per-bot view ({_p.split('?')[0]}?id=…) renders {_jb['name']}", "ok" if _ok else "missing", "ok")
_ret = [b["name"] for b in D.get("bots", []) if b.get("retired")]
if _ret:
    _d = _render("", "index.html")
    if _d is not None:
        _d = re.sub(r"<script>.*?</script>", "", _d, flags=re.S); _tm = _d[_d.find('id="team"'):]; _tm = _tm[:_tm.find("</section>")]
        _f = _d[_d.find('id="filters"'):_d.find('id="fcount"')]
        chk("headless main view: no team tile / Bot chip for retired bots", ", ".join(n for n in _ret if f">{n}<" in _tm + _f) or ("ok" if _tm.count("›") > 3 else "team not rendered"), "ok")
# data level: each state section of the briefing counts exactly the open board cards of that state
# (done-today items struck and not counted)
for _k in ("red", "yellow", "blue"):
    _ss = [x for x in bs if x.get("status") == _k]
    chk(f"briefing '{_k}' section open count = tile", str(sum(x["open_now"] for x in _ss)), str(D["counts"][_k]))
# live panel: state sections are built from the log; checked against the log independently of build_data
_today = datetime.datetime.now(CFG.TZ).date().isoformat()
_lk = lambda o, j: (str(o).strip().lower(), str(j).strip().lower()[:80])
_secopen = {_k: {_lk(m["owner"], m["job"]) for x in bs if x.get("status") == _k for i in x["items"] if not (i.get("live") or {}).get("done")
                 for m in i["live"]["matches"]} for _k in ("red", "yellow", "blue")}
for _k in ("red", "yellow", "blue"):
    _want = {_lk(j["owner"], j["job"]) for j in D["open"] if j["cat"] == _k and not j.get("closing")}
    chk(f"live '{_k}' section holds exactly the open '{_k}' jobs", "ok" if _secopen[_k] == _want else f"extra {len(_secopen[_k] - _want)}, missing {len(_want - _secopen[_k])}", "ok")
_logopen = {k for k, o in _lat.items() if str(o.get("state", "")).strip().lower() != "done" and not _rem(o)} - _closing
_all = set().union(*_secopen.values())
chk("live state sections = every open (not done, not reminder, not Done-sent) job in the log", "ok" if _all == _logopen else f"extra {sorted(_all - _logopen)[:3]}, missing {sorted(_logopen - _all)[:3]}", "ok")
_remk = {k for k, o in _lat.items() if _rem(o)}
_inrem = [i["head"][:40] for x in bs if x.get("status") for i in x["items"] for m in (i.get("live") or {}).get("matches", []) if _lk(m["owner"], m["job"]) in _remk]
chk("no due-date reminders in the live state sections", ", ".join(_inrem) or "none", "none")
_olddone = [i["head"][:40] for x in bs if x.get("status") for i in x["items"] if (i.get("live") or {}).get("done") and (i["live"].get("done_day") or _today) != _today]
chk("struck done items are from today (your time zone) only", ", ".join(_olddone) or "ok", "ok")
_bf = sorted(f for f in (os.listdir(os.path.join(LOGDIR, "briefings")) if os.path.isdir(os.path.join(LOGDIR, "briefings")) else []) if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", f))
_top = 0
if _bf:
    _in = False
    for _l in open(os.path.join(LOGDIR, "briefings", _bf[-1]), encoding="utf-8", errors="replace"):
        if _l.startswith("## "): _in = "action" in _l.lower()
        elif _in and re.match(r"^\s*(?:[-*]|\d+\.)\s+\S", _l): _top += 1
_B = D.get("briefing") or {}
if _bf and _bf[-1][:-3] == _today:
    chk("Top 3 = the morning briefing's numbered items (today's briefing file)", f"{len(_B.get('actions_live') or [])} items from {_B.get('top3_date')}", f"{_top} items from {_today}")
    chk("Top 3 has 3 items", str(len(_B.get("actions_live") or [])), "3")
else:  # no briefing yet today: Top 3 dropped off at midnight
    chk("Top 3 empty until today's morning briefing", f"{len(_B.get('actions_live') or [])} items, date {_B.get('top3_date')}", "0 items, date None")
_exc = [m for x in bs if x["name"] == "Exceptions" for i in x["items"] for m in (i.get("live") or {}).get("matches", [])]
for _so in CFG.C["stale_overrides"]:   # jobs with a longer stale threshold (config "stale_overrides")
    _hit = [m for m in _exc if m["owner"] == _so["owner"] and m["job"].lower() == _so["job"].lower()]
    _age = (datetime.datetime.now(CFG.TZ) - datetime.datetime.fromisoformat(_hit[0]["ts"])).days if _hit else 0
    chk(f"{_so['owner']} '{_so['job']}' ({_so['days']}-day stale rule) not flagged early", "flagged early" if _hit and _age < _so["days"] else "ok", "ok")
# Usage panel (optional): renders from data.json "usage"; bar counts match the data; collected_at shown
_U = D.get("usage")
print("     usage panel:", "data present" if _U else "no usage/latest.json (panel stays empty; optional)")
_d = _render("", "index.html") if _U else None
if _U and _d is not None:
    _d = re.sub(r"<script>.*?</script>", "", _d, flags=re.S); _us = _d[_d.find('id="usage"'):]; _us = _us[:_us.find('<section class="panel"><h2>Team')]
    def _blk(i, nxt):
        a = _us.find(f'id="{i}"'); b = _us.find(f'id="{nxt}"') if nxt else len(_us); return _us[a:b] if a >= 0 else ""
    chk("headless: Usage panel renders", "ok" if 'id="ubody"' in _us and "ukpi" in _us else "missing", "ok")
    chk("headless: By bot bars = data", str(_blk("ubot", "uroutine").count('class="ubar"')), str(len(_U["by_bot"])))
    chk("headless: Top routines bars = data (non-summary rows)", str(_blk("uroutine", "uday").count('class="ubar"')), str(sum(not x.get("other") for x in _U["by_routine"])))
    chk("headless: By day bars = data", str(_blk("uday", "uact").count('class="uday"')), str(len(_U["by_day"])))
    chk("headless: By activity donut segments = data", str(_blk("uact", None).count('class="useg"')), str(len(_U["by_activity"])))
    _ca = datetime.datetime.fromisoformat(_U["collected_at"]).astimezone(CFG.TZ)
    _want = "Updated " + _ca.strftime("%b ") + str(_ca.day) + ", " + _ca.strftime("%I:%M %p").lstrip("0") + " " + TZL
    chk(f"headless: Usage shows collected_at (Updated … {TZL})", "ok" if _want in _us else "missing " + _want, "ok")
    chk(f"headless: reset time labelled {TZL}", "ok" if f"limit resets ({TZL})" in _us else "missing", "ok")
print("ALL OK" if not BAD else f"{len(BAD)} check(s) failed: " + ", ".join(BAD))
sys.exit(1 if BAD else 0)
