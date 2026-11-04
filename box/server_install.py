#!/usr/bin/env python3
"""Upload the kit's server/ folder to the VM (~/mission-control-server) and run install.sh there with sudo.
Values come from config.json: dash_host, site_name, owner_name, login_username, timezone. Safe to run again.
  .venv/bin/python server_install.py
Prints only install.sh's own output (it never prints secrets)."""
import os, sys, shlex
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mc_config as CFG
from remote import connect
SRV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server")
env = {"DASH_HOST": CFG.need("dash_host"), "SITE_NAME": CFG.SITE, "OWNER_DISPLAY_NAME": CFG.OWNER,
       "DASH_USERNAME": CFG.get("login_username") or "owner", "DASH_TZ": CFG.TZ_NAME}
c = connect(); s = c.open_sftp()
_, o, _ = c.exec_command("mkdir -p ~/mission-control-server && cd ~/mission-control-server && pwd"); rdir = o.read().decode().strip()
for f in sorted(os.listdir(SRV)):
    p = os.path.join(SRV, f)
    if os.path.isfile(p): s.put(p, f"{rdir}/{f}")
cmd = "cd " + shlex.quote(rdir) + " && sudo env " + " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items()) + " bash install.sh 2>&1"
_, o, _ = c.exec_command(cmd, timeout=1800)
for line in iter(o.readline, ""): print(line, end="", flush=True)
rc = o.channel.recv_exit_status(); c.close(); sys.exit(rc)
