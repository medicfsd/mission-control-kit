#!/bin/bash
# Roll the dashboard CODE (box/, server/, team-log/*.py) back to a saved version. Needs the kit folder to be a git repo.
#   ./rollback.sh mark      save the current version as rollback-1 (the old rollback-1 becomes rollback-2); run before a risky change
#   ./rollback.sh 1|2       restore rollback-1 or rollback-2 as a NEW commit (nothing is deleted), then redeploy with --full
# The team log data (updates.jsonl etc.) is never rolled back.
set -e
cd "$(dirname "$0")/.."
git rev-parse --git-dir >/dev/null 2>&1 || { echo "not a git repo: run 'git init' in the kit folder first"; exit 1; }
case "${1:-}" in
  mark)
    git add -A box server team-log && { git diff --cached --quiet || git commit -qm "Saved version $(date '+%F %T')"; }
    git rev-parse -q --verify rollback-1 >/dev/null && git tag -f rollback-2 rollback-1 >/dev/null
    git tag -f rollback-1 HEAD >/dev/null && echo "rollback-1 = $(git rev-parse --short HEAD)";;
  1|2)
    T=$(git rev-parse --short "rollback-$1")
    git checkout "rollback-$1" -- box server team-log
    git commit -qm "Rollback to $T (rollback-$1)" && echo "Restored $T" || echo "already at $T"
    box/build_and_deploy.sh --full;;
  *) echo "usage: rollback.sh mark | 1 | 2"; exit 1;;
esac
