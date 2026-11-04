# Mission Control kit

A private, password + passkey protected website that shows what your Grok Bot team is doing:
today's briefing, items that need you (with Approve / Deny / Done buttons), work in progress,
upcoming deadlines and bills, usage, and one card per bot.

How it works, in one line: your bots append one JSON line per update to a shared team log on the box;
`box/build_and_deploy.sh` turns the log into `www/data.json` and copies it to a small server
(an Oracle Cloud Always Free VM) that serves the site over HTTPS behind a login.

Start with **SETUP.md** (plain-words walkthrough). The "getting-started" bot skill does most of it for you.

```
mission-control-kit/
  README.md              this file
  SETUP.md               step-by-step setup, owner steps marked [YOU]
  box/                   runs on the Grok Bot box
    config.example.json  copy to config.json: the ONE file with your names, time zone, site address
    mc_config.py         loads config.json for every script
    build_data.py        team log + briefing -> www/data.json
    roll_cards.py        rolls paid credit-card due dates forward a month
    update_usage.py      checks and saves usage/latest.json for the Usage panel
    build_and_deploy.sh  roll cards, build, upload, check (add --full after a design change)
    deploy.py            uploads www/ to the server
    check.py             after a deploy: data matches, sign-in still required
    watch_log.py         background watcher: rebuilds ~1 min after the log changes, applies Done/Approve taps
    remote.py            SSH helper: keygen, trust, run, put
    server_install.py    uploads server/ and runs install.sh on the VM
    set_password_remote.py  sets the site password without showing it anywhere
    tunnel.py            loopback checks used by check.py
    rollback.sh          mark a good state / go back one or two
    www/                 the site (index.html, bot.html, closeout.js)
  server/                runs on the VM (install.sh puts it in place)
    install.sh           idempotent installer for Oracle Linux 8/9 or Ubuntu 22.04/24.04
    auth_service.py      login: password, passkeys, first-run setup page, sessions
    set_password.py      sets the password from stdin
    closeout_api.py      receives Done / Approve / Deny taps
    Caddyfile.template   HTTPS (automatic certificate), forward auth, security headers
    *.service, *.conf    systemd units
    host.env.example     what install.sh writes to /opt/team-dashboard/host.env
  team-log/
    README.md            the log format your bots follow (paste into each bot's instructions)
    briefing_check.py    compares each daily briefing with the log and flags gaps
  skill/getting-started.md  the walkthrough skill for the shared bot template
  examples/config.sample.json  a filled-in fake config (used by the test)
  tests/                 run_local_test.sh: builds the site from fake data, no server needed
```

Nothing in this kit holds a secret. Passwords, passkeys, session keys and the HTTPS certificate are
created on your server and stay there. Your SSH private key is created on the box and stays there.

Try it without a server: `bash tests/run_local_test.sh`
This project is MIT licensed.
