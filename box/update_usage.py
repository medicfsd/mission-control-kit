#!/usr/bin/env python3
"""Daily usage update for the dashboard's Usage panel (optional).

A daily routine (any bot) first writes REAL figures from the Grok Bot usage report to
  <team_log_dir>/usage/latest.json   (contract: team-log/README.md, "Usage data")
then runs, BEFORE its own deploy:
  python3 <kit>/box/update_usage.py --no-deploy
This validates latest.json (schema, numbers, no secrets) and saves usage/YYYY-MM-DD.json (local date of collected_at).
Without --no-deploy it also runs build_and_deploy.sh (data-only; shares the deploy lock, waits up to 8 min).
Missing/invalid file: clear error, exit 1. It never invents numbers.
"""
import datetime, json, os, re, shutil, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mc_config as CFG
UDIR = os.path.join(CFG.LOG_DIR, "usage")
LATEST = os.path.join(UDIR, "latest.json")
LISTS = {"by_bot": "name", "by_routine": "name", "by_day": "date", "by_activity": "name"}
SECRET = re.compile(r"(api[_-]?key|token|secret|password|passwd|bearer|private key|ghp_|gho_|sk-[A-Za-z0-9]{8}|xox[bp]-|-----BEGIN)", re.I)
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?([+-]\d{2}:\d{2})$")

def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 100

def validate(d):
    """Return a list of problems (empty = valid)."""
    e = []
    if not isinstance(d, dict): return ["top level is not an object"]
    for k in ("collected_at", "resets_at"):
        if not (isinstance(d.get(k), str) and ISO.match(d[k])): e.append(f"{k} must be ISO time with offset, e.g. 2026-11-04T09:00:00-07:00")
    if not (isinstance(d.get("period"), str) and d["period"].strip()): e.append("period must be non-empty text")
    if not _num(d.get("weekly_used_pct")): e.append("weekly_used_pct must be a number 0-100")
    if d.get("on_demand_pct") is not None and not _num(d.get("on_demand_pct")): e.append("on_demand_pct must be a number 0-100 or null")
    for k, req in LISTS.items():
        L = d.get(k)
        if not isinstance(L, list) or not L: e.append(f"{k} must be a non-empty list"); continue
        for i, x in enumerate(L):
            if not isinstance(x, dict): e.append(f"{k}[{i}] is not an object"); continue
            if not (isinstance(x.get(req), str) and x[req].strip()): e.append(f"{k}[{i}].{req} missing")
            if not _num(x.get("pct")): e.append(f"{k}[{i}].pct must be a number 0-100")
            if x.get("lt1") and x.get("pct") != 0.5: e.append(f"{k}[{i}]: lt1 rows must have pct 0.5")
            if k == "by_day" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(x.get("date", ""))): e.append(f"by_day[{i}].date must be YYYY-MM-DD")
    if SECRET.search(json.dumps(d)): e.append("looks like it contains a secret (token/key/password); refusing")
    return e

def load(path=LATEST):
    with open(path, encoding="utf-8") as f: return json.load(f)

def main():
    if not os.path.isfile(LATEST): sys.exit(f"ERROR: {LATEST} not found. Write real usage figures there first (see team-log/README.md, 'Usage data').")
    try: d = load()
    except Exception as x: sys.exit(f"ERROR: {LATEST} is not valid JSON: {x}")
    errs = validate(d)
    if errs: sys.exit("ERROR: usage/latest.json is invalid:\n  - " + "\n  - ".join(errs))
    day = datetime.datetime.fromisoformat(d["collected_at"]).astimezone(CFG.TZ).date().isoformat()
    shutil.copyfile(LATEST, os.path.join(UDIR, f"{day}.json"))
    print(f"usage OK: collected {d['collected_at']}, saved usage/{day}.json")
    if "--no-deploy" in sys.argv[1:]: print("--no-deploy: the next build_and_deploy.sh run picks it up"); return
    sys.exit(subprocess.call([os.path.join(HERE, "build_and_deploy.sh")], cwd=HERE))

if __name__ == "__main__": main()
