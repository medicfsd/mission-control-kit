#!/usr/bin/env python3
"""Roll credit card due dates forward in the team log (card due days don't change month to month).
Card = any (owner, job) with the config.json card_roll category (default "Bills") and job_contains text (default
"payment due") in the job. ONLY when its latest line is done (paid),
append ONE new line with the next due date that is today (your time zone) or later. Unpaid past-due cards are left alone (they
stay overdue on the dashboard/briefing). Idempotent.
Usage: roll_cards.py [--log PATH] [--today YYYY-MM-DD] [--dry-run]"""
import argparse, calendar, datetime as dt, fcntl, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mc_config as CFG

AZ = CFG.TZ
CR = CFG.C["card_roll"]
if not CR.get("enabled", True): print("card_roll disabled in config.json"); sys.exit(0)
CAT, MATCH = CR.get("category", "Bills"), CR.get("job_contains", "payment due").lower()
p = argparse.ArgumentParser()
p.add_argument("--log", default=CFG.LOG)
p.add_argument("--today"); p.add_argument("--dry-run", action="store_true")
a = p.parse_args()
today = dt.date.fromisoformat(a.today) if a.today else dt.datetime.now(AZ).date()

def next_month(d, day):
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return dt.date(y, m, min(day, calendar.monthrange(y, m)[1]))

if not os.path.exists(a.log): print("no team log yet; nothing to roll"); sys.exit(0)
with open(a.log, "r+", encoding="utf-8") as f:
    fcntl.flock(f, fcntl.LOCK_EX)
    latest, info, cards = {}, {}, set()
    for line in f:
        try: o = json.loads(line)
        except ValueError: continue
        if not isinstance(o, dict) or not o.get("job") or not o.get("owner"): continue
        k = (o["owner"], o["job"]); latest[k] = o
        if o.get("category") == CAT and MATCH in o["job"].lower(): cards.add(k)
        if o.get("due_date"): info[k] = o          # last line that carried the date/card details
    now = dt.datetime.now(AZ).replace(microsecond=0).isoformat()
    out = []
    for k in sorted(cards):
        last, base = latest[k], info.get(k)
        if not base: continue
        due = dt.date.fromisoformat(base["due_date"][:10])
        if last.get("state") != "done":
            print(f"{'OVERDUE' if due < today else 'ok'}  {k[1]}: due {due} (not done, left as is)"); continue
        day, new = int(base.get("due_day") or due.day), next_month(due, int(base.get("due_day") or due.day))
        while new < today: new = next_month(new, day)
        n = {"timestamp": now, "job": k[1], "owner": k[0], "state": "in progress", "kind": "deadline",
             "due_date": new.isoformat(), "due_day": day, "category": CAT}
        if base.get("last4"): n["last4"] = base["last4"]
        if "autopay" in base: n["autopay"] = base["autopay"]
        n["evidence"] = f"Auto-rolled by roll_cards.py after the {due} due date was marked done (paid); card due day {day} is fixed"
        n["next_step"] = "Pay by the due date (amount comes with the new statement)"
        out.append(n); print(f"ROLL  {k[1]}: {due} -> {new}")
    if out and not a.dry_run:
        f.seek(0, 2); f.write("".join(json.dumps(n, ensure_ascii=False) + "\n" for n in out)); f.flush()
    print(f"{len(out)} rolled{' (dry run, nothing written)' if a.dry_run else ''}")
