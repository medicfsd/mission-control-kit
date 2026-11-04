#!/usr/bin/env python3
"""Forward box 127.0.0.1:18088 -> server 127.0.0.1:8088 (the loopback-only dashboard copy) over SSH.
Use for checks/screenshots while the public ports are closed. The loopback copy asks for the check login in
/opt/team-dashboard/check.netrc on the server (root-only; it never leaves the server unless you copy it). Ctrl-C to stop."""
import socket, threading, select, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from remote import connect
LPORT = int(os.environ.get("LPORT", "18088"))
c = connect(); t = c.get_transport()
def handle(sock):
    ch = t.open_channel("direct-tcpip", ("127.0.0.1", 8088), sock.getpeername())
    while True:
        r, _, _ = select.select([sock, ch], [], [])
        if sock in r:
            d = sock.recv(65536)
            if not d: break
            ch.sendall(d)
        if ch in r:
            d = ch.recv(65536)
            if not d: break
            sock.sendall(d)
    ch.close(); sock.close()
srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", LPORT)); srv.listen(20); print(f"tunnel on 127.0.0.1:{LPORT}", flush=True)
while True:
    s, _ = srv.accept(); threading.Thread(target=handle, args=(s,), daemon=True).start()
