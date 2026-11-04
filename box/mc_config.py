"""Loads config.json (the ONE file with your personal values). Every box script imports this.

Path: $MC_CONFIG if set, else config.json next to this file. Copy config.example.json to config.json and fill it in
(the getting-started bot does this for you). config.json is git-ignored; it holds no secrets, only names and paths.
"""
import json, os, glob, re
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.environ.get("MC_CONFIG") or os.path.join(HERE, "config.json")

DEFAULTS = {
    "site_name": "Mission Control",
    "owner_name": "",                 # shown as "<owner>'s Mission Control" and "Needs <owner>"; blank = "Needs you"
    "owner_aliases": [],              # other names your bots use for you in the log ("waiting on <alias>")
    "timezone": "UTC",                # IANA name, e.g. America/New_York
    "tz_label": "",                   # short label shown after times, e.g. ET; blank = derived from the zone
    "dash_host": "",                  # e.g. yourname.duckdns.org (also the passkey RP ID)
    "login_username": "owner",
    "agent_data_dir": "/home/box/agent-data",
    "team_log_dir": "/home/box/agent-data/shared/team-log",
    "team": [],                       # [] = discover bots from <agent_data_dir>/agents/*/profile.json
    "team_exclude": [],               # bot names to leave off an auto-discovered roster
    "coordinator": "",                # bot opened by "Open in Grok Bot" when an item has no owner (usually your briefing bot)
    "aliases": {},                    # lowercased name a bot might log as -> roster name
    "retired": {},                    # bot name -> date retired (old lines stay as history, no tile)
    "stale_days": 3,                  # open job with no update for this many days -> Exceptions
    "stale_overrides": [],            # [{"owner": "...", "job": "...", "days": 35}] for jobs that only update monthly
    "upcoming_days_back": 60,
    "upcoming_days_ahead": 60,
    "card_roll": {"enabled": True, "category": "Bills", "job_contains": "payment due"},
    "ssh": {"host": "", "user": "opc", "port": 22,
            "key_path": "/home/box/.mission-control/ssh/id_ed25519",
            "known_hosts_path": "/home/box/.mission-control/ssh/known_hosts"},
    "git_auto_commit": False,         # commit the kit folder after every --full deploy (for rollback.sh / private backup)
    "webhook_env": "",                # optional file with DECISIONS_WEBHOOK_URL / DECISIONS_WEBHOOK_KEY (keep it chmod 600)
}

PLACEHOLDER = re.compile(r"\{\{[A-Z0-9_]+\}\}")


def _load():
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        with open(PATH, encoding="utf-8") as f:
            user = json.load(f)
    except FileNotFoundError:
        user = {}
    for k, v in user.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg


C = _load()


def get(key):
    return C[key]


def need(key, sub=None):
    """Value that must be filled in (not blank, not a {{PLACEHOLDER}})."""
    v = C[key][sub] if sub else C[key]
    if v in ("", None) or (isinstance(v, str) and PLACEHOLDER.search(v)):
        raise SystemExit(f"config.json: '{key}{'.' + sub if sub else ''}' is not set yet ({PATH})")
    return v


def _clean_tz(v):
    return "UTC" if (not v or PLACEHOLDER.search(str(v))) else v


TZ = ZoneInfo(_clean_tz(C["timezone"]))
TZ_NAME = _clean_tz(C["timezone"])


def tz_label():
    lab = C.get("tz_label") or ""
    if lab and not PLACEHOLDER.search(lab):
        return lab
    import datetime as dt
    return dt.datetime.now(TZ).strftime("%Z") or TZ_NAME


def _txt(v):
    return "" if (not v or PLACEHOLDER.search(str(v))) else str(v).strip()


OWNER = _txt(C["owner_name"])
SITE = _txt(C["site_name"]) or "Mission Control"
TITLE = f"{OWNER}'s {SITE}" if OWNER else SITE
NEEDS_LABEL = f"Needs {OWNER}" if OWNER else "Needs you"
OWNER_NAMES = [n for n in [OWNER] + [_txt(a) for a in C.get("owner_aliases") or []] if n]

AGENTS_DIR = os.path.join(C["agent_data_dir"], "agents")
LOG_DIR = C["team_log_dir"]
LOG = os.path.join(LOG_DIR, "updates.jsonl")


def team():
    """[(name, role, agent_id)] from config, or discovered from the bots' profile.json files."""
    if C["team"]:
        return [(t["name"], t.get("role", ""), t.get("id", "")) for t in C["team"]]
    out = []
    for p in sorted(glob.glob(os.path.join(AGENTS_DIR, "*", "profile.json"))):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        name = (d.get("name") or "").strip()
        if not name or name in C["team_exclude"] or name in C["retired"]:
            continue
        out.append((name, (d.get("title") or "").strip(), os.path.basename(os.path.dirname(p))))
    return out


def site_block():
    """What index.html needs to label itself (goes into data.json as "site")."""
    return {"title": TITLE, "site_name": SITE, "owner": OWNER, "needs_label": NEEDS_LABEL,
            "owner_names": OWNER_NAMES, "tz": TZ_NAME, "tz_label": tz_label(),
            "coordinator": _txt(C.get("coordinator")),
            "upcoming_days_ahead": C["upcoming_days_ahead"]}
