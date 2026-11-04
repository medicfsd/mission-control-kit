#!/usr/bin/env python3
"""Mission Control login service (passkeys + password fallback). Listens on 127.0.0.1:8090 only.
Caddy uses it two ways:
  forward_auth  -> GET /auth/verify       200 = signed in; else 302 to /login (page loads) or 401 (fetch/API)
  reverse_proxy -> /login, /login/passkey, /logout, /auth/*   (the login UI + JSON endpoints)
State lives in DATA (owner teamauth, mode 700): secret.key (cookie HMAC key), password.bcrypt (owner password hash,
written by set_password.py or the one-time /login/setup page), user.json (WebAuthn user handle), credentials.json
(registered passkeys: public keys only), setup.json (only until the first password is set: hash of a one-time code).
Names and the time zone come from /opt/team-dashboard/host.env (DASH_HOST, SITE_NAME, OWNER_DISPLAY_NAME, DASH_USERNAME, DASH_TZ).
Never logs passwords, hashes, cookies or the secret. Runs via team-dashboard-auth.service."""
import json, os, hmac, hashlib, secrets, time, threading, base64, datetime, re, sys
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlsplit, parse_qs, quote
import bcrypt
from webauthn import (generate_registration_options, verify_registration_response, generate_authentication_options,
                      verify_authentication_response, options_to_json)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url, parse_client_data_json
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, ResidentKeyRequirement,
                                      UserVerificationRequirement, PublicKeyCredentialDescriptor, AttestationConveyancePreference)

DATA = os.environ.get("AUTH_DATA", "/opt/team-dashboard/auth-data")
PORT = int(os.environ.get("AUTH_PORT", "8090"))
RP_ID = (os.environ.get("DASH_HOST") or "").strip().lower()   # from /opt/team-dashboard/host.env
if not RP_ID: sys.exit("DASH_HOST is not set (put it in /opt/team-dashboard/host.env)")
SITE_NAME = (os.environ.get("SITE_NAME") or "Mission Control").strip()
OWNER = (os.environ.get("OWNER_DISPLAY_NAME") or "").strip()
RP_NAME = SITE_NAME
ORIGIN = os.environ.get("AUTH_ORIGIN_OVERRIDE") or "https://" + RP_ID   # override only for local tests
USERNAME = (os.environ.get("DASH_USERNAME") or "owner").strip().lower()
SESSION_COOKIE = "__Host-mc_session"     # __Host- => Secure, Path=/, no Domain (blocks cookie tossing from sibling subdomains)
DEVICE_COOKIE = "__Host-mc_pk"           # "this browser/device already has a passkey" (skip the save-passkey offer)
SESSION_TTL = 30 * 86400
MAX_BODY = 65536
try:
    from zoneinfo import ZoneInfo
    AZ = ZoneInfo(os.environ.get("DASH_TZ") or "UTC")
except Exception:
    AZ = datetime.timezone.utc
MIN_PW = 12
LOCK = threading.Lock()

def log(msg): print(datetime.datetime.now(AZ).strftime("%Y-%m-%d %H:%M:%S %Z"), msg, flush=True)
def rd(name, default=None):
    try:
        with open(os.path.join(DATA, name)) as f: return f.read()
    except FileNotFoundError: return default
def wr(name, text):
    p = os.path.join(DATA, name); tmp = p + ".tmp"
    with open(tmp, "w") as f: f.write(text)
    os.chmod(tmp, 0o600); os.replace(tmp, p)

# ---- one-time init: secret + user handle --------------------------------------------------------------
os.umask(0o077)
if not rd("secret.key"): wr("secret.key", secrets.token_hex(32))
SECRET = bytes.fromhex(rd("secret.key").strip())
if not rd("user.json"): wr("user.json", json.dumps({"user_handle": bytes_to_base64url(secrets.token_bytes(32))}))
USER_HANDLE = base64url_to_bytes(json.loads(rd("user.json"))["user_handle"])
def pw_hash():
    h = (rd("password.bcrypt") or "").strip()
    return h.encode() if h.startswith("$2") else None
def set_pw(pw):
    wr("password.bcrypt", bcrypt.hashpw(pw.encode(), bcrypt.gensalt(12)).decode() + "\n")
