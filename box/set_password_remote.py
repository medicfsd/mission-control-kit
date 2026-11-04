#!/usr/bin/env python3
"""Set the owner's dashboard password on the server. Reads the password from THIS script's stdin and passes it to
server/set_password.py over SSH stdin, so it never appears in a command line, a log or the chat.
  <password on stdin> | .venv/bin/python set_password_remote.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from remote import connect
pw = sys.stdin.read().rstrip("\r\n")
if not 12 <= len(pw) <= 200: sys.exit("password must be 12-200 characters")
c = connect()
i, o, e = c.exec_command("sudo -u teamauth /opt/team-dashboard/auth/venv/bin/python /opt/team-dashboard/auth/set_password.py", timeout=60)
i.write(pw); i.channel.shutdown_write(); del pw
out = (o.read() + e.read()).decode().strip(); rc = o.channel.recv_exit_status(); c.close()
print(out); sys.exit(rc)
