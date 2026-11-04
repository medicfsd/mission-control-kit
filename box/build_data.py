#!/usr/bin/env python3
"""Convert the team log into www/data.json for the dashboard. Skips malformed lines, redacts
anything that looks like a credential, trims long text. Reads bot names/avatar shape+color
from <agent_data_dir>/agents/<id>/profile.json each run (see config.json)."""
import json, re, os, glob, datetime as dt
import mc_config as CFG
AZ = CFG.TZ          # your time zone (config.json "timezone"); name kept short for the code below
BASE = CFG.C["agent_data_dir"]
LOG = CFG.LOG
BRIEF = os.path.join(CFG.LOG_DIR, "briefings")
SETUP = os.path.join(CFG.LOG_DIR, "setup-improvements.jsonl")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "www", "data.json")

# Team roster: display name, role, agent id (profile.json supplies avatarShape/avatarColor).
# From config.json "team", or discovered from <agent_data_dir>/agents/*/profile.json when that list is empty.
TEAM = CFG.team()
# Retired bots: off the roster; their old log lines stay as history but they get no team tile or filter chip
RETIRED = dict(CFG.C["retired"])
# App color names -> hex, copied from the Grok Bot app's own palette (GROK_BOT_COLORS in
# /opt/sand/sand-host/host-main.cjs, ../packages/grok-bot/dist/colors.js; same values on x.ai).
COLORS = {"black":"#000000","brown":"#936439","red":"#FF263C","orange":"#FF6700","yellow":"#FF9800",
          "green":"#00C972","cyan":"#00BCA6","blue":"#1084FE","violet":"#9159FE","magenta":"#FF309B",
          "gray":"#777777"}
# The app's last-resort color ("?? 'black'" in mark-deal.js). A bot with no stored or unknown
# avatarColor gets the app's dealt hue for its agent id (app_dealt_color), exactly like the app.
DEFAULT = "#000000"
_DEALT_HUES = [k for k in COLORS if k != "black"]
def _imul(a, b): return ((a & 0xFFFFFFFF) * (b & 0xFFFFFFFF)) & 0xFFFFFFFF
def app_dealt_color(key):
    """Port of grokBotMarkColorForKey (mark-deal.js): FNV-1a hash -> seeded PRNG -> hue name."""
    h = 2166136261
    for ch in key: h = _imul(h ^ ord(ch), 16777619)
    seed = (h ^ _imul(1, 2654435769)) & 0xFFFFFFFF          # grokBotMarkSeed, PRODUCT_SEED=1
    st = (seed ^ _imul(1, 2654435769)) & 0xFFFFFFFF          # HUE_STREAM_SEED=1
    st = (st + 1831565813) & 0xFFFFFFFF
    v = _imul(st ^ (st >> 15), 1 | st)
    v = ((v + _imul(v ^ (v >> 7), 61 | v)) & 0xFFFFFFFF) ^ v
    r = ((v ^ (v >> 14)) & 0xFFFFFFFF) / 4294967296
    return _DEALT_HUES[int(r * len(_DEALT_HUES))]
ALIASES = {str(k).lower(): v for k, v in CFG.C["aliases"].items()}