def setup_ok(code):
    """One-time first-run code (install.sh stores only its sha256 + expiry in setup.json). Valid until used or expired."""
    try: d = json.loads(rd("setup.json") or "{}")
    except ValueError: return False
    if not d.get("sha256") or int(d.get("expires", 0)) < time.time() or not code: return False
    return hmac.compare_digest(hashlib.sha256(code.strip().encode()).hexdigest(), d["sha256"])
def creds():
    return json.loads(rd("credentials.json", "[]") or "[]")
def save_creds(c): wr("credentials.json", json.dumps(c, indent=1))

# ---- signed session cookie ----------------------------------------------------------------------------
def b64e(b): return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
def b64d(s): return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
def make_session(method):
    now = int(time.time())
    body = b64e(json.dumps({"u": USERNAME, "iat": now, "exp": now + SESSION_TTL, "m": method, "n": secrets.token_hex(8)},
                           separators=(",", ":")).encode())
    return "v1." + body + "." + b64e(hmac.new(SECRET, ("v1." + body).encode(), hashlib.sha256).digest())
def read_session(val):
    try:
        v, body, sig = val.split(".")
        if v != "v1": return None
        if not hmac.compare_digest(b64e(hmac.new(SECRET, ("v1." + body).encode(), hashlib.sha256).digest()), sig): return None
        d = json.loads(b64d(body))
        if d.get("u") != USERNAME or int(d.get("exp", 0)) < time.time(): return None
        return d
    except Exception: return None

# ---- rate limiting (password + passkey failures) ------------------------------------------------------
FAILS = {}            # ip -> [timestamps]
GLOBAL_FAILS = []
def limited(ip):
    now = time.time()
    with LOCK:
        f = FAILS.get(ip, []); f = [t for t in f if now - t < 900]; FAILS[ip] = f
        GLOBAL_FAILS[:] = [t for t in GLOBAL_FAILS if now - t < 3600]
        return len(f) >= 5 or len(GLOBAL_FAILS) >= 30
def fail(ip):
    with LOCK:
        FAILS.setdefault(ip, []).append(time.time()); GLOBAL_FAILS.append(time.time())
        if len(FAILS) > 5000: FAILS.clear()

# ---- WebAuthn challenges (single use, 5 min) ----------------------------------------------------------
CHAL = {}             # b64url challenge -> (purpose, expiry, session nonce or None)
def put_chal(ch, purpose, nonce=None):
    now = time.time()
    with LOCK:
        for k in [k for k, v in CHAL.items() if v[1] < now]: CHAL.pop(k, None)
        if len(CHAL) > 2000: CHAL.clear()
        CHAL[bytes_to_base64url(ch)] = (purpose, now + 300, nonce)
def take_chal(ch_b64, purpose, nonce=None):
    with LOCK: v = CHAL.pop(ch_b64, None)
    return bool(v and v[0] == purpose and v[1] >= time.time() and (nonce is None or v[2] == nonce))

def safe_next(n):
    n = (n or "/").strip()
    if not n.startswith("/") or n.startswith("//") or n.startswith("/\\") or any(c in n for c in "\r\n\t\\") or len(n) > 500:
        return "/"
    if n.startswith("/login") or n.startswith("/logout") or n.startswith("/auth/"): return "/"
    return n
def device_name(ua):
    ua = ua or ""
    for k, v in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Macintosh", "Mac"), ("Windows", "Windows"),
                 ("CrOS", "Chromebook"), ("Linux", "Linux")):
        if k in ua: return v
    return "Device"
def esc(s): return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))

