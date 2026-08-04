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

Alarm mode is toggled per chat by an admin (`/alarm_on`, `/alarm_off`) —
this MVP does not auto-poll an external air-raid API, admins arm it when a
strike/alert is happening in their region.

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
3. Set environment variables in the Render dashboard: `BOT_TOKEN`,
   `ADMIN_IDS`. Leave `PORT` unset — Render injects it automatically and the
   app reads it from `$PORT`.
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

## Tuning the filter

- Add keywords at runtime (admin only): `/addkeyword слово` (strike-result
  tier) or `/addkeyword назва_району location` (location tier — only fires
  during alarm mode).
- Edit `bot/keywords.py` directly for a permanent change, then redeploy.
- `PHOTO_BLUR_RADIUS` / `VIDEO_BLUR_STRENGTH` env vars control how heavy the
  blur is (higher = less recoverable detail).
- `MAX_VIDEO_MB` skips blurring (falls back to plain delete) above this size,
  to avoid timing out on Render's free CPU allocation.
