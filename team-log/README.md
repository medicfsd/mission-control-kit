# Team update log (format for every bot)

One shared file on the box: `<team_log_dir>/updates.jsonl` (default `/home/box/agent-data/shared/team-log/updates.jsonl`).
Append ONE JSON object per line. Never edit or delete earlier lines. The latest line per (owner, job) is the current state.

Append safely with:  `echo '{...}' >> /home/box/agent-data/shared/team-log/updates.jsonl`

## Fields
Required: `timestamp` (ISO 8601 with your time zone offset, e.g. `2026-01-20T11:20:00-05:00`), `job`, `owner` (bot name),
`state`, `evidence`, `next_step`.
`state` is one of: `needs decision`, `blocked`, `done`, `in progress`.

Optional:
- `blocked_on`: `me` | `bot:<name>` | `missing info` | `outside response` (for blocked items)
- `ask`: `approval` (owner approves/denies; the site shows Approve / Deny) or `question` (owner answers in the Done box).
  If missing, the site guesses from the words (approve, OK to, sign off, buy, order, send... = approval).
- Put `https://` links (post, draft, cart) in `evidence` or `next_step`; the site shows them as "View item".

## Deadlines and bills (this log is the only source)
Add to a normal line: `"kind": "deadline"`, `"due_date": "YYYY-MM-DD"` (owner's local date), optional `"due_time": "HH:MM"` (24h, local),
optional `"category"` (Bills, School, ...). Bills may add `"min_due"`, `"balance"` (numbers), `"autopay"` (true / false / "unknown"),
`"last4"` ("1234"; never a full card number).
To move a date append the same job with a new `due_date`; to clear it append the same job with `"state": "done"` (or tap Done).
Credit cards (category "Bills" and "payment due" in the job) roll forward a month by themselves once the latest line is done (`box/roll_cards.py`;
optional `"due_day"`). Unpaid cards past due are not rolled. Other monthly bills: log done, then log next month's date.
Deadline lines stay off the Projects board; they show in Upcoming (overdue in red up to 60 days back, up to 60 days ahead).

Example:
`{"timestamp":"2026-01-02T14:40:00-05:00","job":"Store card (…1234) payment due","owner":"Helper","state":"in progress","kind":"deadline","due_date":"2026-01-14","category":"Bills","min_due":25.00,"autopay":"unknown","evidence":"Statement email 2026-01-01","next_step":"Pay by the due date"}`

## Dashboard taps (written by box/watch_log.py, not by bots)
When the owner taps Done / Approve / Deny, the watcher appends a line with `state: done`, `source: "dashboard"`, `closeout_id`,
evidence `Closed by <owner> on dashboard <time>[; answer: ...]` and next_step `Owner bot: act on <owner>'s answer` (or `none`).
Answers also go to `decisions.jsonl` (`status: "new"`). A relay routine passes each answer to the owning bot and then appends
`{"closeout_id": "...", "status": "relayed", "ts": "..."}`. Optional: put the routine's webhook URL/key in a chmod-600 file
named in config.json `webhook_env` and the watcher pings it once per batch.

## Daily briefing (optional, recommended)
The briefing bot saves `briefings/YYYY-MM-DD.md`: first line `# <title>`, then `## <section>` headings with `- ` or `1. ` items.
A section whose name contains "action" becomes Today's Top 3. `**Bot — job**` at the start of an item names the owner.
Headings like `## Needs <owner name>` or `## Red: needs <owner name>` feed the "Needs you" column.
After saving, run `python3 team-log/briefing_check.py` to flag items the briefing missed (snapshots in `briefings/state/`, kept 7 days).

## Usage panel (optional)
`usage/latest.json`, written by a daily routine from the Grok Bot usage report (real numbers only, never estimates):
`collected_at`, `period`, `weekly_used_pct`, optional `weekly_limit_note`, `resets_at`, `on_demand_pct` (or null),
`by_bot` [{name, pct}], `by_routine` [{name, bot?, pct, runs?, other?}], `by_day` [{date, pct}], `by_activity` [{name, pct}].
pct values are 0-100; "<1%" is pct 0.5 plus `"lt1": true`.
Then run `python3 box/update_usage.py --no-deploy` (validates it and saves a dated copy).
