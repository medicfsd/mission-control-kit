#!/usr/bin/env bash
# Daily UPDATE: regenerate only www/data.json from the team log + latest daily briefing, push to your server, verify.
set -euo pipefail
cd "$(dirname "$0")"
# one run at a time (watcher, a morning briefing routine, manual --full): wait up to 8 min for the other run, never skip (8 min + a ~30 s run stays inside watch_log.py's 10-min timeout).
# The lock is held on fd 9, so it is released automatically when this script exits or crashes.
exec 9>.deploy.lock
flock -n 9 || { echo "deploy lock held by another run; waiting (up to 8 min)…"; flock -w 480 9 || { echo "deploy lock still held after 8 min; giving up" >&2; exit 1; }; echo "deploy lock acquired"; }
PY=./.venv/bin/python
if [ ! -x "$PY" ]; then python3 -m venv .venv && ./.venv/bin/pip install -q paramiko bcrypt; fi
python3 roll_cards.py || echo "roll_cards failed (continuing)"   # roll card due dates forward in the log first
python3 build_data.py
"$PY" deploy.py "$@"   # data.json only; add --full after a design change
CHK=--data-only; for a in "$@"; do [ "$a" = "--full" ] && CHK=; done   # full sign-in checks only after a --full deploy
"$PY" check.py $CHK || echo "check failed (deploy itself succeeded)"
# after a --full deploy, optionally commit the kit folder (config.json "git_auto_commit": true; enables rollback.sh)
if [ -z "$CHK" ] && python3 -c 'import mc_config as C,sys; sys.exit(0 if C.C["git_auto_commit"] else 1)'; then
  git -C .. rev-parse --git-dir >/dev/null 2>&1 && { git -C .. add -A box server team-log 2>/dev/null; git -C .. diff --cached --quiet || git -C .. commit -qm "build_and_deploy --full $(date '+%F %T')" >/dev/null 2>&1 || echo "git commit failed (continuing)"; }
fi