# ---- pages --------------------------------------------------------------------------------------------
CSS = """*{box-sizing:border-box}html,body{margin:0;background:#0b1020;color:#e8ecf6;font:16px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:400px;margin:0 auto;padding:max(28px,env(safe-area-inset-top)) 20px 40px;min-height:100vh;display:flex;flex-direction:column;justify-content:center}
.card{background:#141b31;border:1px solid #26304f;border-radius:18px;padding:26px 22px;box-shadow:0 10px 30px rgba(0,0,0,.35)}
h1{font-size:22px;margin:0 0 4px;text-align:center}h1 span{color:#7aa2ff}.sub{color:#9aa6c6;font-size:14px;text-align:center;margin:0 0 22px}
.btn{display:block;width:100%;border:0;border-radius:12px;padding:14px 16px;font-size:17px;font-weight:600;cursor:pointer;text-align:center;text-decoration:none}
.pri{background:#3b6cff;color:#fff}.pri:active{background:#2c56d6}.sec{background:#222b47;color:#e8ecf6;border:1px solid #33406a}
.hint{color:#9aa6c6;font-size:13px;margin:8px 2px 0;text-align:center}.or{display:flex;align-items:center;gap:10px;color:#6f7ba0;font-size:13px;margin:22px 0}
.or:before,.or:after{content:"";flex:1;height:1px;background:#26304f}label{display:block;font-size:13px;color:#9aa6c6;margin:0 0 6px}
input{width:100%;font-size:17px;padding:13px 14px;border-radius:12px;border:1px solid #33406a;background:#0e1428;color:#e8ecf6;margin:0 0 14px;-webkit-appearance:none}
input:focus{outline:2px solid #3b6cff;border-color:#3b6cff}.msg{border-radius:10px;padding:10px 12px;font-size:14px;margin:0 0 16px}
.err{background:#3a1520;color:#ffb3c0;border:1px solid #6b2333}.ok{background:#12301f;color:#a6f0c0;border:1px solid #1f5a37}.info{background:#16213f;color:#b9c8ff;border:1px solid #2a3a6e}
.links{text-align:center;margin-top:18px;font-size:14px}.links a{color:#9ab4ff;text-decoration:none;margin:0 8px}.hidden{display:none}
.desk{display:none}@media (hover:hover) and (pointer:fine) and (min-width:768px){.desk{display:block}}"""
def page(title, body, script=True):
    return ("<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content=\"width=device-width,initial-scale=1,viewport-fit=cover\">"
            "<meta name=robots content=noindex><meta name=color-scheme content=dark><title>" + esc(title) + " · " + esc(SITE_NAME) + "</title>"
            "<style>" + CSS + "</style></head><body><main>" + body + "</main>"
            + ("<script src=\"/auth/login.js\" defer></script>" if script else "") + "</body></html>")
ERRS = {"bad": ("err", "Wrong username or password."), "rate": ("err", "Too many attempts. Wait 15 minutes and try again."),
        "setup": ("err", "That setup code is wrong or expired, or the passwords don't match (use at least 12 characters)."),
        "pk": ("err", "Passkey sign-in didn't work. Try again or use your password."), "out": ("ok", "You're signed out."),
        "origin": ("err", "Your browser didn't confirm this sign-in came from this page, so it was blocked for safety. "
                   "Make sure the address bar shows " + RP_ID + ", reload this page and try again.")}
