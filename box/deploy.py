#!/usr/bin/env python3
"""Upload www/data.json (daily update) to /opt/team-dashboard/www on your server (atomic rename).
Pass --full to also upload index.html + bot.html (redirect to index.html) + closeout.js and the closeout API (server/closeout_api.py +
server/team-dashboard-api.service -> /opt/team-dashboard/api, /etc/systemd/system) and the login service
(server/auth_service.py + server/team-dashboard-auth.service -> /opt/team-dashboard/auth). Each service is restarted only
when one of its files changed. The Caddyfile is never touched here (server/install.sh writes it; re-run install.sh to change it)."""
import os, sys, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from remote import connect
HERE = os.path.dirname(os.path.abspath(__file__))
SRV = os.path.join(HERE, "..", "server")
c = connect(); s = c.open_sftp()
def run(cmd):
    _, o, e = c.exec_command(cmd, timeout=120); out = o.read().decode() + e.read().decode()
    rc = o.channel.recv_exit_status()
    if rc: raise SystemExit(f"remote failed ({rc}): {cmd}\n{out}")
    return out
FULL = "--full" in sys.argv
FILES = ("index.html", "bot.html", "closeout.js", "data.json") if FULL else ("data.json",)
for f in FILES:
    tmp = f"/opt/team-dashboard/www/.{f}.tmp"
    s.put(os.path.join(HERE, "www", f), tmp); s.chmod(tmp, 0o644)
    try: s.posix_rename(tmp, f"/opt/team-dashboard/www/{f}")
    except IOError: s.rename(tmp, f"/opt/team-dashboard/www/{f}")
msg = "deployed " + " + ".join(FILES)
if FULL:
    changed = False
    for src, dst in (("closeout_api.py", "/opt/team-dashboard/api/closeout_api.py"),
                     ("team-dashboard-api.service", "/etc/systemd/system/team-dashboard-api.service")):
        lp = os.path.join(SRV, src); h = hashlib.sha256(open(lp, "rb").read()).hexdigest()
        if run(f"sudo sha256sum {dst} 2>/dev/null | cut -d' ' -f1 || true").strip() == h: continue
        s.put(lp, f"/tmp/{src}")
        run(f"sudo install -d -o root -g root -m 755 /opt/team-dashboard/api && sudo install -o root -g root -m 644 /tmp/{src} {dst} && rm -f /tmp/{src} && sudo restorecon {dst} 2>/dev/null || true")
        changed = True
    run("sudo install -d -o teamdash -g teamdash -m 755 /opt/team-dashboard/queue && sudo -u teamdash touch /opt/team-dashboard/queue/closeouts.jsonl")
    if changed:
        run("sudo systemctl daemon-reload && sudo systemctl enable team-dashboard-api.service >/dev/null 2>&1; sudo systemctl restart team-dashboard-api.service")
    msg += " + closeout API " + ("(updated, API service restarted)" if changed else "(unchanged)")
    # login service (passkeys + password; Caddy forward_auth). Restarted only when its files changed.
    achanged = False
    for src, dst in (("auth_service.py", "/opt/team-dashboard/auth/auth_service.py"),
                     ("team-dashboard-auth.service", "/etc/systemd/system/team-dashboard-auth.service")):
        lp = os.path.join(SRV, src); h = hashlib.sha256(open(lp, "rb").read()).hexdigest()
        if run(f"sudo sha256sum {dst} 2>/dev/null | cut -d' ' -f1 || true").strip() == h: continue
        s.put(lp, f"/tmp/{src}")
        run(f"sudo install -o root -g root -m 644 /tmp/{src} {dst} && rm -f /tmp/{src} && sudo restorecon {dst} 2>/dev/null || true")
        achanged = True
    if achanged:
        run("sudo systemctl daemon-reload && sudo systemctl restart team-dashboard-auth.service")
    msg += " + login service " + ("(updated, restarted)" if achanged else "(unchanged)")
    msg += " · auth: " + run("systemctl is-active team-dashboard-auth.service || true").strip()
    msg += " · api: " + run("systemctl is-active team-dashboard-api.service || true").strip()
s.close(); c.close(); print(msg)
