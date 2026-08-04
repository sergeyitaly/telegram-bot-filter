# Air-Alarm Content Filter Bot

A Telegram moderation bot for Ukrainian community chats. It removes content
posted during/after drone or missile strikes that could help an attacker
confirm a hit or correct aim for a follow-up strike:

- **Text**: deletes messages containing strike-result keywords (приліт,
  влучання, наслідки удару, бавовна, шахед, прилетіло, жахнуло, etc.) or
  address/location chatter, while alarm mode is active **or** within
  `POST_ALARM_GRACE_SECONDS` after it ends (default 2h). Shared coordinates
  are deleted **unconditionally at all times** — that's a direct location leak,
  not a timing-sensitive one. Detected formats: decimal degrees (`50.45,30.52`),
  degrees-minutes-seconds (`50°27'N 30°31'E`), and Google Plus Codes (`8G7G+XH`).
  Map service URLs (Google Maps, Waze, OpenStreetMap, Apple Maps, etc.) are
  also blocked unconditionally. During an active alarm, all HTTPS links are
  blocked because Telegram's server-side link preview reveals article content
  before the message can be deleted.
- **Photos/videos**: while alarm mode is on, every photo/video in the chat is
  deleted and reposted **blurred** with a warning. Outside an active alarm
  (including during the grace window), only media whose caption matches
  strike-result keywords or coordinates is blurred.
- **Animations (GIF)**: same pipeline as video — blurred during alarm,
  deleted/blurred if caption is flagged outside alarm.
- **Voice and audio**: deleted unconditionally during active alarm (no
  transcription available); chat admins are notified for manual review during
  the grace period.
- **Live location and venue**: always deleted immediately. Telegram venue
  messages (a named place shared from the attachment menu) are treated
  identically to live location — they set the same `location` field that
  the bot already monitors.
- **Polls**: deleted during active alarm via the deny-by-default catch-all
  (poll text can describe strike locations or damage).
- **Edited messages**: re-scanned through the full classification pipeline.
  Editing a flagged message after posting is not a bypass.
- **Deny-by-default catch-all**: during active alarm any message type not
  explicitly handled is deleted. Future Telegram API additions cannot silently
  create a bypass.
- **Split messages**: a 30-second sliding context window per user detects
  coordinates or keywords split deliberately across two or three messages.
- **Unauthorized bots**: any bot account that joins without being whitelisted
  via `/allowbot` is kicked immediately. This cannot detect human-operated
  "userbot" scraper accounts; Telegram doesn't expose that distinction to the
  Bot API.
- **Repeat offenders**: a member who trips the filter `VIOLATION_THRESHOLD`
  times within `VIOLATION_WINDOW_SECONDS` is auto-muted pending admin review
  (`/unmute <user_id>` to lift it).
- **Admin-posted flagged content**: bot admins are exempt from auto-deletion,
  but if an admin posts something that matches the filter, the chat's other
  admins are DM-notified. Admin accounts are a higher-value social-engineering
  target, so blind exemption is not safe.

