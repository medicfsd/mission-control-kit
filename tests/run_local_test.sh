#!/bin/bash
# Local smoke test with FAKE data: no server, no SSH, nothing personal. Leaves the result in $OUT (prints the path).
set -euo pipefail
KIT="$(cd "$(dirname "$0")/.." && pwd)"; OUT="${1:-$(mktemp -d)}"
python3 "$KIT/tests/make_sample.py" "$OUT" America/New_York
sed "s#__OUT__#$OUT#g" "$KIT/examples/config.sample.json" > "$OUT/config.json"
export MC_CONFIG="$OUT/config.json"
echo "== syntax"; for f in "$KIT"/box/*.py "$KIT"/server/*.py "$KIT"/team-log/*.py "$KIT"/tests/*.py; do python3 -m py_compile "$f"; done
for f in "$KIT"/box/*.sh "$KIT"/server/*.sh "$KIT"/tests/*.sh; do bash -n "$f"; done; echo ok
echo "== card roll (sample)"; python3 "$KIT/box/roll_cards.py" || true
echo "== build"; cp -r "$KIT/box" "$OUT/box"; rm -f "$OUT/box/config.json"; python3 "$OUT/box/build_data.py"
python3 - "$OUT/box/www/data.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1])); s = d["site"]
assert s["title"] == "Sam's Mission Control" and s["tz"] == "America/New_York" and s["needs_label"] == "Needs Sam", s
names = sorted(b["name"] for b in d["bots"]); print("bots:", names); assert {"Coordinator", "Helper", "Researcher", "Coder"} <= set(names)
print("top-level keys:", sorted(d))
PY
echo "== briefing check"; MC_KIT_DIR="$KIT" python3 "$KIT/team-log/briefing_check.py" --log "$OUT/team-log/updates.jsonl" --no-save \
  | python3 -c 'import json,sys;r=json.load(sys.stdin);print("counts:",r["counts"]);print("not logged:",r["not_logged_bots"])'
echo "PASS  (dashboard: $OUT/box/www/index.html; serve with: cd $OUT/box/www && python3 -m http.server 8000)"
