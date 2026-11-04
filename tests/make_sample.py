#!/usr/bin/env python3
"""Write a small FAKE team log + bot profiles (fresh timestamps) into the folder given, for local tests and demos."""
import json, datetime, os, sys
from zoneinfo import ZoneInfo
out = sys.argv[1]; tz = ZoneInfo(sys.argv[2] if len(sys.argv) > 2 else "America/New_York")
now = datetime.datetime.now(tz).replace(microsecond=0)
t = lambda h: (now - datetime.timedelta(hours=h)).isoformat()
d = lambda n: (now.date() + datetime.timedelta(days=n)).isoformat()
L = [
 {"timestamp": t(30), "job": "Weekly grocery order", "owner": "Helper", "state": "in progress", "evidence": "cart drafted", "next_step": "Helper: check out Friday"},
 {"timestamp": t(2), "job": "Weekly grocery order", "owner": "Helper", "state": "done", "evidence": "https://example.com/order/123", "next_step": "none"},
 {"timestamp": t(5), "job": "Approve new phone plan", "owner": "Helper", "state": "needs decision", "ask": "approval", "evidence": "https://example.com/plan", "next_step": "Owner: approve or deny the plan change"},
 {"timestamp": t(4), "job": "Research standing desks", "owner": "Researcher", "state": "in progress", "evidence": "notes", "next_step": "Researcher: shortlist 3 by tomorrow"},
 {"timestamp": t(100), "job": "Fix website footer", "owner": "Coder", "state": "blocked", "blocked_on": "waiting on Sam for the logo file", "evidence": "none", "next_step": "Sam: send the logo"},
 {"timestamp": t(3), "job": "Which hotel for the trip?", "owner": "Researcher", "state": "needs decision", "ask": "question", "evidence": "https://example.com/hotels", "next_step": "Owner: pick A or B"},
 {"timestamp": t(6), "job": "Sample Card payment due", "owner": "Helper", "state": "in progress", "evidence": "statement email", "next_step": "Helper: remind 3 days before",
  "kind": "card", "category": "Bills", "due_date": d(5), "min_due": "25.00", "balance": "410.12", "autopay": False, "last4": "0000"},
 {"timestamp": t(6), "job": "Car registration renewal", "owner": "Helper", "state": "in progress", "evidence": "renewal notice", "next_step": "Owner: renew online", "deadline": d(12), "due_date": d(12), "category": "Car"},
 {"timestamp": t(1), "job": "Morning briefing", "owner": "Coordinator", "state": "done", "evidence": "https://example.com/briefing", "next_step": "none"},
]
log = os.path.join(out, "team-log"); os.makedirs(os.path.join(log, "briefings"), exist_ok=True)
open(os.path.join(log, "updates.jsonl"), "w").write("".join(json.dumps(x) + "\n" for x in L))
open(os.path.join(log, "briefings", now.date().isoformat() + ".md"), "w").write(
    "# Morning briefing\n\n## Headlines and top actions\n1. Approve the new phone plan (Helper)\n2. Pick a hotel for the trip (Researcher)\n"
    "3. Send the logo so Coder can finish the footer\n\n## Needs Sam\n- Approve new phone plan\n\n## Waiting on a bot\n- Research standing desks\n")
for i, (n, ti, c, sh) in enumerate([("Coordinator", "Briefings", "#8B5CF6", "blob"), ("Helper", "Errands", "#10B981", "circle"),
                                    ("Researcher", "Research", "#3B82F6", "square"), ("Coder", "Code", "#F59E0B", "hexagon")]):
    p = os.path.join(out, "agents", f"0000000{i}-0000-4000-8000-00000000000{i}"); os.makedirs(p, exist_ok=True)
    json.dump({"name": n, "title": ti, "avatarColor": c, "avatarShape": sh, "description": "sample bot"}, open(os.path.join(p, "profile.json"), "w"))
print("sample written to", out)
