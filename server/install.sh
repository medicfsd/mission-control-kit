#!/bin/bash
# Mission Control server install for a fresh Oracle Cloud Always Free VM (Oracle Linux 8/9 or Ubuntu 22.04/24.04,
# ARM Ampere or x86). Safe to run again: it keeps existing passwords, passkeys, secrets and certificates.
#
# Run from the folder that holds this kit's server/ files, as the normal SSH user (opc or ubuntu):
#   sudo DASH_HOST=myname.duckdns.org SITE_NAME="Mission Control" OWNER_DISPLAY_NAME="Sam" DASH_TZ=America/New_York bash install.sh
# Optional: DASH_USERNAME (default owner), SKIP_FIREWALL=1.
#   sudo bash install.sh --new-setup-code   make a fresh one-time setup code (prints it ONLY to an interactive terminal)
#
# What it does: installs Caddy (pinned, checksum-verified), Python venv for the login service, two system users,
# /opt/team-dashboard, the three systemd services, opens ports 80/443 in the OS firewall, and makes these secrets
# ON THIS SERVER ONLY (never printed): cookie key, check-login password, one-time setup code.
# You still must open 80/443 in the Oracle Cloud VCN security list (see SETUP.md) — this script cannot do that.
set -euo pipefail
umask 022
CADDY_VERSION=2.11.7
declare -A CADDY_SHA512=(
  [arm64]=3db36ba90c7a6e8dda40ee3dd71fa08844c76b5fb08f61b31e5e78d2ed38e71c51dc7baed875e50d1ca1279196e84302967237386ae87c91ae9f2aaceada682e
  [amd64]=a7a433a1b133efc3c8d10eb0b99d52a24b5ef5c322dc77f5282182b1c0402139ab83f3a99f0c52409df77d20123fb0b523edad8a66d8f5e49136197bf61ef0e7
)
SRC="$(cd "$(dirname "$0")" && pwd)"
R=/opt/team-dashboard
say(){ printf '\n==> %s\n' "$*"; }
die(){ echo "ERROR: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || die "run with sudo"
SSH_USER="${SUDO_USER:-opc}"; [ "$SSH_USER" = root ] && SSH_USER=opc

new_setup_code(){   # stores only sha256 + expiry; the code itself is printed only to a real terminal
  [ -s "$R/auth-data/password.bcrypt" ] && { echo "A password is already set; use set_password.py to change it."; return 0; }
  local code; code=$(python3 -c 'import secrets;print("-".join(secrets.token_hex(3) for _ in range(3)))')
  printf '%s' "$code" | python3 -c 'import sys,json,hashlib,time;c=sys.stdin.read().strip();print(json.dumps({"sha256":hashlib.sha256(c.encode()).hexdigest(),"expires":int(time.time())+86400}))' \
    | install -o teamauth -g teamauth -m 600 /dev/stdin "$R/auth-data/setup.json"
  if [ -t 1 ]; then echo "One-time setup code (valid 24 hours, works once): $code"; else echo "setup code refreshed (not shown: no terminal)"; fi
}
if [ "${1:-}" = "--new-setup-code" ]; then new_setup_code; exit 0; fi

# ---- settings ---------------------------------------------------------------------------------------------------
for k in DASH_HOST SITE_NAME OWNER_DISPLAY_NAME DASH_USERNAME DASH_TZ; do   # re-runs keep earlier values unless given again
  if [ -z "${!k:-}" ] && [ -f "$R/host.env" ]; then printf -v "$k" '%s' "$(sed -n "s/^$k=//p" "$R/host.env" | head -1)"; fi
done
DASH_HOST="$(echo "${DASH_HOST:-}" | tr 'A-Z' 'a-z')"; SITE_NAME="${SITE_NAME:-Mission Control}"
OWNER_DISPLAY_NAME="${OWNER_DISPLAY_NAME:-}"; DASH_USERNAME="$(echo "${DASH_USERNAME:-owner}" | tr 'A-Z' 'a-z')"; DASH_TZ="${DASH_TZ:-UTC}"
[[ "$DASH_HOST" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$ ]] || die "set DASH_HOST to your website name, e.g. myname.duckdns.org"
[[ "$DASH_USERNAME" =~ ^[a-z0-9._-]{1,32}$ ]] || die "DASH_USERNAME: letters, digits, . _ - only"
[ -e "/usr/share/zoneinfo/$DASH_TZ" ] || die "unknown time zone DASH_TZ=$DASH_TZ (example: America/New_York)"
for v in SITE_NAME OWNER_DISPLAY_NAME; do [[ "${!v}" =~ [\"\$\`\\] ]] && die "$v must not contain quotes, \$, \` or backslashes"; done

# ---- packages ---------------------------------------------------------------------------------------------------
say "packages"
if command -v dnf >/dev/null; then PM=dnf; dnf -y -q install python3 python3-pip curl tar policycoreutils-python-utils >/dev/null
elif command -v apt-get >/dev/null; then PM=apt; export DEBIAN_FRONTEND=noninteractive
  apt-get -qq update; apt-get -y -qq --no-install-recommends install python3 python3-venv python3-pip curl ca-certificates tar iptables-persistent >/dev/null
else die "need Oracle Linux (dnf) or Ubuntu (apt)"; fi

# ---- users + folders ----------------------------------------------------------------------------------------------
say "users and folders"
NOLOGIN=$(command -v nologin || echo /sbin/nologin)
for u in teamdash teamauth; do id "$u" >/dev/null 2>&1 || useradd --system --no-create-home --home-dir /nonexistent --shell "$NOLOGIN" "$u"; done
install -d -o root -g root -m 755 "$R" "$R/bin" "$R/api" "$R/auth"
install -d -o "$SSH_USER" -g "$SSH_USER" -m 755 "$R/www"
install -d -o teamdash -g teamdash -m 750 "$R/data"
install -d -o teamdash -g teamdash -m 755 "$R/queue"; [ -e "$R/queue/closeouts.jsonl" ] || install -o teamdash -g teamdash -m 644 /dev/null "$R/queue/closeouts.jsonl"
install -d -o teamauth -g teamauth -m 700 "$R/auth-data"
[ -e "$R/www/index.html" ] || printf '<!doctype html><title>%s</title><p>Installed. Waiting for the first upload from your bot.</p>\n' "$SITE_NAME" | install -o "$SSH_USER" -m 644 /dev/stdin "$R/www/index.html"

# ---- Caddy --------------------------------------------------------------------------------------------------------
case "$(uname -m)" in aarch64|arm64) ARCH=arm64;; x86_64|amd64) ARCH=amd64;; *) die "unsupported CPU $(uname -m)";; esac
if ! "$R/bin/caddy" version 2>/dev/null | grep -q "v$CADDY_VERSION"; then
  say "Caddy $CADDY_VERSION ($ARCH)"
  T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
  curl -fsSL -o "$T/c.tgz" "https://github.com/caddyserver/caddy/releases/download/v$CADDY_VERSION/caddy_${CADDY_VERSION}_linux_$ARCH.tar.gz"
  echo "${CADDY_SHA512[$ARCH]}  $T/c.tgz" | sha512sum -c --quiet - || die "Caddy download checksum mismatch"
  tar -xzf "$T/c.tgz" -C "$T" caddy && install -o root -g root -m 755 "$T/caddy" "$R/bin/caddy"
fi

# ---- login service venv -------------------------------------------------------------------------------------------
say "login service"
[ -x "$R/auth/venv/bin/python" ] || python3 -m venv "$R/auth/venv"
"$R/auth/venv/bin/pip" install -q --upgrade pip >/dev/null 2>&1 || true
"$R/auth/venv/bin/pip" install -q "webauthn==2.7.1" "bcrypt==4.3.0"
install -o root -g root -m 644 "$SRC/auth_service.py" "$R/auth/auth_service.py"
install -o root -g root -m 644 "$SRC/set_password.py" "$R/auth/set_password.py"
install -o root -g root -m 644 "$SRC/closeout_api.py" "$R/api/closeout_api.py"

# ---- settings file (no secrets) -----------------------------------------------------------------------------------
printf 'DASH_HOST=%s\nSITE_NAME=%s\nOWNER_DISPLAY_NAME=%s\nDASH_USERNAME=%s\nDASH_TZ=%s\n' \
  "$DASH_HOST" "$SITE_NAME" "$OWNER_DISPLAY_NAME" "$DASH_USERNAME" "$DASH_TZ" | install -o root -g root -m 644 /dev/stdin "$R/host.env"

# ---- secrets, made here and never printed ----------------------------------------------------------------------------
say "secrets (kept on this server)"
if [ ! -s "$R/check.bcrypt" ] || [ ! -s "$R/check.netrc" ]; then   # login for the loopback-only check copy (port 8088)
  "$R/auth/venv/bin/python" - "$R" <<'PY'
import os, sys, secrets, bcrypt
r = sys.argv[1]; pw = secrets.token_urlsafe(24); os.umask(0o077)
open(os.path.join(r, "check.netrc"), "w").write(f"machine 127.0.0.1 login check password {pw}\n")
open(os.path.join(r, "check.bcrypt"), "w").write(bcrypt.hashpw(pw.encode(), bcrypt.gensalt(12)).decode() + "\n")
PY
  chown root:root "$R/check.netrc" "$R/check.bcrypt"; chmod 600 "$R/check.netrc" "$R/check.bcrypt"
fi
# cookie key + passkey user handle are created by the login service itself on first start (in auth-data, mode 600)
[ -s "$R/auth-data/password.bcrypt" ] || [ -s "$R/auth-data/setup.json" ] || new_setup_code >/dev/null

# ---- Caddyfile ------------------------------------------------------------------------------------------------------
"$R/auth/venv/bin/python" - "$SRC/Caddyfile.template" "$R/check.bcrypt" "$R/Caddyfile.new" <<'PY'
import sys, os
t = open(sys.argv[1]).read(); h = open(sys.argv[2]).read().strip()
os.umask(0o027); open(sys.argv[3], "w").write(t.replace("__CHECK_HASH__", h))
PY
chown root:teamdash "$R/Caddyfile.new"; chmod 640 "$R/Caddyfile.new"
DASH_HOST="$DASH_HOST" "$R/bin/caddy" validate --config "$R/Caddyfile.new" --adapter caddyfile >/dev/null 2>&1 || die "Caddyfile did not validate"
mv -f "$R/Caddyfile.new" "$R/Caddyfile"
chown -R teamdash:teamdash "$R/data"   # "caddy validate" ran as root and may have created access.log / caddy storage as root

# ---- systemd --------------------------------------------------------------------------------------------------------
say "services"
for u in team-dashboard.service team-dashboard-api.service team-dashboard-auth.service; do install -o root -g root -m 644 "$SRC/$u" "/etc/systemd/system/$u"; done
install -d -m 755 /etc/systemd/system/team-dashboard.service.d
install -o root -g root -m 644 "$SRC/team-dashboard.service.d-host.conf" /etc/systemd/system/team-dashboard.service.d/host.conf
if command -v selinuxenabled >/dev/null && selinuxenabled; then
  restorecon -R "$R" /etc/systemd/system/team-dashboard* >/dev/null 2>&1 || true
  semanage fcontext -a -t bin_t "$R/bin/caddy" 2>/dev/null || semanage fcontext -m -t bin_t "$R/bin/caddy" 2>/dev/null || true
  restorecon "$R/bin/caddy" 2>/dev/null || chcon -t bin_t "$R/bin/caddy" 2>/dev/null || true
fi
systemctl daemon-reload
systemctl enable -q team-dashboard-auth.service team-dashboard-api.service team-dashboard.service
systemctl restart team-dashboard-auth.service team-dashboard-api.service team-dashboard.service

# ---- OS firewall (the Oracle VCN security list is separate: open 80+443 there too) --------------------------------------
if [ "${SKIP_FIREWALL:-0}" != 1 ]; then
  say "firewall: allow 80, 443"
  if command -v firewall-cmd >/dev/null && systemctl is-active -q firewalld; then
    firewall-cmd -q --permanent --add-service=http --add-service=https && firewall-cmd -q --reload
  elif command -v iptables >/dev/null; then   # Oracle's Ubuntu images: iptables with a final REJECT rule
    for p in 80 443; do iptables -C INPUT -p tcp -m state --state NEW --dport $p -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p tcp -m state --state NEW --dport $p -j ACCEPT; done
    command -v netfilter-persistent >/dev/null && netfilter-persistent save >/dev/null 2>&1 || true
  fi
fi

# ---- report (no secrets) --------------------------------------------------------------------------------------------
sleep 3
for u in team-dashboard-auth team-dashboard-api team-dashboard; do printf '%-22s %s\n' "$u" "$(systemctl is-active $u)"; done
echo "website: https://$DASH_HOST  (certificate is fetched on first visit once DNS + ports are right)"
if [ -s "$R/auth-data/password.bcrypt" ]; then echo "owner password: set"; else echo "owner password: NOT set yet (bot: set_password.py over stdin, or /login/setup with a setup code)"; fi