H1 = (esc(OWNER) + "'s " if OWNER else "") + "<span>" + esc(SITE_NAME) + "</span>"
def setup_page(e):
    m = ERRS.get(e)
    return page("First-time setup", f"""<div class=card><h1>{H1}</h1><p class=sub>First-time setup: choose your password</p>
{f'<div class="msg {m[0]}">{esc(m[1])}</div>' if m else ''}
<form method=post action="{ORIGIN}/login/setup" autocomplete=on>
<label for=code>Setup code (from your bot)</label><input id=code name=code type=text autocomplete=one-time-code autocapitalize=none autocorrect=off spellcheck=false required>
<input type=hidden name=username autocomplete=username value="{esc(USERNAME)}">
<label for=password>New password (12+ characters)</label><input id=password name=password type=password autocomplete=new-password minlength=12 required>
<label for=password2>Same password again</label><input id=password2 name=password2 type=password autocomplete=new-password minlength=12 required>
<button class="btn pri" type=submit>Save password</button></form>
<p class=hint>Your username is <b>{esc(USERNAME)}</b>. Next you can save a passkey.</p></div>""", script=False)
def login_page(nxt, e):
    m = ERRS.get(e)
    return page("Sign in", f"""<div class=card><h1>{H1}</h1><p class=sub>Sign in to continue</p>
{f'<div class="msg {m[0]}">{esc(m[1])}</div>' if m else ''}<div id=pkmsg class="msg err hidden" role=alert></div>
<div id=pkbox class=hidden><button type=button class="btn pri" id=pkbtn>🔑 Sign in with passkey</button>
<p class="hint desk">On a computer? Choose "Use a phone" (or "Use another device") and scan the QR code with your phone.</p><div class=or>or use your password</div></div>
<form method=post action="{ORIGIN}/auth/password" id=pwform autocomplete=on>
<input type=hidden name=next id=next value="{esc(nxt)}">
<label for=username>Username</label><input id=username name=username type=text autocomplete="username webauthn" autocapitalize=none autocorrect=off spellcheck=false required>
<label for=password>Password</label><input id=password name=password type=password autocomplete="current-password" required>
<button class="btn sec" type=submit>Sign in with password</button></form></div>""")
def passkey_page(nxt, n):
    return page("Save a passkey", f"""<div class=card><h1>Save a <span>passkey</span></h1>
<p class=sub>Next time, sign in with Face ID, Touch ID or your device PIN instead of typing your password.{f' You have {n} passkey' + ('s' if n != 1 else '') + ' saved.' if n else ''}</p>
<div id=pkmsg class="msg err hidden" role=alert></div><div id=pkok class="msg ok hidden">✓ Passkey saved. Opening your dashboard…</div>
<div id=pkreg class=hidden><button type=button class="btn pri" id=regbtn>🔑 Save a passkey on this device</button>
<p class=hint>Your device will ask to save a passkey for “{RP_ID}”. Tap Continue and confirm (Face ID, fingerprint or PIN).
On a computer without one, choose "Use a phone" and scan the QR code with your phone.</p></div>
<p id=nopk class="msg info hidden">This browser can't create passkeys. Open this page in Safari (iPhone) or Chrome (Android).</p>
<input type=hidden id=next value="{esc(nxt)}"><div style="height:12px"></div><a class="btn sec" id=skip href="{esc(nxt)}">Not now, go to dashboard</a>
<div class=links><a href="/logout">Sign out</a></div></div>""")