SECRET_PATTERNS = [
 (re.compile(r"(?i)\b(pass(word)?|passwd|pwd|secret|token|api[_-]?key|private[_ -]?key|seed phrase|mnemonic|otp|2fa code)\b\s*[:=]\s*\S+"), r"\1: [redacted]"),
 (re.compile(r"\b(sk|pk|ghp|gho|xox[abp]|AKIA)[-_A-Za-z0-9]{12,}\b"), "[redacted]"),
 (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|$)", re.S), "[redacted key]"),
 (re.compile(r"\b[0-9a-fA-F]{48,}\b"), "[redacted hex]"),
 (re.compile(r"\b\d{6}\b(?=[^\n]{0,20}\b(code|otp)\b)", re.I), "[code]"),
 (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[redacted number]"),
]
BASE58_LONG = re.compile(r"(?<![/=A-Za-z0-9])[1-9A-HJ-NP-Za-km-z]{60,}(?![A-Za-z0-9])")
EMAIL = re.compile(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")

def clean(s, n):
    if s is None: return ""
    if not isinstance(s, str): s = json.dumps(s)
    for p, r in SECRET_PATTERNS: s = p.sub(r, s)
    s = BASE58_LONG.sub(lambda m: m.group(0)[:6] + "…", s)   # long tx sigs / keys shortened
    s = EMAIL.sub(lambda m: f"{m.group(1)}…@{m.group(2)}", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[:n-1].rstrip() + "…"

def norm_owner(o):
    if not o: return "Unknown"
    o = str(o).strip(); k = o.lower()
    if k in ALIASES: return ALIASES[k]
    for name, _, _ in TEAM:
        if k == name.lower() or k.startswith(name.lower()+" "): return name
    return o

STATES = {"needs decision","blocked","done","in progress"}
def parse_ts(s):
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z","+00:00"))
        if t.tzinfo is None: t = t.replace(tzinfo=AZ)
        return t.astimezone(AZ)
    except Exception: return None


BOTNAMES = [t[0] for t in TEAM]
_ON = "|".join(re.escape(n) for n in CFG.OWNER_NAMES) or "the owner"
OWNER_WAIT = re.compile(rf"(waiting (?:on|for) (?:{_ON}|you)\b|needs? (?:{_ON})\b|(?:{_ON})(?:'s)? (?:ok|okay|approval|go-ahead|call|decision|sign-?in)\b|"
    rf"\b(?:{_ON}) (?:posts|trims|signs|approves|decides|confirms|pastes|reviews|picks|sends|replies|chooses|answers|must|needs to|to |logs in|uploads)|"
    rf"\b(?:his|her|their|your) (?:ok|okay|approval|go-ahead)\b|\bapprov(?:e|al) from (?:{_ON}|you)\b)", re.I)

def bot_wait(text, owner):
    for b in BOTNAMES:
        if b == owner: continue
        if re.search(r"(?:waiting|wait|waits|pending|until|blocked|depends)\s+(?:on|for)?\s*(?:bot:)?\s*" + re.escape(b) + r"\b", text, re.I):
            return b
    return None
def categorize(e):
    """red = needs the owner (you), yellow = waiting on another bot, blue = in progress, green = done."""
    st = e["state"]; txt = f"{e.get('next','')} {e.get('evidence','')}"; bo = (e.get("blocked_on") or "").lower()
    if st == "done": return "green", "done"
    if st == "needs decision": return "red", "needs your decision"
    if st == "blocked":
        if bo.startswith("bot:"): return "yellow", "waiting on " + bo[4:].strip()
        if bo == "me" or OWNER_WAIT.search(txt): return "red", "blocked on you"
        b = bot_wait(txt, e["owner"])
        if b: return "yellow", "waiting on " + b
        return "red", "blocked (" + (bo or "needs attention") + ")"
    b = bot_wait(e.get("next", ""), e["owner"])
    if b: return "yellow", "waiting on " + b
    if OWNER_WAIT.search(e.get("next", "")): return "red", "waiting on you"
    return "blue", "in progress"

def parse_briefing():
    files = sorted(f for f in glob.glob(f"{BRIEF}/*.md") if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", os.path.basename(f)))
    if not files: return None
    txt = open(files[-1], encoding="utf-8", errors="replace").read()
    lines = txt.splitlines()
    title = lines[0].lstrip("# ").strip() if lines else ""
    sections, cur = [], None
    for l in lines[1:]:
        if l.startswith("## ") or (l.startswith("### ") and sections):
            # "### Blue: in progress" under "## All open items" is its own section, so its header count is live too
            nm = l.lstrip("#").strip()
            # "Red: needs <owner>" -> "Needs <owner>" (status kept separately for the bar color); briefing files untouched
            cm = re.match(r"(?i)^(red|yellow|amber|orange|blue|green|grey|gray)\s*[:\-\u2013\u2014]\s*(.+)$", nm)
            st = {"amber": "yellow", "orange": "yellow", "grey": "", "gray": ""}.get(cm.group(1).lower(), cm.group(1).lower()) if cm else ""
            if cm: nm = cm.group(2)[:1].upper() + cm.group(2)[1:]
            for _o in CFG.OWNER_NAMES: nm = re.sub(r"(?i)\b" + re.escape(_o) + r"\b", _o, nm)
            cur = {"name": clean(nm, 60), "items": [], **({"status": st} if st else {}), **({"parent": sections[-1].get("parent") or sections[-1]["name"]} if l.startswith("### ") else {})}
            sections.append(cur); continue
        if cur is None: continue
        m = re.match(r"^\s*(?:[-*]|\d+\.)\s+(.*)", l)
        if not m: continue
        body = m.group(1)
        hm = re.match(r"\*\*(.+?)\*\*\s*(.*)", body)
        head, rest = (hm.group(1), hm.group(2)) if hm else ("", body)
        head = head.rstrip(".")
        om = re.search(r"\(([^()]+)\)\s*$", head)
        owners = []; oldfmt = False
        dm = re.match(r"^([^—–]{2,40}?)\s+[—–]\s+(.+)$", head)  # newer format: "Owner — job"
        if dm and norm_owner(dm.group(1).strip()) in {t[0] for t in TEAM}:
            owners = [norm_owner(dm.group(1).strip())]; head = dm.group(2).strip()
        elif om:
            oldfmt = True
            for o in re.split(r",|&| and ", om.group(1)):
                n = norm_owner(o.strip())
                if n in {t[0] for t in TEAM}: owners.append(n)
        rest = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1", rest)
        rest = re.sub(r"`([^`]*)`", r"\1", rest).replace("**", "")
        if not head and re.fullmatch(r"\s*none\.?\s*", rest, re.I): continue   # "- None." placeholder
        mentions = [] if owners else [n for n, _, _ in TEAM if re.search(r"\b" + re.escape(n) + r"\b", head, re.I)]
        cur["items"].append({"head": clean(re.sub(r"\s*\([^()]*\)\s*$", "", head) if owners and oldfmt else head, 120),
                             "owners": owners, "text": clean(rest, 260), **({"mentions": mentions} if mentions else {})})
    page = re.search(r"Page:\s*\[[^\]]*\]\((https://[^)]+)\)", txt)
    actions = []
    for sec in sections:
        if "action" in sec["name"].lower(): actions = [i["head"] + (" " if i["head"] else "") + i["text"] for i in sec["items"]]
    tm = re.search(r"\((\d{1,2}:\d{2}\s*[AP]M)", title)
    ran = dt.datetime.fromtimestamp(os.path.getmtime(files[-1]), AZ).isoformat()
    return {"date": os.path.basename(files[-1])[:-3], "title": clean(title, 120), "time": tm.group(1) if tm else "", "ran": ran,
            "page": page.group(1) if page else None,
            "actions": [a.strip() for a in actions if a.strip()],
            "sections": [x for x in sections if "action" not in x["name"].lower() and (x["items"] or not any(y.get("parent") == x["name"] for y in sections))]}

# ---- Top 3 from the morning briefing: match its items/actions to the current team log ----
_STOP = set("""a an the and or to of on in at for with from by is are was be it its this that these those you your me my
him his them they we our as up out into about now just so then than too very can will should would need needs
tell ask send get make keep have has had do does done not no yes all any some more most other two one""".split())
_DATEW = set("mon tue wed thu fri sat sun monday tuesday wednesday thursday friday saturday sunday jan feb mar apr may jun jul aug sep sept oct nov dec today tomorrow yesterday morning".split())
_GENERIC = {"post","video","clip","reminder","item","entry","job","task","x"}
def _norm(t): return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", str(t).lower().replace("'", "").replace("\u2019", ""))).strip()
def _stem(w):
    if w.endswith("ies") and len(w) > 4: return w[:-3] + "y"
    if w.endswith("s") and len(w) > 3 and not w.endswith("ss"): return w[:-1]
    return w
def _toks(t, drop=()):
    out = set()
    for w in _norm(t).split():
        w = _stem(w)
        if w in _STOP or w in _DATEW or w.isdigit() or len(w) < 2 or w in drop: continue
        out.add(w)
    return out
_MON = set("jan feb mar apr may jun jul aug sep sept oct nov dec january february march april june july august september october november december".split())
def _dsig(t):
    """month words + numbers in a job name: 'by Tue Dec 1' -> {dec,1}; weekdays ignored. Different deadlines = different jobs."""
    return {w[:3] for w in _norm(t).split() if w in _MON} | {w for w in _norm(t).split() if w.isdigit()}
_BOTTOK = set()
for _n, _, _ in TEAM: _BOTTOK |= set(_norm(_n).split())

def match_job(head, owner, jobs):
    """Latest-per-job entries of this owner whose job name equals the head (normalized) or contains it / is contained in it."""
    h = _norm(head)
    if len(h) < 8: return []
    mine = [j for j in jobs if j["owner"] == owner]
    exact = [j for j in mine if _norm(j["job"]) == h]
    H0 = _toks(head)
    same = [j for j in mine if len(H0) >= 2 and _toks(j["job"]) == H0 and _dsig(j["job"]) == _dsig(head)]
    if exact: return [max(exact + same, key=lambda j: j["ts"])]  # a reworded newer entry for the same job wins
    sub = [j for j in mine if len(_norm(j["job"])) >= 8 and (h in _norm(j["job"]) or _norm(j["job"]) in h) and _dsig(j["job"]) >= _dsig(head)]
    # same words once dates/weekdays are ignored (e.g. "by Dec 1" vs "by Tue Dec 1"); newest wins
    H = _toks(head)
    if len(H) >= 2:
        tok = [j for j in mine if _toks(j["job"]) == H and _dsig(j["job"]) == _dsig(head)]
        if tok:
            best = max(tok + sub, key=lambda j: j["ts"])
            return [best]
    return sorted(sub, key=lambda j: j["ts"], reverse=True)

SECNAMES = (("red", CFG.NEEDS_LABEL), ("yellow", "Waiting on another bot or an outside response"), ("blue", "In progress"))
STALE_DAYS = CFG.C["stale_days"]
# per-job stale threshold (days), keyed by (owner, job lowercased), for jobs that only update monthly (config "stale_overrides")
STALE_OVERRIDE = {(o["owner"], o["job"].lower()): o["days"] for o in CFG.C["stale_overrides"]}
def _m(j): return {"owner": j["owner"], "line": j["line"], "job": j["job"], "state": j["state"], "cat": j["cat"], "why": j["why"],
                   "ts": j["ts"], "evidence": j["evidence"], "next": j["next"]}
def top3_live(brief, jobs):
    """Top 3 = the morning briefing's numbered list (chosen by the morning run); each line's done status is live:
    it links to the briefing items it names, and those are matched to the team log."""
    # a card deadline that was paid and auto-rolled to next month's due date (roll_cards.py) counts as done for
    # today's Top 3, so the rolled entry under the same job name doesn't reopen it
    def _rolled(j):
        return (j.get("kind") == "deadline" and str(j.get("evidence", "")).startswith("Auto-rolled")
                and str(j.get("due_date") or "") > brief["date"])
    jobs = [dict(j, state="done") if _rolled(j) else j for j in jobs]
    items = []
    for sec in brief["sections"]:
        for it in sec["items"]:
            if not it.get("head") or not it.get("owners"): continue
            per = []
            for o in it["owners"]:
                m = match_job(it["head"], o, jobs)
                if m:   # several substring matches: open ones win (conservative), else the newest done one
                    per.append(sorted(m, key=lambda j: (j["state"] == "done", -dt.datetime.fromisoformat(j["ts"]).timestamp()))[0])
            if per: items.append((it, per))
    heads = [(it, per, _toks(it["head"], _GENERIC | _BOTTOK)) for it, per in items]
    freq = {}
    for _, _, H in heads:
        for w in H: freq[w] = freq.get(w, 0) + 1
    try: bstart = dt.datetime.strptime(brief["date"] + " " + brief["time"].replace(" ", ""), "%Y-%m-%d %I:%M%p").replace(tzinfo=AZ)
    except Exception: bstart = dt.datetime.fromisoformat(brief["ran"]) if brief.get("ran") else None
    out = []
    for a in brief.get("actions", []):
        rec = {"text": a, "links": [], "done": False, "done_ts": None}
        if re.match(r"^\s*(nothing|no other|no more|that'?s all|all clear|none)\b", a, re.I):
            out.append(rec); continue
        named = [n for n, _, _ in TEAM if re.search(r"\b" + re.escape(n) + r"\b", a, re.I)]
        A = _toks(re.sub(r"\S*/\S*", " ", a), _GENERIC | _BOTTOK)   # file paths/URLs don't name items ("/workspace/followers/…")
        for it, per, H in heads:
            if named and not set(it["owners"]) & set(named): continue
            hit = H & A
            if hit and (len(hit) * 2 >= len(H) or any(freq[w] == 1 and len(w) >= 3 for w in hit)):
                rec["links"].append({"head": it["head"], "hit": sorted(hit), "done": all(j["state"] == "done" for j in per),
                                     "state": "done" if all(j["state"] == "done" for j in per) else next(j["state"] for j in per if j["state"] != "done"),
                                     "ts": max(j["ts"] for j in per), "lines": [j["line"] for j in per]})
        # drop a link whose matched words are a strict subset of another link's (e.g. a line naming one card
        # also hitting a second card that shares the word "card")
        rec["links"] = [l for l in rec["links"] if not any(set(l["hit"]) < set(o["hit"]) for o in rec["links"])]
        if rec["links"] and all(l["done"] for l in rec["links"]):
            rec["done"] = True; rec["done_ts"] = max(l["ts"] for l in rec["links"])
        elif rec["links"] and bstart and re.search(r"\b(ask|get)\b.*\b(fresh|update|updated|status|count|check)\b", a, re.I) \
                and all(dt.datetime.fromisoformat(l["ts"]) > bstart for l in rec["links"]):
            # "ask a bot for an update" is done once every linked item has a newer log entry than the briefing
            rec["done"] = True; rec["done_ts"] = max(l["ts"] for l in rec["links"]); rec["done_why"] = "updated after briefing"
        out.append(rec)
    # a Top 3 line that went done stays done (struck) for that briefing's day, even if a linked job is reopened or reworded
    sticky_p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "briefing_sticky_done.json")
    try: sticky = json.load(open(sticky_p))
    except Exception: sticky = {}
    if sticky.get("date") != brief["date"]: sticky = {"date": brief["date"], "actions": {}}
    sticky = {"date": sticky["date"], "actions": sticky.get("actions", {})}
    for n, rec in enumerate(out):
        key = f"{n} | {rec['text'][:80]}"
        if rec["done"]: sticky["actions"].setdefault(key, rec["done_ts"])
        elif key in sticky["actions"]: rec.update({"done": True, "done_ts": sticky["actions"][key], "sticky": True})
    try:
        tmp = sticky_p + ".tmp"; json.dump(sticky, open(tmp, "w"), ensure_ascii=False, indent=1); os.replace(tmp, sticky_p)
    except Exception: pass
    return out

def live_panel(brief, jobs, now, bad_lines):
    """Today's panel, built from the team log on every rebuild each open job sits in the section for its
    current state; jobs that went done today (your time zone) stay struck in the section they were in until midnight; due-date
    reminders only in Upcoming. Top 3 still comes from the morning briefing file (chosen by the morning run)."""
    today = now.date().isoformat()
    az_day = lambda ts: dt.datetime.fromisoformat(ts).astimezone(AZ).date().isoformat()
    def item(j, done=False, why=None):
        txt = ("Next step: " + j["next"]) if j["next"] else j["evidence"]
        return {"head": j["job"], "owners": [j["owner"]], "text": clean(txt, 260),
                "live": {"state": "done" if done else j["state"], "cat": "green" if done else j["cat"], "why": why or j["why"],
                         "done": done, "ts": j["ts"], **({"done_day": az_day(j["ts"])} if done else {}), "matches": [_m(j)]}}
    secs = {c: {"name": n, "status": c, "items": []} for c, n in SECNAMES}
    board = [j for j in jobs if not j.get("_dl")]
    for j in sorted(board, key=lambda j: j["ts"], reverse=True):
        if j["state"] != "done":
            if j.get("closing"): secs[j["cat"]]["items"].append(item(j, True, "Done sent from the dashboard, waiting for the log"))
            else: secs[j["cat"]]["items"].append(item(j))
        elif az_day(j["ts"]) == today:
            secs[j.get("prev_cat") or "blue"]["items"].append(item(j, True, "done"))
    sections = [secs[c] for c, _ in SECNAMES]
    exc = []
    for j in sorted((j for j in board if j["state"] != "done" and not j.get("closing")), key=lambda j: j["ts"]):
        age = (now - dt.datetime.fromisoformat(j["ts"])).total_seconds() / 86400
        if age >= STALE_OVERRIDE.get((j["owner"], j["job"].lower()), STALE_DAYS):
            exc.append({**item(j), "text": clean(f"Stale: no log update for {int(age)} days (last {az_day(j['ts'])}). " + (("Next step: " + j["next"]) if j["next"] else ""), 260)})
    if bad_lines:
        exc.append({"head": "Team log lines the dashboard can't read", "owners": [], "text": "Lines " + ", ".join(map(str, bad_lines[:20])) + (" …" if len(bad_lines) > 20 else "")})
    sections.append({"name": "Exceptions", "items": exc})
    chg = []
    for j in sorted(board, key=lambda j: j["ts"], reverse=True):
        if az_day(j["ts"]) != today: continue
        before = j.get("state_before_today")
        if before == j["state"]: continue
        chg.append({"head": j["job"], "owners": [j["owner"]],
                    "text": (f"{before} → {j['state']}" if before else "New today: " + (j["state"] if j.get("first_state_today", j["state"]) == j["state"] else f"{j['first_state_today']} → {j['state']}")) + f" ({dt.datetime.fromisoformat(j['ts']).astimezone(AZ).strftime('%-I:%M %p')})"})
    sections.append({"name": "Changes today", "items": chg})
    for sec in sections:
        sec["done_now"] = sum(1 for it in sec["items"] if (it.get("live") or {}).get("done"))
        sec["open_now"] = len(sec["items"]) - sec["done_now"]
    out = {"live": True, "date": today, "live_as_of": now.isoformat(), "sections": sections, "actions": [], "actions_live": []}
    if brief and brief["date"] == today:  # Top 3 drops off at midnight (your time zone); the next morning briefing picks a new one
        out.update({"top3_date": brief["date"], "top3_ran": brief.get("ran"), "page": brief.get("page"),
                    "actions": brief["actions"], "actions_live": top3_live(brief, jobs)})
    return out

# Needs-owner (red) items come in two kinds. Optional log field "ask": "approval" | "question"; else this heuristic.
APPROVAL = re.compile(r"\b(approv\w*|approve/deny|ok(?:ay)? to|sign[- ]?off|confirm before|permission|go-?ahead|"
    r"buy|purchas\w*|order(?!\s+to\b)|send (?:an? |the |this |that )?(?:e-?mail|dm|message|reply|text))\b", re.I)
URL = re.compile(r"https://[^\s<>\"'`)\]]+")
def ask_kind(e, raw_ask):
    a = str(raw_ask or "").strip().lower()
    if a in ("approval", "question"): return a, "log field ask"
    return ("approval", "heuristic") if APPROVAL.search(" ".join((e["job"], e["evidence"], e["next"]))) else ("question", "heuristic")
CLOSEOUTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "closeouts_processed.jsonl")  # written by watch_log.py

def recent_closeouts(now, jobs):
    """Dashboard Done-button closeouts the box has pulled (last 48h). pending = log does not show the job done yet."""
    out = []
    try:
        for l in open(CLOSEOUTS, encoding="utf-8"):
            try: c = json.loads(l)
            except Exception: continue
            t = parse_ts(c.get("ts"))
            if not t or (now - t).total_seconds() > 48*3600: continue
            j = next((j for j in jobs if j["owner"] == c.get("owner") and j["job"].lower()[:80] == str(c.get("job","")).lower()[:80]), None)
            out.append({"id":c.get("id"),"ts":t.isoformat(),"job":clean(c.get("job"),140),"owner":c.get("owner"),
                        "has_answer":bool(c.get("answer")),"pending":not (j and j["state"] == "done")})
    except FileNotFoundError: pass
    return out[-100:]

# ---- Upcoming: due dates / deadlines. The team log (lines with due_date) is the ONLY source. ----
UPC_BACK, UPC_AHEAD = CFG.C["upcoming_days_back"], CFG.C["upcoming_days_ahead"]   # overdue items stay shown (red) this many days back; window ahead
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")
def _date(s):
    s = str(s or "").strip()[:10]
    if not DATE_RE.match(s): return None
    try: return dt.date.fromisoformat(s)
    except ValueError: return None
def _time(s):
    s = str(s or "").strip()
    return s if TIME_RE.match(s) else None
def _money(v):
    if v is None or v == "": return None
    try: return round(float(str(v).replace("$", "").replace(",", "")), 2)
    except ValueError: return None
def _autopay(v):
    if isinstance(v, bool): return v
    s = str(v or "").strip().lower()
    if s in ("true", "yes", "on", "enabled", "1"): return True
    if s in ("false", "no", "off", "disabled", "0"): return False
    return None   # unknown
def _upc(date, title, owner, category, source, kind, **extra):
    it = {"date": date.isoformat(), "time": extra.pop("time", None), "title": clean(title, 160), "owner": owner,
          "category": clean(category, 40) if category else "", "source": source, "kind": kind or "deadline",
          "detail": clean(extra.pop("detail", ""), 220), "src_note": clean(extra.pop("src_note", ""), 220)}
    it.update({k: v for k, v in extra.items() if v is not None})
    return it

def upcoming_from_log(jobs_latest):
    """Team-log lines with a due_date (or kind:deadline). Latest entry per (owner, job) wins; done drops off.
    Legacy: an open job whose latest entry has a YYYY-MM-DD 'deadline' is also shown."""
    out = []
    for e in jobs_latest:
        if e["state"] == "done": continue
        d = _date(e.get("due_date")) or _date(e.get("deadline"))
        if not d: continue
        out.append(_upc(d, e["job"], e["owner"], e.get("category") or "", "team log", e.get("kind") or "deadline",
                        time=_time(e.get("due_time")), detail=e.get("next") or "", src_note=e.get("evidence") or "",
                        min_due=e.get("min_due"), balance=e.get("balance"), autopay=e.get("autopay"),
                        last4=e.get("last4") or None, updated=e["ts"],
                        log_job={"job": e["job"], "owner": e["owner"], "state": e["state"], "ts": e["ts"],
                                 "cat": "blue", "urls": e.get("urls") or []}))
    return out, []

def _tkey(s): return re.sub(r"[^a-z0-9]+", " ", str(s).lower().replace("\u2026", "")).strip()
def build_upcoming(now, jobs_latest):
    today = now.date(); lo, hi = today - dt.timedelta(days=UPC_BACK), today + dt.timedelta(days=UPC_AHEAD)
    items, notes, seen = [], [], set()
    try: got, n = upcoming_from_log(jobs_latest)
    except Exception as ex: got, n = [], [f"team log: failed ({type(ex).__name__})"]
    notes += n
    for it in got:
        k = (it["owner"].lower(), _tkey(it["title"]), it["date"])
        if k in seen: continue
        seen.add(k)
        d = dt.date.fromisoformat(it["date"])
        if lo <= d <= hi: items.append(it)
    items.sort(key=lambda i: (i["date"], i.get("time") or "99:99", i["title"].lower()))
    return {"items": items, "notes": notes, "window_days": UPC_AHEAD, "today": today.isoformat()}

def load_usage():
    """team-log/usage/latest.json (written by your daily usage routine, validated by update_usage.py); None if missing/invalid."""
    try:
        import sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import update_usage as U
        d = U.load()
        if U.validate(d): print("usage/latest.json invalid; Usage panel left empty"); return None
        return d
    except FileNotFoundError: return None
    except Exception as x: print("usage/latest.json unreadable:", x); return None

def main():
    now = dt.datetime.now(AZ)
    entries, bad = [], []
    if not os.path.exists(LOG): raise SystemExit(f"team log not found: {LOG} (config.json team_log_dir)")
    with open(LOG, encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f, 1):
            if not line.strip(): continue
            try: o = json.loads(line)
            except Exception: bad.append(i); continue
            if not isinstance(o, dict): bad.append(i); continue
            if "timestamp" not in o and "ts" in o:   # off-format line, e.g. {"ts","from","type","text"}
                o = {"timestamp":o.get("ts"),"owner":o.get("from"),"state":o.get("type"),
                     "job":o.get("text"),"evidence":"","next_step":"","_offformat":True}
            t = parse_ts(o.get("timestamp")); st = str(o.get("state","")).strip().lower()
            if not t or st not in STATES or not o.get("job"): bad.append(i); continue
            entries.append({"line":i,"ts":t.isoformat(),"owner":norm_owner(o.get("owner")),
                "job":clean(o.get("job"),140),"state":st,"evidence":clean(o.get("evidence"),220),
                "next":clean(o.get("next_step"),220),"blocked_on":clean(o.get("blocked_on"),60),
                "deadline":clean(o.get("deadline"),40),"offformat":bool(o.get("_offformat")),
                "urls":list(dict.fromkeys(u.rstrip(".,;:!?") for u in URL.findall(clean(o.get("evidence"),2000)+" "+clean(o.get("next_step"),2000))))[:3],
                "_ask":o.get("ask"),
                **({k: clean(o.get(k), 40) for k in ("kind","due_date","due_time","category") if o.get(k) not in (None, "")}),
                **({k: _money(o.get(k)) for k in ("min_due","balance") if _money(o.get(k)) is not None}),
                **({"autopay": _autopay(o.get("autopay"))} if "autopay" in o else {}),
                **({"last4": str(o.get("last4"))} if re.fullmatch(r"\d{4}", str(o.get("last4") or "")) else {})})
    entries.sort(key=lambda e: e["ts"])
    for e in entries:
        e["cat"], e["why"] = categorize(e)
        raw = e.pop("_ask")
        if e["cat"] == "red": e["ask"], e["ask_src"] = ask_kind(e, raw)
    latest, nupd, first, prevcat, before, firsttoday = {}, {}, {}, {}, {}, {}
    today = now.date().isoformat()
    for e in entries:
        k = (e["owner"], e["job"].lower()[:80])
        if k in latest and latest[k]["state"] != "done": prevcat[k] = latest[k]["cat"]   # section it was in before done
        if dt.datetime.fromisoformat(e["ts"]).astimezone(AZ).date().isoformat() < today: before[k] = e["state"]
        elif k not in before: firsttoday.setdefault(k, e["state"])
        latest[k] = e; nupd[k] = nupd.get(k, 0) + 1; first.setdefault(k, e["ts"])
    for k, e in latest.items():
        e["updates"] = nupd[k]; e["first_ts"] = first[k]
        if k in prevcat: e["prev_cat"] = prevcat[k]
        if k in before: e["state_before_today"] = before[k]
        elif k in firsttoday: e["first_state_today"] = firsttoday[k]
    jobs = sorted(latest.values(), key=lambda e: e["ts"], reverse=True)
    # pure deadline lines (kind "deadline") live in the Upcoming section, not on the Projects board / open counts
    is_goal = lambda j: bool(re.search(r"\bby\b", j["job"], re.I)) and not re.search(r"\b(due|payment|appointment)\b", j["job"], re.I)
    is_dl = lambda j: str(j.get("kind", "")).lower() == "deadline" and not is_goal(j)
    open_jobs = [j for j in jobs if j["state"] != "done" and not is_dl(j)]

    bots = []; known = set()
    _ci = [t[2] for t in TEAM if t[0] == CFG.C.get("coordinator")] or [t[2] for t in TEAM[:1]]
    global _COORD_ID; _COORD_ID = _ci[0] if _ci else ""
    for name, role, aid in TEAM:
        known.add(name)
        try: p = json.load(open(f"{BASE}/agents/{aid}/profile.json"))
        except Exception: p = {}
        cname = (p.get("avatarColor") or "").strip().lower()
        bots.append({"name":name,"role":role,"id":aid,"shape":(p.get("avatarShape") or "").strip().lower() or "circle",
            "shape_src":"profile.json avatarShape" if p.get("avatarShape") else "default (none stored)",
            "color":COLORS[cname] if cname in COLORS else COLORS.get(app_dealt_color(aid), DEFAULT),
            "color_name":cname if cname in COLORS else app_dealt_color(aid),
            "color_src":"profile.json avatarColor" if cname in COLORS else "app dealt hue for agent id (none/unknown stored)"})
    for o in sorted({e["owner"] for e in entries} - known):
        bots.append({"name":o,"role":"","id":_COORD_ID,"shape":"circle","shape_src":"default","color":COLORS["gray"],
                     "color_name":"gray","color_src":"not on roster", **({"retired":RETIRED[o]} if o in RETIRED else {})})
    for b in bots:
        mine = [e for e in entries if e["owner"] == b["name"]]
        mo = [j for j in open_jobs if j["owner"] == b["name"]]
        last = mine[-1] if mine else None
        recent = last and (now - dt.datetime.fromisoformat(last["ts"])).total_seconds() < 72*3600
        if any(j["cat"]=="red" for j in mo): s = "red"
        elif any(j["cat"]=="yellow" for j in mo): s = "yellow"
        elif recent: s = "blue"
        else: s = "idle"
        b.update({"status":s,"open":len(mo),"total":len(mine),
                  "red":sum(j["cat"]=="red" for j in mo),"yellow":sum(j["cat"]=="yellow" for j in mo),
                  "blue":sum(j["cat"]=="blue" for j in mo),
                  "last_ts":last["ts"] if last else None,"last_job":last["job"] if last else "No log entries yet",
                  "last_state":last["state"] if last else ""})

    days = [(now.date() - dt.timedelta(days=d)).isoformat() for d in range(13, -1, -1)]
    act = {d:{} for d in days}
    for e in entries:
        d = e["ts"][:10]
        if d in act: act[d][e["owner"]] = act[d].get(e["owner"],0) + 1

    for j in jobs:
        if is_dl(j): j["_dl"] = True
    closeouts = recent_closeouts(now, jobs)
    closing = {(c["owner"], str(c["job"]).lower()[:80]) for c in closeouts if c["pending"]}
    for j in open_jobs:
        if (j["owner"], j["job"].lower()[:80]) in closing and any(parse_ts(c["ts"]) >= parse_ts(j["ts"]) for c in closeouts
                if c["pending"] and (c["owner"], str(c["job"]).lower()[:80]) == (j["owner"], j["job"].lower()[:80])): j["closing"] = True
    brief = live_panel(parse_briefing(), jobs, now, bad)
    for j in jobs: j.pop("_dl", None)
    setup = []
    try:
        for l in open(SETUP, encoding="utf-8", errors="replace"):
            try: o = json.loads(l)
            except Exception: continue
            if o.get("change"):
                setup.append({"ts":o.get("timestamp"),"change":clean(o.get("change"),120),
                              "result":clean(o.get("result"),160),"owner":norm_owner(o.get("owner"))})
    except FileNotFoundError: pass

    # counts are taken from exactly the cards the board shows: open (not closing) per category + done
    counts = {c: sum(j["cat"] == c and not j.get("closing") for j in open_jobs) for c in ("red", "yellow", "blue")}
    counts["green"] = sum(j["state"] == "done" for j in jobs) + sum(1 for j in open_jobs if j.get("closing"))
    brief["totals_live"] = f"{counts['green']} done, {counts['blue']} in progress, {counts['red']} needing you, {counts['yellow']} waiting on a bot"
    state_counts = {s: sum(j["state"]==s for j in jobs) for s in STATES}
    data = {"site":CFG.site_block(),"generated":now.isoformat(),"newest_entry":entries[-1]["ts"] if entries else None,
        "counts":counts,"state_counts":state_counts,"bad_lines":bad,"valid_lines":len(entries),"bots":bots,"open":open_jobs,
        "done_recent":[j for j in jobs if j["state"]=="done"][:40],
        "jobs":jobs,  # every job (latest entry per owner+job), newest first; used by the per-bot view in index.html
        "feed":list(reversed(entries))[:30],"activity":{"days":days,"by_day":act},
        "briefing":brief,"setup":setup[-8:][::-1],"closeouts":closeouts,
        "upcoming":build_upcoming(now, jobs),"usage":load_usage()}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"; json.dump(data, open(tmp,"w"), ensure_ascii=False); os.replace(tmp, OUT)
    print(f"data.json: {len(entries)} valid, bad lines {bad}, {len(jobs)} jobs, {len(open_jobs)} open, {len(data['upcoming']['items'])} upcoming")

if __name__ == "__main__": main()
