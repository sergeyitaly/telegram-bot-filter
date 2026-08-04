# Air-Alarm Content Filter Bot

A Telegram moderation bot for Ukrainian community chats. It removes content
posted during/after drone or missile strikes that could help an attacker
confirm a hit or correct aim for a follow-up strike:

- **Text**: deletes messages containing strike-result keywords (приліт,
  влучання, наслідки удару, etc.), shared coordinates, or — while alarm mode
  is on — any address/location chatter.
- **Photos/videos**: while alarm mode is on, every photo/video in the chat is
  deleted and reposted **blurred** with a warning. Outside alarm mode, only
  media whose caption matches strike-result keywords is blurred.
- **Live location**: always deleted immediately.
- **Unauthorized bots**: any bot account that joins without being whitelisted
  via `/allowbot` is kicked immediately — another bot in the chat can scrape
  messages independently of us, so it's removed rather than raced. This can't
  detect human-operated "userbot" scraper accounts; Telegram doesn't expose
  that distinction to the Bot API.
- **Repeat offenders**: a member who trips the filter `VIOLATION_THRESHOLD`
  times within `VIOLATION_WINDOW_SECONDS` is auto-muted pending admin review
  (`/unmute <user_id>` to lift it).

Alarm mode toggles (`/alarm_on`, `/alarm_off`, `/status`) reply via **admin
DM**, not in the group, and the triggering command is deleted — so the timing
of a toggle isn't visible to chat members. Alarm mode can also auto-arm from
real air-raid status via [alerts.in.ua](https://devs.alerts.in.ua/) (see
below) instead of relying only on a manual `/alarm_on`.

### Why alarm mode locks permissions instead of just deleting

Deleting a flagged photo/video after the fact still leaves a window where
anyone already watching the chat — a human, or a bot/userbot scraping it —
sees it before the delete lands. Telegram delivers a message to every
member/bot the instant it's posted, independently of when *our* bot reacts,
so no amount of reaction-speed tuning closes that gap. Instead, `/alarm_on`
sets chat-wide permissions (`can_send_photos`/`videos`/etc. = false) so
regular members can't send media **at all** while active — nothing to leak.
Text stays reactive (keyword-filtered on delete) so coordination is still
possible during an alert. Admins/the chat owner are always exempt from chat
permissions by Telegram design, so this only restricts non-admin members.

## Local setup

```
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

Install `ffmpeg` locally too (needed for video blurring) — e.g.
`choco install ffmpeg` (Windows) or `apt install ffmpeg` (Linux). Without it,
video blurring silently falls back to delete-without-repost.

Copy `.env.example` to `.env`, fill in `BOT_TOKEN` (from @BotFather) and
`ADMIN_IDS` (comma-separated Telegram numeric user IDs — get yours from
@userinfobot), then load it and run:

```
export $(cat .env | xargs)   # or use a tool like direnv/python-dotenv
python main.py
```

Add the bot to your group and give it **delete messages** admin permission.

## Deploying to Render (free tier)

Video blurring needs the `ffmpeg` binary, which Render's native Python
runtime can't install (no apt access), so deploy via the included
`Dockerfile`:

1. Push this repo to GitHub.
2. On Render: **New → Web Service** → connect the repo → environment:
   **Docker** (it will auto-detect the `Dockerfile`).
3. Set environment variables in the Render dashboard — see `.env.example`
   for the full list (`BOT_TOKEN`, `ADMIN_IDS` are required; the rest are
   optional with sane defaults). Leave `PORT` unset — Render injects it
   automatically and the app reads it from `$PORT`.
4. Deploy. The service binds an HTTP server on `$PORT` (`bot/health.py`)
   purely so Render sees an open port and so an uptime pinger has something
   to hit — the bot itself talks to Telegram via long polling, not webhooks.

## Keeping it awake (Render free tier sleeps after ~15 min idle)

Render's free web services spin down after ~15 minutes without inbound HTTP
traffic, which would kill the bot's polling loop. Since the app already
exposes `GET /` returning `200 ok`, ping it periodically with a free
external monitor:

1. Sign up at [UptimeRobot](https://uptimerobot.com) or
   [cron-job.org](https://cron-job.org).
2. Create a new HTTP(S) monitor/job pointed at your Render URL, e.g.
   `https://your-bot-name.onrender.com/health`.
3. Set the check interval to **5–10 minutes** (well under the 15-minute
   sleep timer).
4. Save. No code changes needed — the health endpoint already exists for
   this.

Note: this avoids idle spin-down, but Render free instances still get a
periodic forced restart (~monthly) and have a monthly hour cap. If the chat
needs guaranteed uptime during active shelling, a paid tier or a
always-on VPS removes that risk entirely.

## Auto-arming alarm mode from real air-raid status

Set `ALERTS_API_TOKEN` and `ALERTS_OBLAST_UID` to auto-arm alarm mode from
[alerts.in.ua](https://devs.alerts.in.ua/) instead of relying only on manual
`/alarm_on` — important because Render free instances restart on every cold
start, and a drone strike can hit with no siren warning at all.

1. Get a free token at https://devs.alerts.in.ua/.
2. Find your oblast's UID from the same docs (e.g. `31` = м. Київ).
3. Set `ALERTS_API_TOKEN` and `ALERTS_OBLAST_UID` (and optionally
   `ALERTS_POLL_SECONDS`, default 60).

Every poll re-syncs every chat the bot is in to the current real-world
state — not just on a state *change* — so a chat that joins mid-alert, or a
bot that restarts mid-alert, still ends up correctly armed on the next tick
rather than waiting for the alert to toggle off and on again. One
consequence: a manual `/alarm_off` during a still-ongoing real alert gets
re-armed on the next tick — this is intentional (favors not missing a real
threat over honoring a stale manual override), not a bug.

Alarm mode set by the poller (`auto_armed`) auto-clears when the real alert
ends. Alarm mode set manually via `/alarm_on` only clears via manual
`/alarm_off` — the poller won't touch it.

## Tuning the filter

- Add keywords at runtime (admin only): `/addkeyword слово` (strike-result
  tier) or `/addkeyword назва_району location` (location tier — only fires
  during alarm mode).
- Edit `bot/keywords.py` directly for a permanent change, then redeploy.
- `PHOTO_BLUR_RADIUS` / `VIDEO_BLUR_STRENGTH` env vars control how heavy the
  blur is (higher = less recoverable detail).
- `MAX_VIDEO_MB` skips blurring (falls back to plain delete) above this size,
  to avoid timing out on Render's free CPU allocation.
- `VIOLATION_THRESHOLD` / `VIOLATION_WINDOW_SECONDS` control repeat-offender
  auto-muting sensitivity (default: mute after 3 hits in 10 minutes).
- `TRUSTED_BOT_IDS` (or runtime `/allowbot <bot_id>`) whitelists bot accounts
  that should be allowed to join without being auto-kicked.
