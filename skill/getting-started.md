---
name: getting-started
description: When the new owner first talks to this bot, or asks to set up, fix, or change their Mission Control site.
---
# Getting started: Mission Control

Open with one line: "I'll set up Mission Control, a private website that shows what your bots are doing and what needs you, on a free Oracle server; about an hour, and I'll tell you exactly when I need you."

Get the code: `git clone https://github.com/medicfsd/mission-control-kit /home/box/agent-data/shared/mission-control` (or pull if it is there). Work from that folder. Follow SETUP.md; it is the source of truth.

## Ask one question at a time
Say why you ask and what you do with the answer. Save each answer in `box/config.json` (copied from `config.example.json`) as you go.
1. "What should the site be called?" (default Mission Control) -> `site_name`.
2. "What name should it greet you by, and any nicknames your bots use for you?" -> `owner_name`, `owner_aliases`. Used for the title and the "Needs you" column.
3. "What's your time zone?" Map a city to an IANA zone and a short label -> `timezone`, `tz_label`.
4. "Which bots should post to the log, and which one writes your daily briefing?" -> `team` (or leave [] to list all), `coordinator`.
5. "Do you have an Oracle Cloud account yet?" If not, walk SETUP step 1.1 with them.
6. "What web address would you like?" Suggest `<name>-mission-control`. Check it's free on duckdns.org, then -> `dash_host` as `<name>.duckdns.org`.

## Owner steps (stop, explain, wait)
- Oracle sign-up, card check, 2FA: the owner does these. Never ask for or type their card or Oracle password.
- Run `python3 box/remote.py keygen` and show the public key line; they paste it when creating the VM (Ampere A1.Flex, 1 OCPU, 6 GB, public IP). Ask for the public IP and the image (Oracle Linux: user opc; Ubuntu: user ubuntu) -> `ssh`.
- They add ingress rules for TCP 80 and 443 from 0.0.0.0/0 in the VCN's default security list.
- DuckDNS: they sign in with Google, GitHub, X or Reddit, add the subdomain, set its IP. Tell them never to paste the DuckDNS token; it isn't needed.
- Password: send an in-chat form with one password field (12+ chars). Pipe the value only to `python3 box/set_password_remote.py` on stdin. Never echo it, save it, log it or repeat it.
- Passkey: they open the site, sign in, and add a passkey (iPhone Face ID; on a computer "Use a phone or tablet" and scan the QR). Ask them to sign out and back in with it.

## Bot steps
1. `python3 box/remote.py trust` and tell them the fingerprint saved.
2. Check DNS: `getent hosts <dash_host>` matches the IP; wait if not.
3. `python3 box/server_install.py`. If it fails, read the error, fix (most often ports or DNS), run again; it is safe to repeat.
4. Create `/home/box/agent-data/shared/team-log/`, copy `team-log/README.md` there. Give the owner one paste-in line for each logging bot (SETUP 5.2).
5. `bash box/build_and_deploy.sh --full`; report the check result plainly.
6. Start `box/watch_log.py` in the background and create a routine that restarts it if it stops (ask before creating the routine).
7. Offer the optional private GitHub backup (SETUP 6). The owner creates the repo and signs in `gh`; never make it public.

## Rules
- Secrets live only on the server or in files the scripts create with mode 600. Never print, copy or send passwords, keys, tokens, session secrets or password hashes.
- Don't change the Oracle account, DuckDNS or GitHub for them; give the exact click path.
- After each step, one short line: what's done, what's next, and whether you need them.
- Done: send the site link, say how to add bots later (edit `team`, run build_and_deploy.sh --full), and how to reset the password (form again).
