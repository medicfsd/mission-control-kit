#!/usr/bin/env python3
"""Daily briefing check for the shared team log (stdlib only).

Usage:
  briefing_check.py [--log PATH] [--state-dir DIR] [--now ISO] [--no-save]

Reads updates.jsonl, applies the briefing rules, prints a JSON report to
stdout and (unless --no-save) writes briefings/state/YYYY-MM-DD.json (snapshot)
and briefings/state/YYYY-MM-DD.check.json (this report). Each saving run deletes state files
older than 7 days (keeping the newest snapshot it needs as the prior).
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone, date

BASE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LOG = os.path.join(BASE, "updates.jsonl")
DEFAULT_STATE = os.path.join(BASE, "briefings", "state")

# Time zone, expected bots and name aliases come from the kit's config.json (via box/mc_config.py).
# Looks in $MC_KIT_DIR/box, then ../box next to this file. Without it: UTC, bots found from the log only.
TZ, EXPECTED_BOTS, _ALIAS_CFG = timezone.utc, [], {}
for _d in (os.path.join(os.environ.get("MC_KIT_DIR", ""), "box"), os.path.join(BASE, "..", "box")):
    if os.path.isfile(os.path.join(_d, "mc_config.py")):
        sys.path.insert(0, os.path.abspath(_d))
        try:
            import mc_config as _CFG
            TZ = _CFG.TZ
            EXPECTED_BOTS = [name for name, _role, _id in _CFG.team()]
            _ALIAS_CFG = _CFG.C.get("aliases") or {}
        except Exception as _e:  # config missing/half-filled: still run with defaults
            print(f"briefing_check: config not loaded ({_e}); using UTC", file=sys.stderr)
        break

REQUIRED = ["timestamp", "job", "owner", "state", "evidence", "next_step"]
STATES = {"needs decision", "blocked", "done", "in progress"}
STALE_HOURS = 48
DEADLINE_DAYS = 3

# normalized key (lowercase letters/digits only) -> canonical bot name: every expected bot, plus config "aliases"
ALIASES = {re.sub(r"[^a-z0-9]", "", b.casefold()): b for b in EXPECTED_BOTS}
ALIASES.update({re.sub(r"[^a-z0-9]", "", k.casefold()): v for k, v in _ALIAS_CFG.items()})

URL_RE = re.compile(r"https?://\S+", re.I)
PATH_RE = re.compile(r"(?<![\w.:/])(?:~|/)[\w.\-~/]+")
EMAIL_RE = re.compile(r"\b(e-?mail(?:ed|s|ing)?|sent|mail(?:ed)?)\b", re.I)


def norm_key(s):
    return re.sub(r"\s+", " ", str(s)).strip().casefold()


def norm_owner(s):
    raw = re.sub(r"\s+", " ", str(s)).strip()
    key = re.sub(r"[^a-z0-9]", "", raw.casefold())
    return ALIASES.get(key, raw)


def parse_ts(s):
    if not isinstance(s, str) or not s.strip():
        raise ValueError("empty timestamp")
    t = s.strip()
    if t.endswith(("Z", "z")):
        t = t[:-1] + "+00:00"
    dt = datetime.fromisoformat(t)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)  # naive timestamps are owner-local time
    return dt.astimezone(TZ)


def parse_deadline(s):
    t = str(s).strip()
    try:
        return parse_ts(t) if "T" in t else datetime.combine(
            date.fromisoformat(t[:10]), datetime.max.time()).replace(tzinfo=TZ)
    except ValueError:
        return None


def iso(dt):
    return dt.isoformat(timespec="seconds") if dt else None


def verify_evidence(ev):
    """Return (ok, reason) for done-job evidence."""
    ev = ("" if ev is None else str(ev)).strip()
    if not ev:
        return False, "evidence is empty"
    urls = URL_RE.findall(ev)
    rest = URL_RE.sub(" ", ev)
    paths = [p.rstrip(".,;:)") for p in PATH_RE.findall(rest)]
    paths = [p for p in paths if len(p) > 1]
    missing = [p for p in paths if not os.path.exists(os.path.expanduser(p))]
    if missing:
        return False, "file path(s) do not exist: " + ", ".join(missing)
    if urls or paths:
        return True, ""
    return False, "evidence is not a URL or file path"


def load(log_path):
    entries, exceptions = [], []
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue

        def exc(reason, obj=None, ts=None):
            obj = obj if isinstance(obj, dict) else {}
            exceptions.append({
                "type": "invalid_entry", "line": n,
                "job": obj.get("job") if isinstance(obj.get("job"), str) else None,
                "owner": norm_owner(obj["owner"]) if isinstance(obj.get("owner"), str) else None,
                "detail": f"line {n}: {reason}",
                "last_ts": iso(ts) if ts else (obj.get("timestamp") if isinstance(obj.get("timestamp"), str) else None),
            })
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            exc(f"malformed JSON ({e.msg})")
            continue
        if not isinstance(obj, dict):
            exc("line is not a JSON object")
            continue
        missing = [k for k in REQUIRED if k not in obj or obj[k] is None]
        empty = [k for k in ("timestamp", "job", "owner", "state")
                 if k in obj and isinstance(obj[k], str) and not obj[k].strip()]
        if missing or empty:
            parts = []
            if missing:
                parts.append("missing required field(s): " + ", ".join(missing))
            if empty:
                parts.append("empty field(s): " + ", ".join(empty))
            exc("; ".join(parts), obj)
            continue
        try:
            ts = parse_ts(obj["timestamp"])
        except (ValueError, TypeError) as e:
            exc(f"bad timestamp {obj.get('timestamp')!r} ({e})", obj)
            continue
        state = norm_key(obj["state"])
        if state not in STATES:
            exc(f"invalid state {obj['state']!r}", obj, ts)
            continue
        if state == "blocked" and not str(obj.get("blocked_on") or "").strip():
            exc("blocked entry has no blocked_on", obj, ts)
            # still a usable entry; keep it in the grouping
        dline = obj.get("deadline") or obj.get("due_date")   # due_date lines (cards, Upcoming) count as deadlines
        if dline not in (None, "") and parse_deadline(dline) is None:
            exc(f"unparseable deadline {dline!r}", obj, ts)
        entries.append({"line": n, "ts": ts, "job": re.sub(r"\s+", " ", str(obj["job"])).strip(),
                        "key": norm_key(obj["job"]), "owner": norm_owner(obj["owner"]),
                        "state": state, "evidence": obj.get("evidence"),
                        "next_step": obj.get("next_step"),
                        "blocked_on": obj.get("blocked_on"), "deadline": dline})
    return lines, entries, exceptions


def group(entries):
    jobs = {}
    for e in entries:
        jobs.setdefault(e["key"], []).append(e)
    out = {}
    for key, es in jobs.items():
        es.sort(key=lambda e: (e["ts"], e["line"]))
        last = es[-1]
        owners = []
        for e in es:
            if e["owner"] not in owners:
                owners.append(e["owner"])
        deadline = last["deadline"]
        if deadline in (None, ""):
            for e in reversed(es):
                if e["deadline"] not in (None, ""):
                    deadline = e["deadline"]
                    break
        out[key] = {"job": last["job"], "owners": owners, "state": last["state"],
                    "latest_ts": iso(last["ts"]), "_ts": last["ts"],
                    "evidence": last["evidence"], "next_step": last["next_step"],
                    "blocked_on": last["blocked_on"], "deadline": deadline,
                    "entry_count": len(es), "lines": [e["line"] for e in es]}
    return out


def prior_snapshot(state_dir, today):
    best = None
    if os.path.isdir(state_dir):
        for fn in os.listdir(state_dir):
            m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.json", fn)
            if m and m.group(1) < today and (best is None or m.group(1) > best):
                best = m.group(1)
    if not best:
        return None, None
    with open(os.path.join(state_dir, best + ".json"), encoding="utf-8") as f:
        return best, json.load(f)


KEEP_DAYS = 7


def prune_state(state_dir):
    """Delete snapshot/check files dated more than KEEP_DAYS days ago (by the real local date, not --now),
    always keeping the newest snapshot and the newest one before today (prior_snapshot() needs it)."""
    today = datetime.now(TZ).date()
    cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
    snaps = sorted(m.group(1) for fn in os.listdir(state_dir) if (m := re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.json", fn)))
    keep = set(snaps[-1:]) | set([d for d in snaps if d < today.isoformat()][-1:])
    removed = []
    for fn in sorted(os.listdir(state_dir)):
        m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.(?:check\.)?json", fn)
        if m and m.group(1) < cutoff and not (fn == m.group(1) + ".json" and m.group(1) in keep):
            os.remove(os.path.join(state_dir, fn)); removed.append(fn)
    return removed


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--now", help="override current time (ISO 8601; naive = configured time zone)")
    ap.add_argument("--no-save", action="store_true", help="do not write snapshot/check files")
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--state-dir", default=DEFAULT_STATE)
    a = ap.parse_args()

    now = parse_ts(a.now) if a.now else datetime.now(TZ)
    today = now.date().isoformat()
    lines, entries, exceptions = load(a.log)
    jobs = group(entries)

    buckets = {s: [] for s in STATES}
    needs_sent = []
    for key, j in sorted(jobs.items(), key=lambda kv: kv[1]["_ts"], reverse=True):
        item = {k: j[k] for k in ("job", "owners", "latest_ts", "evidence", "next_step",
                                  "blocked_on", "deadline", "entry_count")}
        buckets[j["state"]].append(item)
        owner = ", ".join(j["owners"])

        def add(t, detail):
            exceptions.append({"type": t, "job": j["job"], "owner": owner,
                               "detail": detail, "last_ts": j["latest_ts"]})
        if j["state"] != "done":
            age = now - j["_ts"]
            if age >= timedelta(hours=STALE_HOURS):
                add("stale", f"state '{j['state']}', no entry for {age.total_seconds()/3600:.1f}h (>= {STALE_HOURS}h)")
            if j["deadline"] not in (None, ""):
                dl = parse_deadline(j["deadline"])
                if dl is not None:
                    if dl < now:
                        add("deadline_past_due", f"deadline {j['deadline']} is past due")
                    elif dl - now <= timedelta(days=DEADLINE_DAYS):
                        add("deadline_soon", f"deadline {j['deadline']} is within {DEADLINE_DAYS} days")
        else:
            ok, why = verify_evidence(j["evidence"])
            if not ok:
                add("unverified_done", why)
            if EMAIL_RE.search(f"{j['job']} {j['next_step'] or ''}"):
                needs_sent.append({**item, "reason": "done job looks like an email send; confirm in Sent folder"})

    logged = {e["owner"].casefold() for e in entries}
    not_logged = [b for b in EXPECTED_BOTS if b.casefold() not in logged]

    snapshot = {"date": today, "generated_at": iso(now),
                "jobs": {k: {"job": j["job"], "state": j["state"], "latest_ts": j["latest_ts"],
                             "entry_count": j["entry_count"]} for k, j in jobs.items()}}
    prev_date, prev = prior_snapshot(a.state_dir, today)
    changes = {"new": [], "state_changed": [], "updated": []}
    if prev is None:
        changes["note"] = "no prior snapshot"
    else:
        changes["compared_to"] = prev_date
        pj = prev.get("jobs", {})
        for k, j in snapshot["jobs"].items():
            p = pj.get(k)
            if p is None:
                changes["new"].append({"job": j["job"], "state": j["state"], "latest_ts": j["latest_ts"]})
            elif p.get("state") != j["state"]:
                changes["state_changed"].append({"job": j["job"], "from": p.get("state"), "to": j["state"],
                                                 "latest_ts": j["latest_ts"]})
            elif p.get("latest_ts") != j["latest_ts"] or p.get("entry_count") != j["entry_count"]:
                changes["updated"].append({"job": j["job"], "state": j["state"], "latest_ts": j["latest_ts"],
                                           "new_entries": j["entry_count"] - int(p.get("entry_count") or 0)})

    order = {"invalid_entry": 0, "deadline_past_due": 1, "deadline_soon": 2, "stale": 3, "unverified_done": 4}
    exceptions.sort(key=lambda x: (order.get(x["type"], 9), x.get("line") or 0, x.get("job") or ""))
    tss = [e["ts"] for e in entries]
    report = {
        "generated_at": iso(now),
        "source": os.path.abspath(a.log),
        "window": {"first_ts": iso(min(tss)) if tss else None, "last_ts": iso(max(tss)) if tss else None},
        "counts": {"lines": sum(1 for l in lines if l.strip()), "valid_entries": len(entries),
                   "jobs": len(jobs), "needs_decision": len(buckets["needs decision"]),
                   "blocked": len(buckets["blocked"]), "done": len(buckets["done"]),
                   "in_progress": len(buckets["in progress"]), "exceptions": len(exceptions),
                   "needs_sent_check": len(needs_sent), "not_logged_bots": len(not_logged)},
        "needs_decision": buckets["needs decision"],
        "blocked": buckets["blocked"],
        "done": buckets["done"],
        "in_progress": buckets["in progress"],
        "exceptions": exceptions,
        "needs_sent_check": needs_sent,
        "not_logged_bots": not_logged,
        "changes_since_last": changes,
    }
    out = json.dumps(report, indent=2, ensure_ascii=False)
    print(out)
    if not a.no_save:
        os.makedirs(a.state_dir, exist_ok=True)
        for name, data in ((today + ".json", json.dumps(snapshot, indent=2, ensure_ascii=False)),
                           (today + ".check.json", out)):
            tmp = os.path.join(a.state_dir, name + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(data + "\n")
            os.replace(tmp, os.path.join(a.state_dir, name))
        removed = prune_state(a.state_dir)
        if removed:
            print(f"pruned {len(removed)} state file(s) older than {KEEP_DAYS} days", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
