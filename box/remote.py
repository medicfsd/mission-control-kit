#!/usr/bin/env python3
"""Tiny SSH/SFTP helper (the box has no ssh binary; uses paramiko). Server, user and key come from config.json "ssh".
  remote.py run '<cmd>'            run a command on the server
  remote.py put <local> <remote>   upload a file
  remote.py keygen                 make the box's SSH key once (private key stays on the box; prints ONLY the public key)
  remote.py trust                  first contact only: show the server's host key fingerprint and save it to known_hosts
Host keys are checked strictly (RejectPolicy) after the first "trust".
"""
import sys, os, paramiko
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mc_config as CFG
S = CFG.C["ssh"]
HOST = os.environ.get("MC_SSH_HOST") or S["host"]
USER = S.get("user") or "opc"
PORT = int(S.get("port") or 22)
KEY = os.path.expanduser(S["key_path"])
KH = os.path.expanduser(S["known_hosts_path"])

def connect():
    CFG.need("ssh", "host")
    c = paramiko.SSHClient()
    c.load_host_keys(KH)
    c.set_missing_host_key_policy(paramiko.RejectPolicy())
    c.connect(HOST, port=PORT, username=USER, key_filename=KEY, timeout=20, look_for_keys=False, allow_agent=False)
    return c

def trust():
    """Fetch the server's host key once and save it. Compare the fingerprint with the VM's console output if you can."""
    import base64, hashlib
    CFG.need("ssh", "host")
    t = paramiko.Transport((HOST, PORT)); t.start_client(timeout=20); k = t.get_remote_server_key(); t.close()
    fp = base64.b64encode(hashlib.sha256(k.asbytes()).digest()).decode().rstrip("=")
    os.makedirs(os.path.dirname(KH), mode=0o700, exist_ok=True)
    hk = paramiko.HostKeys(KH) if os.path.exists(KH) else paramiko.HostKeys()
    name = HOST if PORT == 22 else f"[{HOST}]:{PORT}"
    hk.add(name, k.get_name(), k); hk.save(KH); os.chmod(KH, 0o600)
    print(f"saved {k.get_name()} SHA256:{fp} for {name} to {KH}")

def keygen():
    """Create an ed25519 key at ssh.key_path if missing (mode 600) and print the PUBLIC key for the VM's "Add SSH keys" box."""
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives import serialization as ser
    pub = KEY + ".pub"
    if not os.path.exists(KEY):
        os.makedirs(os.path.dirname(KEY), mode=0o700, exist_ok=True)
        k = ed25519.Ed25519PrivateKey.generate()
        fd = os.open(KEY, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(k.private_bytes(ser.Encoding.PEM, ser.PrivateFormat.OpenSSH, ser.NoEncryption()))
        with open(pub, "wb") as f:
            f.write(k.public_key().public_bytes(ser.Encoding.OpenSSH, ser.PublicFormat.OpenSSH) + b" mission-control-box\n")
    print(open(pub).read().strip())

if __name__ == "__main__":
    if sys.argv[1] == "keygen": keygen(); sys.exit(0)
    if sys.argv[1] == "trust": trust(); sys.exit(0)
    c = connect()
    if sys.argv[1] == "run":
        _, o, e = c.exec_command(sys.argv[2], timeout=900)
        out = o.read().decode(errors="replace"); err = e.read().decode(errors="replace")
        rc = o.channel.recv_exit_status()
        sys.stdout.write(out); sys.stderr.write(err); sys.exit(rc)
    elif sys.argv[1] == "put":
        s = c.open_sftp(); s.put(sys.argv[2], sys.argv[3]); s.close()