Whether alarm mode is on/off is posted **publicly in the group** on every
change — the underlying fact (an active air-raid alert) is already public
via official apps and sirens, and it's why members' photos/videos are
suddenly being blocked, so hiding it just confuses people. `/status` still
replies by admin DM (an on-demand query, not a state-change broadcast), and
the `/alarm_on`/`/alarm_off` command message itself is deleted so it's the
bot's own announcement people see, not who triggered it. Technical failures
(e.g. couldn't lock permissions) go to admin DM only — that's a moderation
detail, not a public status. Alarm mode can also auto-arm from real
air-raid status via [alerts.in.ua](https://devs.alerts.in.ua/) (see below)
instead of relying only on a manual `/alarm_on`.

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

The lockdown permissions are re-applied on every auto-alarm poll tick, so a
native Telegram admin silently overriding them is corrected automatically on
the next tick.

## Commands

### Chat admin commands
(`/alarm_on`, `/alarm_off`, `/status`, `/addkeyword`, `/addadmin`,
`/removeadmin`, `/unmute`, `/violations`, `/activate`)

| Command | What it does |
|---|---|
| `/alarm_on` | Activates alarm mode: locks media send permissions chat-wide, starts keyword filtering |
| `/alarm_off` | Deactivates alarm mode: restores pre-alarm permissions, begins 2h grace window |
| `/status` | DMs the calling admin with current alarm on/off state |
| `/addkeyword <term> [location]` | Adds a term to the strike-result list (or location list with `location` flag) |
| `/addadmin <user_id>` | Grants another user bot-admin rights in this chat |
| `/removeadmin <user_id>` | Revokes a previously added bot admin. Global owners can only be removed by other owners |
| `/unmute <user_id>` | Lifts an auto-mute placed by the repeat-offender logic |
| `/violations [user_id]` | DMs the calling admin the audit log for this chat (or one user) |
| `/activate` | Re-registers this chat if its registration was lost after a restart |

### Owner-only commands
(`/listkeywords`, `/mychats`, `/allowbot`)

| Command | What it does |
|---|---|
| `/listkeywords` | DMs the full keyword list to the calling owner only — never shown in group |
| `/mychats` | DMs the list of every authorized chat with title and admin IDs |
| `/allowbot <bot_id>` | Whitelists a bot account so it isn't auto-kicked when joining |

## Local setup

```
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

Install `ffmpeg` locally too (needed for video blurring) — e.g.
`choco install ffmpeg` (Windows) or `apt install ffmpeg` (Linux). Without it,
video blurring silently falls back to delete-without-repost.

Copy `.env.example` to `.env`, fill in `BOT_TOKEN` (from @BotFather),
`OWNER_IDS` (your numeric Telegram user ID — get it from @userinfobot), and
`CHAT_ADMINS` (see [Multi-group deployments](#multi-group-deployments)
below), then load it and run:

```
export $(cat .env | xargs)   # or use a tool like direnv/python-dotenv
python main.py
```

Add the bot to your group and give it **delete messages** and **restrict
members** admin permissions.

## Deploying to Render (free tier)

Video blurring needs the `ffmpeg` binary, which Render's native Python
runtime can't install (no apt access), so deploy via the included
`Dockerfile`:

1. Push this repo to GitHub.
2. On Render: **New → Web Service** → connect the repo → environment:
   **Docker** (it will auto-detect the `Dockerfile`).
3. Set environment variables in the Render dashboard — see `.env.example`
   for the full list (`BOT_TOKEN` and `OWNER_IDS` are required; everything
   else, including which chats the bot serves, is optional with sane
   defaults — a chat either needs a `CHAT_ADMINS` entry or self-activates
   when a verified admin adds the bot, see
   [Multi-group deployments](#multi-group-deployments) below). Leave `PORT`
   unset — Render injects it automatically and the app reads it from `$PORT`.
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
2. Find your region's UID(s) from the same docs. Some oblasts are a single
   UID (`31` = м. Київ); densely populated ones are split into per-raion
   UIDs that all share the oblast's name — covering the whole oblast means
   listing all of them (`Київська область` = `73,74,75,76,77,78,79`, its 7
   raions). `ALERTS_OBLAST_UID` is comma-separated, alarm arms if *any*
   listed UID has an active air-raid alert.
3. Set `ALERTS_API_TOKEN` and `ALERTS_OBLAST_UID` (and optionally
   `ALERTS_POLL_SECONDS`, default **15 seconds**).

Every poll re-syncs every chat the bot is in to the current real-world
state — not just on a state *change* — so a chat that joins mid-alert, or a
bot that restarts mid-alert, still ends up correctly armed on the next tick
rather than waiting for the alert to toggle off and on again. One
consequence: a manual `/alarm_off` during a still-ongoing real alert gets
re-armed on the next tick — this is intentional (favors not missing a real
threat over honoring a stale manual override), not a bug.

The poller also re-applies the media lockdown permissions on every tick for
active-alarm chats, correcting any override a native Telegram admin may have
applied between ticks.

Alarm mode set by the poller (`auto_armed`) auto-clears when the real alert
ends. Alarm mode set manually via `/alarm_on` only clears via manual
`/alarm_off` — the poller won't touch it.

Note: `ALERTS_OBLAST_UID` is a single region for the whole deployment. If
you're serving groups in different oblasts (see below), auto-arm only
matches the one region configured — the rest still work fine with manual
`/alarm_on`.

## Multi-group deployments

One deployed bot (one token) can serve several unrelated Telegram groups
without their admins seeing each other's activity, and without you having to
hand-configure every group yourself:

- Each chat's admins can run `/alarm_on`, `/alarm_off`, `/status`,
  `/addkeyword`, `/unmute`, `/activate`, `/addadmin`, `/removeadmin`, and
  `/violations` **only in their own chat**, and only get DMs about their own
  chat's alarm/violation activity — never another group's.
- **`OWNER_IDS`** (you, the deployer) are admin in every chat and are the
  only ones told about bot-wide security events, like someone who isn't an
  admin trying to add the bot somewhere.
- `/allowbot`, `/listkeywords`, and `/mychats` are owner-only: `/allowbot`
  exempts a bot account across every chat this deployment serves, not just
  the one the command was run in; `/listkeywords` exposes the exact filter
  wordlist — a much wider trust boundary than any one chat's admins should
  have (see [Tuning the filter](#tuning-the-filter)); `/mychats` lists
  every chat the deployment is authorized in.
- The keyword list (`/addkeyword`, `bot/keywords.py`) is shared across all
  chats, not per-group — any chat's admin extending it affects filtering
  everywhere.

There are two ways a chat gets onto the allowlist — mix and match:

**Hardcoded (`CHAT_ADMINS`)** — you already know the group and its admins,
set once in env vars:

```
OWNER_IDS=583805446
CHAT_ADMINS=-1001111111111:222222222; -1002222222222:333333333,444444444
```

**Self-service (automatic, no token, no owner involvement)** — any admin you
trust can activate the bot in their own group entirely on their own:

1. They add the bot to their group.
2. The bot checks, via the Telegram API (not their say-so), whether whoever
   added it is an actual admin/creator of that specific chat.
3. If yes: it activates immediately — posts a confirmation in the group,
   registers that person as the chat's admin, and DMs the owners for
   awareness. If no (a non-admin member added it): it leaves immediately
   and DMs the owners who tried.
4. That admin can add co-admins for their group with `/addadmin <user_id>`
   and revoke them with `/removeadmin <user_id>` — independently, no owner
   involvement.

Self-registered admins survive a restart if `UPSTASH_REDIS_REST_URL`/`_TOKEN`
are set (see [Persisting state across restarts](#persisting-state-across-restarts)
below) — without that, they're in-memory only, and an admin runs `/activate`
once in their group after a restart to re-establish it (same verified-admin
check, instant). `CHAT_ADMINS` is a third option that needs zero action from
anyone, restart or not, since it's read from env vars on every startup.

## Persisting state across restarts

Everything the bot tracks at runtime — alarm/lockdown state, self-claimed
chat admins, custom `/addkeyword` terms — lives in memory by default, which
Render's free tier wipes on every redeploy, cold-start, or periodic forced
restart. Set `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN` to
write this through to [Upstash](https://upstash.com) (free tier, REST API —
no persistent TCP connection to manage) so it survives:

1. Create a free database at upstash.com.
2. On the database's page, copy the REST URL and REST token.
3. Set both as env vars. Leave either empty to run fully in-memory as
   before — nothing else changes, the bot just re-loses this state on every
   restart like it always did.

State is stored as individual per-chat keys (`chat_state:{chat_id}`) rather
than a shared blob, so concurrent alarm activations across different chats
cannot race and overwrite each other.

What's deliberately **not** persisted: the short-window per-user violation
*counter* used for auto-mute (window is minutes, not worth the write
traffic), the per-user sliding text context window (30s, always in-memory),
and which chats the bot has recently seen a message in (`known_chats`,
rebuilt automatically the moment any message arrives).

The violation *audit log* (see
[Reviewing repeat offenders](#reviewing-repeat-offenders) below) is a
separate, durable, capped log — always Redis-backed when configured,
regardless of the short-window counter above.

## Reviewing repeat offenders

Every flagged message (text, photo, video, voice, live location, etc.) is
appended to a durable per-chat log — timestamp, user id/username, matched
reason, and the message text or caption (**never the photo/video itself** —
storing copies of exactly the content this bot exists to suppress would just
create a new leak vector if that log were ever compromised). Requires
`UPSTASH_REDIS_REST_URL` to actually persist; without it the log still
works but resets on every restart like everything else in-memory.

- **`/violations [user_id]`** (chat admin, DM'd to the caller only): the
  chat's recent log, or one user's if you pass an id.
- **`/mychats`** (owner-only, DM'd to the caller only): every chat this
  deployment is currently authorized in, with title and admins — a
  registration lost to a restart (self-service, no `UPSTASH_REDIS_REST_URL`
  configured) won't show up until re-`/activate`d.
- **`REPORT_VIOLATION_THRESHOLD`** (default 10): when a user's *all-time*
  logged-violation count in a chat crosses a multiple of this, admins get
  DM'd to go review `/violations <user_id>`. This is independent of
  `VIOLATION_THRESHOLD`'s short-window auto-mute — it's meant to surface a
  *pattern* across many separate alarms (someone who posts once per alert
  but does it at every single one) rather than a single burst.
- **Admin audit**: if a bot admin posts content that matches the filter
  (e.g. coordinates, strike keywords), they are exempt from auto-deletion
  but the chat's other admins receive a DM notification. Admin accounts
  are a higher-value social-engineering target, so silent exemption is not
  safe.
- By default the bot never escalates anything itself — this is an audit
  trail for a human to review and decide, e.g., whether to report a
  suspected spotter to the police. That decision, and any contact with
  authorities, is entirely on the admin, not the bot.
- **`AUTO_KICK_ON_REPORT_THRESHOLD`** (default off) opts into automating
  the last step: crossing a `REPORT_VIOLATION_THRESHOLD` multiple removes
  (not bans — they can rejoin via invite link) the user from that chat and
  DMs them why, instead of just notifying admins. This is a single global
  setting for every chat the deployment serves. Weigh it deliberately
  before enabling: the filter has real false-positive risk (see
  `bot/keywords.py`), and an automated removal from a community is a much
  bigger step than an admin notification alone.

## Reporting the bot's own downtime

Set `UPTIMEROBOT_API_KEY` (a **Read-Only** key — My Settings → API Settings
on UptimeRobot) and `UPTIMEROBOT_MONITOR_ID` (the numeric id in the
monitor's dashboard URL, e.g. `.../monitors/803663665`) to DM owners
whenever UptimeRobot's own logs show a new completed down period for this
bot's `/health` monitor.

This is necessarily retrospective — a process that isn't running can't run
code to say so — so it's a "you were down from X to Y" message once the
bot is back up and the next `UPTIMEROBOT_POLL_SECONDS` tick runs (default
10 min), never a live "still down" alert. It complements, rather than
replaces, actually keeping UptimeRobot pinging `/health` to prevent that
downtime in the first place (see "Keeping it awake" above).

## Tuning the filter

- Add keywords at runtime (chat admin): `/addkeyword слово` (strike-result
  tier) or `/addkeyword назва_району location` (location tier — only fires
  during alarm mode). **List the current keywords: `/listkeywords`, owner-only
  and DM'd only to whoever ran it** — never posted in a group or shown to
  chat admins. The filter works because its exact wordlist is hard to see;
  handing that out to every self-service chat admin (a much wider trust
  boundary than the deployer) would make it trivial to rephrase around.
  Survives a restart if `UPSTASH_REDIS_REST_URL` is set
  (see below); otherwise it's in-memory only and silently resets to whatever
  is hardcoded in `bot/keywords.py` on the next redeploy/restart, with no
  warning when that happens.
- Edit `bot/keywords.py` directly for a permanent change, then redeploy.
- The filter normalizes text before matching: NFKC normalization, Latin
  homoglyph → Cyrillic substitution (e.g. Latin `a` → Cyrillic `а`), zero-width
  and bidirectional control character stripping, Unicode tag block stripping
  (U+E0000–U+E007F), and emoji digit decoding. This means bypass attempts
  using lookalike letters or invisible spacers are caught by the same patterns
  as regular text.
- Coordinate detection covers decimal degrees (`50.45,30.52`), degrees-
  minutes-seconds (`50°27'N 30°31'E`), and Google Plus Codes (`8G7G+XH`).
- Map service URLs (Google Maps, Waze, OpenStreetMap, Apple Maps, Yandex Maps,
  2GIS, Maps.me) are detected and blocked unconditionally like coordinates.
- `PHOTO_BLUR_RADIUS` / `VIDEO_BLUR_STRENGTH` env vars control how heavy the
  blur is (higher = less recoverable detail).
- `MAX_VIDEO_MB` skips blurring (falls back to plain delete) above this size,
  to avoid timing out on Render's free CPU allocation.
- `VIOLATION_THRESHOLD` / `VIOLATION_WINDOW_SECONDS` control repeat-offender
  auto-muting sensitivity (default: mute after 3 hits in 10 minutes).
- `POST_ALARM_GRACE_SECONDS` (default 7200 = 2h) controls how long keyword
  filtering keeps applying after alarm mode turns off. Set to `0` for
  filtering to stop the instant alarm mode ends; coordinates and map URLs are
  unaffected either way — always blocked.
- `ALERTS_POLL_SECONDS` (default **15**) controls how often the alerts.in.ua
  API is polled. Lower values reduce the window between a real alert starting
  and the bot arming; the API is lightweight and 15 seconds is safe.
- `TRUSTED_BOT_IDS` (or runtime `/allowbot <bot_id>`) whitelists bot accounts
  that should be allowed to join without being auto-kicked.
