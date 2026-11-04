#!/usr/bin/env python3
"""Set (or reset) the dashboard owner password. Reads the new password from STDIN only (never from the command line,
so it is not visible in `ps` or shell history), writes /opt/team-dashboard/auth-data/password.bcrypt (bcrypt, 600),
and removes any unused first-run setup code. Prints nothing secret.
  printf '%s' "$NEW_PASSWORD" | sudo -u teamauth /opt/team-dashboard/auth/venv/bin/python /opt/team-dashboard/auth/set_password.py
Restarting the auth service is not needed (the hash is read on every sign-in)."""
import os, sys
import bcrypt
DATA = os.environ.get("AUTH_DATA", "/opt/team-dashboard/auth-data")
pw = sys.stdin.read().rstrip("\r\n")
if not 12 <= len(pw) <= 200: sys.exit("password must be 12-200 characters")
os.umask(0o077)
p = os.path.join(DATA, "password.bcrypt"); tmp = p + ".tmp"
with open(tmp, "w") as f: f.write(bcrypt.hashpw(pw.encode(), bcrypt.gensalt(12)).decode() + "\n")
os.chmod(tmp, 0o600); os.replace(tmp, p)
try: os.remove(os.path.join(DATA, "setup.json"))
except FileNotFoundError: pass
print("password saved")