JS = r"""(()=>{'use strict';
const $=id=>document.getElementById(id);
if(location.host!=='__RPID__'){location.replace('https://__RPID__'+location.pathname+location.search);return}
const b2u=b=>{const a=new Uint8Array(b);let s='';for(const c of a)s+=String.fromCharCode(c);return btoa(s).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'')};
const u2b=s=>{s=s.replace(/-/g,'+').replace(/_/g,'/');while(s.length%4)s+='=';const r=atob(s),a=new Uint8Array(r.length);for(let i=0;i<r.length;i++)a[i]=r.charCodeAt(i);return a.buffer};
try{const u=new URL(location.href);if(u.searchParams.has('e')){u.searchParams.delete('e');history.replaceState(null,'',u.pathname+(u.search||''))}}catch(_){}
const nxt=()=>($('next')&&$('next').value)||'/';
const show=(id,t)=>{const e=$(id);if(!e)return;if(t!==undefined)e.textContent=t;e.classList.remove('hidden')};
const hide=id=>{const e=$(id);e&&e.classList.add('hidden')};
async function post(url,body){const r=await fetch(url,{method:'POST',credentials:'same-origin',cache:'no-store',
  headers:{'Content-Type':'application/json','X-Requested-With':'mc'},body:JSON.stringify(body||{})});
  let j={};try{j=await r.json()}catch(_){};if(!r.ok)throw new Error(j.error||('HTTP '+r.status));return j}
const hasPK=!!(window.PublicKeyCredential&&navigator.credentials&&navigator.credentials.get);
// ---------- sign in ----------
let ac=null;
async function getOpts(){const o=await post('/auth/passkey/login/options');o.challenge=u2b(o.challenge);
  if(o.allowCredentials)o.allowCredentials=o.allowCredentials.map(c=>Object.assign({},c,{id:u2b(c.id)}));return o}
async function finishLogin(cred){const r=cred.response;
  const j=await post('/auth/passkey/login/verify',{id:cred.id,rawId:b2u(cred.rawId),type:cred.type,authenticatorAttachment:cred.authenticatorAttachment||null,
    clientExtensionResults:{},response:{clientDataJSON:b2u(r.clientDataJSON),authenticatorData:b2u(r.authenticatorData),signature:b2u(r.signature),
    userHandle:r.userHandle?b2u(r.userHandle):null},next:nxt()});location.replace(j.next||'/')}
async function conditional(){try{if(!(await PublicKeyCredential.isConditionalMediationAvailable()))return;ac=new AbortController();
  const o=await getOpts();const c=await navigator.credentials.get({publicKey:o,mediation:'conditional',signal:ac.signal});if(c)await finishLogin(c)}
  catch(e){if(e&&e.name!=='AbortError'&&e.name!=='NotAllowedError')show('pkmsg','Passkey sign-in failed: '+e.message)}}
async function modal(){hide('pkmsg');if(ac){try{ac.abort()}catch(_){}ac=null}
  try{const o=await getOpts();const c=await navigator.credentials.get({publicKey:o});await finishLogin(c)}
  catch(e){if(e&&e.name==='NotAllowedError')show('pkmsg','Passkey sign-in was cancelled or no passkey was found. Use your password, then save a passkey.');
    else if(!(e&&e.name==='AbortError'))show('pkmsg','Passkey sign-in failed: '+(e&&e.message||e))}}
if($('pkbtn')&&hasPK){show('pkbox');$('pkbtn').addEventListener('click',modal);conditional()}
// ---------- register (already signed in) ----------
async function register(){hide('pkmsg');const b=$('regbtn');b.disabled=true;
  try{const o=await post('/auth/passkey/register/options');o.challenge=u2b(o.challenge);o.user.id=u2b(o.user.id);
    if(o.excludeCredentials)o.excludeCredentials=o.excludeCredentials.map(c=>Object.assign({},c,{id:u2b(c.id)}));
    const c=await navigator.credentials.create({publicKey:o});const r=c.response;
    await post('/auth/passkey/register/verify',{id:c.id,rawId:b2u(c.rawId),type:c.type,authenticatorAttachment:c.authenticatorAttachment||null,clientExtensionResults:{},
      response:{clientDataJSON:b2u(r.clientDataJSON),attestationObject:b2u(r.attestationObject),transports:r.getTransports?r.getTransports():[]}});
    hide('pkreg');show('pkok');setTimeout(()=>location.replace(nxt()),1500)}
  catch(e){b.disabled=false;if(e&&e.name==='InvalidStateError')show('pkmsg','This device already has a passkey for this dashboard. You can use it next time.');
    else if(e&&e.name==='NotAllowedError')show('pkmsg','Cancelled. Tap the button to try again.');else show('pkmsg','Could not save the passkey: '+(e&&e.message||e))}}
if($('regbtn')){if(window.PublicKeyCredential&&navigator.credentials&&navigator.credentials.create){show('pkreg');$('regbtn').addEventListener('click',register)}else show('nopk')}
})();"""

