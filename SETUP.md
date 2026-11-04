# Mission Control setup, in plain words

About an hour, mostly waiting. Steps marked **[YOU]** need you (sign-ups, cards, 2FA, typing a password, Face ID).
Everything else your bot can do on its box. Nothing here costs money if you stay on Oracle's Always Free resources.

What you end up with: `https://<your-name>.duckdns.org`, a private site only you can open (password, then a passkey).

Put the kit on the box at `/home/box/agent-data/shared/mission-control` (so all your bots can reach the team log next to it).
Commands below run from that folder.

---

## 1. Oracle Cloud server (free)

1. **[YOU]** Sign up at https://signup.cloud.oracle.com. You need an email, a phone and a card (identity check only; Always Free is not charged).
   Pick your **home region** carefully: it can't be changed later, and Always Free servers live only there. Set up 2FA when asked.
2. Bot: makes the box's SSH key and shows you ONLY the public half:
   `python3 box/remote.py keygen` (the private key stays on the box, mode 600).
3. **[YOU]** In the Oracle console: Menu, Compute, Instances, **Create instance**.
   - Name: anything (e.g. `mission-control`).
   - Image: **Oracle Linux 9** (user `opc`) or **Canonical Ubuntu 24.04** (user `ubuntu`).
   - Shape: Change shape, **Ampere**, `VM.Standard.A1.Flex`, 1 OCPU, 6 GB memory (well inside Always Free).
     If you see "Out of capacity", try another availability domain, try again later, or use the AMD
     `VM.Standard.E2.1.Micro` (also Always Free; slower but fine).
   - Networking: create a new virtual cloud network with a **public subnet**, and keep **Assign a public IPv4 address** on.
   - Add SSH keys: **Paste public keys**, and paste the line the bot showed you (starts with `ssh-ed25519`).
   - Create. When it says Running, copy the **Public IP address** and give it to the bot.
4. **[YOU]** Open the web ports in Oracle's network firewall (the server's own firewall is handled by the installer):
   Networking, Virtual cloud networks, your network, **Security** (or Subnets, your subnet), **Default Security List**, **Add Ingress Rules**:
   - Source CIDR `0.0.0.0/0`, IP protocol TCP, destination port `80`
   - Source CIDR `0.0.0.0/0`, IP protocol TCP, destination port `443`
   Port 22 (SSH) is already open by default. Leave it.
5. Bot: puts the IP (and `ubuntu` as the user if you chose Ubuntu) in `box/config.json` under `ssh`, then runs
   `python3 box/remote.py trust`, which shows the server's fingerprint and saves it. From now on the box refuses any other server at that address.

## 2. Website name (free DuckDNS address)

1. Pick a name: lowercase letters, digits and dashes, e.g. `sams-mission-control`. Your address becomes `sams-mission-control.duckdns.org`.
   This address is also what your passkey is tied to; changing it later means making a new passkey.
2. **[YOU]** Go to https://www.duckdns.org and sign in. DuckDNS has no passwords of its own: you sign in with **Google, GitHub, X or Reddit**.
3. **[YOU]** Type the name in the **sub domain** box and press **add domain**. In the **current ip** box next to it,
   put the server's public IP and press **update ip**.
   The page also shows a **token**. Don't share it or paste it into chat; this setup never needs it (the server IP doesn't change).
4. Bot: sets `dash_host` in `box/config.json` and waits until the name points at the server (a few minutes at most).

Also decided here (the bot asks): the site title (e.g. "Mission Control"), the name it greets you by, your time zone and its short label (e.g. `America/New_York`, `ET`).

## 3. Install the server

Bot: `python3 box/server_install.py`
It uploads `server/` and runs `install.sh` on the VM as root. The installer:
installs Caddy (pinned version, checksum checked) for HTTPS with a free automatic certificate, a small login service
and the Done/Approve service, creates their users and folders, opens ports 80/443 in the server firewall, sets SELinux labels on Oracle Linux,
and starts everything. It is safe to run again (after an update, or if something failed); it keeps existing passwords and passkeys.
All secrets (session key, the check login) are made on the server and never printed.

If the certificate step fails, it is almost always step 1.4 (ports) or step 2.3 (wrong IP). Fix it and run the installer again.

## 4. Password and passkey

1. **[YOU]** Choose the site password (12+ characters). The bot shows you a private form in the chat; type it there.
   The bot pipes it straight to the server (`box/set_password_remote.py`); it is never shown, saved on the box, or put in the chat log.
   (Alternative if you have a terminal on the server: `sudo bash ~/mission-control-server/install.sh --new-setup-code` prints a one-time code,
   valid 24 hours, then open `https://<your-name>.duckdns.org/login/setup` and set the password there.)
2. **[YOU]** Open `https://<your-name>.duckdns.org` and sign in with the password. You land on **Add a passkey**.
   - iPhone or iPad: tap it and use Face ID / Touch ID. The passkey is saved in your iCloud Keychain.
   - Android: use your fingerprint or screen lock.
   - Computer: use the computer's own (Touch ID, Windows Hello, PIN) or choose **Use a phone or tablet** and scan the QR code with your phone.
   Add one on each device you use. After that, sign in with the passkey; the password stays as a backup.
3. **[YOU]** Sign out and sign back in with the passkey once, to make sure it works.

## 5. Team log, then build and deploy

1. Bot: creates `/home/box/agent-data/shared/team-log/` and copies `team-log/README.md` there.
2. **[YOU]** Choose which bots post to the log. The bot gives you one paragraph to paste into each of them (or adds it itself where it can):
   "After any real progress, append one line to the team log in /home/box/agent-data/shared/team-log/updates.jsonl following its README."
   Optional: a daily briefing bot that writes `briefings/YYYY-MM-DD.md` (see the README) and a daily usage routine.
3. Bot: `bash box/build_and_deploy.sh --full` builds the site and runs checks (data matches, sign-in is required).
4. Bot: starts the watcher, which rebuilds about a minute after any log change and applies your Done/Approve taps:
   `nohup python3 box/watch_log.py >> box/watch.log 2>&1 &`
   and a routine (e.g. hourly) that restarts it if it isn't running.

Open the site. Your bots' updates show up within about a minute.

## 6. Optional: private GitHub backup

1. **[YOU]** Create a **private** repository on GitHub (empty, no README) and sign the box's `gh` tool in when the bot asks.
2. Bot: `git init` in the kit folder, commits (the `.gitignore` keeps out `config.json`, keys, log data and anything secret),
   and pushes. Set `"git_auto_commit": true` in config.json to commit after each `--full` deploy.
3. `box/rollback.sh mark` saves a known-good point; `box/rollback.sh 1` (or `2`) goes back to it as a new commit and redeploys. Log data is never rolled back.

## Later
- Change names or time zone: edit `box/config.json`, run `build_and_deploy.sh --full`; for the server side run `server_install.py` again.
- New password: run step 4.1 again. Lost every passkey: sign in with the password and add a new one.
- Remove everything: on the server `sudo systemctl disable --now team-dashboard team-dashboard-auth team-dashboard-api`,
  then terminate the instance in the Oracle console **[YOU]** and delete the DuckDNS name **[YOU]**.