# ---- HTTP ---------------------------------------------------------------------------------------------
class H(BaseHTTPRequestHandler):
    server_version = "mc-auth"; sys_version = ""; protocol_version = "HTTP/1.1"
    def log_message(self, fmt, *a): pass
    def ip(self):
        x = self.headers.get("X-Forwarded-For", "")
        return (x.split(",")[-1].strip() if x else self.client_address[0])[:64]
    def cookies(self):
        out = {}
        for part in (self.headers.get("Cookie") or "").split(";"):
            if "=" in part:
                k, v = part.split("=", 1); out[k.strip()] = v.strip()
        return out
    def session(self):
        v = self.cookies().get(SESSION_COOKIE); return read_session(v) if v else None
    def reply(self, code, body=b"", ctype="text/plain; charset=utf-8", headers=()):
        if isinstance(body, str): body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self'; "
                         "img-src 'self' data:; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        for k, v in headers: self.send_header(k, v)
        self.end_headers()
        if body and self.command != "HEAD": self.wfile.write(body)
    def redirect(self, loc, headers=()): self.reply(303, "", headers=[("Location", loc)] + list(headers))
    def json(self, code, obj, headers=()): self.reply(code, json.dumps(obj), "application/json", headers)
    def set_session(self, method):
        return ("Set-Cookie", f"{SESSION_COOKIE}={make_session(method)}; Path=/; Max-Age={SESSION_TTL}; HttpOnly; Secure; SameSite=Lax")
    def set_device(self):
        return ("Set-Cookie", f"{DEVICE_COOKIE}=1; Path=/; Max-Age=34560000; HttpOnly; Secure; SameSite=Lax")
    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY: raise ValueError("too large")
        return self.rfile.read(n) if n else b""
    def origin_ok(self):
        """CSRF check. Accept: Origin == https://<DASH_HOST>. If Origin is missing or "null" (Safari sends "null" on
        form POSTs under a no-referrer policy), accept when Referer starts with our origin or Sec-Fetch-Site says
        same-origin/none. Anything that names another site (Origin, Referer or Sec-Fetch-Site) is rejected."""
        o = (self.headers.get("Origin") or "").strip()
        ref = (self.headers.get("Referer") or "").strip()
        sfs = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if o == ORIGIN: ok = sfs in ("", "same-origin")
        elif o and o != "null": ok = False
        elif sfs in ("cross-site", "same-site"): ok = False
        elif ref: ok = ref == ORIGIN or ref.startswith(ORIGIN + "/")
        else: ok = sfs in ("same-origin", "none")
        if not ok:
            log(f"origin check: rejected {self.command} {urlsplit(self.path).path} ip={self.ip()} origin={o[:80] or '-'} "
                f"referer={urlsplit(ref).netloc[:80] if ref else '-'} sec-fetch-site={sfs or '-'} want={ORIGIN}")
        return ok
    def password_origin_ok(self):
        """Password form only (login CSRF is low risk: attacker would need the password). Accept unless a header
        POSITIVELY names another site. Missing/"null" Origin, missing Referer and missing Sec-Fetch-* are all fine."""
        o = (self.headers.get("Origin") or "").strip()
        ref = (self.headers.get("Referer") or "").strip()
        sfs = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        bad = []
        if o and o != "null" and o != ORIGIN: bad.append("origin")
        if sfs in ("cross-site", "same-site"): bad.append("sec-fetch-site")
        if ref and not (ref == ORIGIN or ref.startswith(ORIGIN + "/")): bad.append("referer")
        if bad:
            log(f"origin check (password): rejected ip={self.ip()} origin={o[:80] or '-'} referer={urlsplit(ref).netloc[:80] if ref else '-'} "
                f"sec-fetch-site={sfs or '-'} because={','.join(bad)}")
        else:
            log(f"password post: origin={o[:80] or '-'} referer={'ours' if ref else '-'} sec-fetch-site={sfs or '-'}")
        return not bad
    def api_ok(self):   # CSRF guard for JSON endpoints
        return (self.origin_ok() and self.headers.get("X-Requested-With") == "mc"
                and (self.headers.get("Content-Type") or "").startswith("application/json"))

    def do_HEAD(self): self.do_GET()
    def do_GET(self):
        u = urlsplit(self.path); p = u.path; q = parse_qs(u.query)
        if p == "/auth/verify":
            if self.session(): return self.reply(204)
            fwd_uri = self.headers.get("X-Forwarded-Uri") or "/"
            nav = (self.headers.get("X-Forwarded-Method", "GET") == "GET" and
                   (self.headers.get("Sec-Fetch-Mode") == "navigate" or "text/html" in (self.headers.get("Accept") or "")))
            if nav and not fwd_uri.startswith("/api/") and not fwd_uri.split("?")[0].endswith(".json"):
                return self.reply(302, "", headers=[("Location", "/login?next=" + quote(safe_next(fwd_uri), safe="/"))])
            return self.json(401, {"error": "sign in required", "login": "/login"})
        if p == "/auth/health" and not self.headers.get("X-Forwarded-For"):   # local checks only (Caddy always adds XFF)
            return self.json(200, {"ok": True, "passkeys": len(creds()), "password": bool(pw_hash())})
        if p == "/auth/login.js": return self.reply(200, JS.replace("__RPID__", RP_ID), "application/javascript; charset=utf-8")
        if p == "/login/setup":
            if pw_hash(): return self.redirect("/login")
            return self.reply(200, setup_page((q.get("e") or [""])[0]), "text/html; charset=utf-8")
        if p == "/login":
            if not pw_hash(): return self.redirect("/login/setup")
            nxt = safe_next((q.get("next") or ["/"])[0])
            if self.session() and "e" not in q: return self.redirect(nxt)
            return self.reply(200, login_page(nxt, (q.get("e") or [""])[0]), "text/html; charset=utf-8")
        if p == "/login/passkey":
            nxt = safe_next((q.get("next") or ["/"])[0])
            if not self.session(): return self.redirect("/login?next=" + quote("/login/passkey", safe="/"))
            return self.reply(200, passkey_page(nxt, len(creds())), "text/html; charset=utf-8")
        if p == "/logout":
            return self.redirect("/login?e=out", [("Set-Cookie", f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax")])
        return self.json(404, {"error": "not found"})

    def do_POST(self):
        p = urlsplit(self.path).path; ip = self.ip()
        try: raw = self.body()
        except ValueError: return self.json(413, {"error": "too large"})
        if p == "/login/setup":
            if pw_hash(): return self.redirect("/login")
            form = parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
            if not self.password_origin_ok(): return self.redirect("/login/setup?e=origin")
            if limited(ip): log(f"setup: rate limited ip={ip}"); return self.redirect("/login/setup?e=rate")
            pw = (form.get("password") or [""])[0]; pw2 = (form.get("password2") or [""])[0]
            with LOCK:
                ok = setup_ok((form.get("code") or [""])[0]) and pw == pw2 and MIN_PW <= len(pw) <= 200 and not pw_hash()
                if ok:
                    set_pw(pw)
                    try: os.remove(os.path.join(DATA, "setup.json"))
                    except FileNotFoundError: pass
            if not ok: fail(ip); log(f"setup: failed ip={ip}"); return self.redirect("/login/setup?e=setup")
            log(f"setup: owner password set ip={ip}")
            return self.redirect("/login/passkey?next=/", [self.set_session("password")])
        if p == "/auth/password":
            form = parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
            nxt = safe_next((form.get("next") or ["/"])[0]); qn = "&next=" + quote(nxt, safe="/")
            if not self.password_origin_ok(): return self.redirect("/login?e=origin" + qn)
            if limited(ip): log(f"password: rate limited ip={ip}"); return self.redirect("/login?e=rate" + qn)
            user = (form.get("username") or [""])[0].strip().lower(); pw = (form.get("password") or [""])[0]
            h = pw_hash(); ok = False
            if h and pw and len(pw) <= 200:
                try: ok = bcrypt.checkpw(pw.encode(), h)
                except ValueError: ok = False
            ok = ok and hmac.compare_digest(user, USERNAME)
            if not ok: fail(ip); log(f"password: failed ip={ip}"); return self.redirect("/login?e=bad" + qn)
            log(f"password: ok ip={ip}")
            has_pk = self.cookies().get(DEVICE_COOKIE) == "1" and len(creds()) > 0
            dest = nxt if has_pk else "/login/passkey?next=" + quote(nxt, safe="/")
            return self.redirect(dest, [self.set_session("password")])
        if not self.api_ok(): return self.json(403, {"error": "bad request"})
        try: d = json.loads(raw or b"{}")
        except ValueError: return self.json(400, {"error": "bad json"})
        if p == "/auth/passkey/login/options":
            if limited(ip): return self.json(429, {"error": "too many attempts, wait 15 minutes"})
            o = generate_authentication_options(rp_id=RP_ID, user_verification=UserVerificationRequirement.PREFERRED)
            put_chal(o.challenge, "login")   # no allowCredentials/transports: discoverable, lets browsers offer "Use a phone" (QR/hybrid)
            return self.reply(200, options_to_json(o), "application/json")
        if p == "/auth/passkey/login/verify":
            if limited(ip): return self.json(429, {"error": "too many attempts, wait 15 minutes"})
            try:
                cdj = parse_client_data_json(base64url_to_bytes(d["response"]["clientDataJSON"]))
                ch = bytes_to_base64url(cdj.challenge)
                if not take_chal(ch, "login"): raise ValueError("challenge expired, try again")
                cid = d.get("rawId") or d.get("id")
                with LOCK:
                    allc = creds(); c = next((x for x in allc if x["id"] == cid), None)
                    if not c: raise ValueError("unknown passkey (it may have been registered on another dashboard address)")
                    uh = d["response"].get("userHandle")
                    if uh and base64url_to_bytes(uh) != USER_HANDLE: raise ValueError("passkey belongs to another user")
                    v = verify_authentication_response(credential={k: d[k] for k in ("id", "rawId", "type", "response", "authenticatorAttachment", "clientExtensionResults") if k in d},
                        expected_challenge=cdj.challenge, expected_rp_id=RP_ID, expected_origin=ORIGIN,
                        credential_public_key=base64url_to_bytes(c["public_key"]), credential_current_sign_count=int(c.get("sign_count", 0)),
                        require_user_verification=False)
                    c["sign_count"] = v.new_sign_count; c["last_used"] = datetime.datetime.now(AZ).isoformat(timespec="seconds")
                    save_creds(allc)
            except Exception as e:
                fail(ip); log(f"passkey login: failed ip={ip} ({type(e).__name__})")
                return self.json(400, {"error": str(e)[:200] or "verification failed"})
            log(f"passkey login: ok ip={ip} cred={c['id'][:8]}… ({c.get('name')})")
            hdrs = [self.set_session("passkey")]
            if d.get("authenticatorAttachment") == "platform": hdrs.append(self.set_device())
            return self.json(200, {"ok": True, "next": safe_next(d.get("next"))}, hdrs)
        # ---- registration: signed-in sessions only ----
        s = self.session()
        if not s: return self.json(401, {"error": "sign in first"})
        if p == "/auth/passkey/register/options":
            o = generate_registration_options(rp_id=RP_ID, rp_name=RP_NAME, user_id=USER_HANDLE, user_name=USERNAME, user_display_name=OWNER or USERNAME,
                attestation=AttestationConveyancePreference.NONE,
                authenticator_selection=AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.REQUIRED,
                                                                       user_verification=UserVerificationRequirement.PREFERRED),
                exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(c["id"])) for c in creds()])
            put_chal(o.challenge, "register", s["n"])
            return self.reply(200, options_to_json(o), "application/json")
        if p == "/auth/passkey/register/verify":
            try:
                cdj = parse_client_data_json(base64url_to_bytes(d["response"]["clientDataJSON"]))
                if not take_chal(bytes_to_base64url(cdj.challenge), "register", s["n"]): raise ValueError("challenge expired, try again")
                v = verify_registration_response(credential={k: d[k] for k in ("id", "rawId", "type", "response", "authenticatorAttachment", "clientExtensionResults") if k in d},
                    expected_challenge=cdj.challenge, expected_rp_id=RP_ID, expected_origin=ORIGIN, require_user_verification=False)
                cid = bytes_to_base64url(v.credential_id)
                with LOCK:
                    allc = creds()
                    if any(x["id"] == cid for x in allc): raise ValueError("already registered")
                    allc.append({"id": cid, "public_key": bytes_to_base64url(v.credential_public_key), "sign_count": v.sign_count,
                                 "name": device_name(self.headers.get("User-Agent")), "transports": (d["response"].get("transports") or [])[:8],
                                 "device_type": str(getattr(v.credential_device_type, "value", v.credential_device_type)),
                                 "backed_up": bool(v.credential_backed_up),
                                 "created": datetime.datetime.now(AZ).isoformat(timespec="seconds"), "last_used": None})
                    save_creds(allc)
            except Exception as e:
                log(f"passkey register: failed ip={ip} ({type(e).__name__})")
                return self.json(400, {"error": str(e)[:200] or "verification failed"})
            log(f"passkey register: ok ip={ip} cred={cid[:8]}… ({device_name(self.headers.get('User-Agent'))}); total={len(allc)}")
            return self.json(200, {"ok": True, "count": len(allc)}, [self.set_device()])
        return self.json(404, {"error": "not found"})

if __name__ == "__main__":
    if not pw_hash(): log("no password yet: first-time setup page is open at /login/setup (needs the one-time code)")
    log(f"listening on 127.0.0.1:{PORT} rp_id={RP_ID} passkeys={len(creds())}")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
