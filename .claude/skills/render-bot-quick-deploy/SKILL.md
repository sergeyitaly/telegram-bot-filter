---
name: render-bot-quick-deploy
description: Fast, scripted deployment of a brand-new Telegram bot (any purpose — not just moderation bots) from zero to a live Render free-tier service, with a dedicated Upstash Redis database and an UptimeRobot keepalive monitor wired up automatically. Use whenever the user wants to spin up a new bot project on Render quickly, asks to "deploy a new bot," mentions RENDER_API_KEY/UPSTASH_API_KEY/UPTIMEROBOT_API_KEY together, or wants to avoid manually clicking through the Render/Upstash/UptimeRobot dashboards for the Nth bot. Different from the deployment-practical skill, which gives general architecture/IaC advice — this skill is the concrete, repeatable "go" button for a new bot repo that already has a Dockerfile and a /health endpoint.
user-invocable: true
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
  - AskUserQuestion
---

# render-bot-quick-deploy

Stand up a new Telegram bot on Render's free tier in one pass: create the
web service, provision it a dedicated Upstash Redis database, wire in its
secrets, and register an UptimeRobot monitor so it doesn't sleep. The bot
itself can be about anything — this skill only handles the deployment
mechanics, not bot logic.

## Precondition: the target repo needs two things

Before running this, confirm the repo being deployed has:

1. **A `Dockerfile`** at the repo root (the script assumes Docker runtime).
2. **An HTTP `/health` endpoint** that returns 200 on a port read from the
   `PORT` env var — Render injects `PORT` and pings it to know the service
   is alive; UptimeRobot pings the same endpoint to keep the free tier from
   sleeping after ~15 min idle. This repo's own `bot/health.py` is a
   ~30-line reference implementation (aiohttp) if the new bot doesn't have
   one yet.

If either is missing, say so and offer to add a minimal one (point to
`bot/health.py` and this repo's `Dockerfile` as the pattern) before
attempting to deploy — don't try to deploy something that will fail
Render's health check or never stop sleeping.

## Step 1: Collect what's needed

Five kinds of secret/config, three required, three optional. Ask for
whichever aren't already in the environment — **use AskUserQuestion or a
plain chat prompt, never write them into a file that could get committed**
(check `.gitignore` covers `.env` in the target repo first if in doubt).

| Var | Required | Where to get it |
|---|---|---|
| `RENDER_API_KEY` | Yes | Render dashboard -> Account Settings -> API Keys |
| `BOT_TOKEN` | Yes | @BotFather on Telegram (`/newbot`) |
| `OWNER_IDS` | Yes | The user's own numeric Telegram id (e.g. via @userinfobot) |
| `CHAT_ADMINS` | No | Only if pre-registering specific chats; format `chat_id:uid,uid` |
| `UPTIMEROBOT_API_KEY` | No | UptimeRobot -> My Settings -> API Settings -> **Main** key (needed to create monitors, not the read-only key) |
| `UPSTASH_API_KEY` + `UPSTASH_EMAIL` | No | Upstash Console -> Account -> API Keys. Both needed (Basic auth as `email:api_key`). Skip both to deploy without Redis (bot runs in-memory only, loses state on restart) |

Also confirm: the **repo URL** to deploy (default: `git remote get-url
origin` in the target repo), the **branch** (default `main`), and a
**service name** (ask if not obvious — becomes the `*.onrender.com`
subdomain, so it needs to be DNS-safe).

## Step 2: Dry run, always, before asking to go live

Run `scripts/render_deploy.py` with the collected values as env vars — with
**no** `--yes` flag first. This is a pure dry run: it prints the exact plan
(masked secrets, the full request payloads it would send) and makes zero
API calls. Always do this before the live run, even if the user seems in a
hurry — creating a cloud resource and provisioning a database are the kind
of actions worth a beat to actually look at first.

```bash
RENDER_API_KEY=... BOT_TOKEN=... OWNER_IDS=... REPO_URL=... SERVICE_NAME=... \
  [CHAT_ADMINS=...] [UPTIMEROBOT_API_KEY=...] [UPSTASH_API_KEY=... UPSTASH_EMAIL=...] \
  python .claude/skills/render-bot-quick-deploy/scripts/render_deploy.py
```

Show the dry-run output to the user (or summarize it: service name, repo,
which of Upstash/UptimeRobot will be created) and get an explicit go-ahead.
This mirrors how this repo's own CLAUDE.md treats creating cloud resources
and pushing secrets: confirm first, since it's visible/costly/hard to
cleanly undo.

## Step 3: Live run

Same command with `--yes` appended. It will, in order:

1. `GET /services` on the Render account and copy `ownerId`/`plan` from an
   existing service, rather than guessing Render's current plan-naming
   scheme (see caveat below) — falls back to asking for `OWNER_ID_OVERRIDE`
   if the account has no existing services yet.
2. If Upstash credentials were given: `POST /redis/database` to create a
   dedicated free-tier database, and fold the resulting
   `UPSTASH_REDIS_REST_URL`/`UPSTASH_REDIS_REST_TOKEN` into the new
   service's env vars.
3. `POST /services` to create the Render web service (Docker runtime,
   `autoDeploy: yes`, all env vars set at creation time).
4. If an UptimeRobot key was given: `POST /newMonitor` pointed at
   `<service-url>/health`, 300s interval.

## Step 4: Smoke test

The first build takes a few minutes. Once it should be done, `curl` the
reported service URL's `/health` endpoint and confirm a 200. If it's not up
yet, say so plainly rather than guessing — Render's dashboard
(`https://dashboard.render.com`) shows live build logs if something failed.

## API caveats — verify, don't blindly trust

These were researched against current docs while building this skill, but
all three providers have changed their APIs before, and getting it wrong
here means either a failed deploy or (worse) a resource created with the
wrong settings:

- **Render's `plan` field naming.** As of writing, `POST /services` takes
  `serviceDetails.plan`, and Render's own docs are inconsistent about
  whether a literal `"free"` value still exists for web services (vs.
  `starter` as the new minimum paid tier) — Render restructured pricing
  around 2025. `render_deploy.py` sidesteps this by copying the `plan`
  value from an existing service on the account rather than hardcoding a
  guess. If the account has zero existing services, this will need
  confirming by hand in the Render dashboard first.
- **Upstash's create-database response shape.** The endpoint
  (`POST https://api.upstash.com/v2/redis/database`, Basic auth as
  `email:api_key`) and request fields (`database_name`, `platform`,
  `primary_region`) are confirmed against Upstash's docs. The response is
  expected to include `endpoint` and `rest_token` fields, but this wasn't
  100% pinned down during research — `render_deploy.py` checks for both and
  fails loud (prints the raw response, doesn't silently skip) if they're
  missing, so a docs drift here is visible rather than silently broken.
- **UptimeRobot's legacy v2 API.** `POST /newMonitor` with
  `api_key`/`format=json`/`type=1`/`url`/`friendly_name`/`interval` as form
  fields is confirmed and matches the pattern already used in this repo's
  own `bot/uptime_check.py`. UptimeRobot has a newer v3 API; v2 still works
  as of writing and is what this script uses for consistency with the rest
  of this codebase.

If any live call fails with a 4xx mentioning an unrecognized field or
value, that's this drift showing up — check `https://api-docs.render.com`,
`https://upstash.com/docs/devops/developer-api`, or
`https://uptimerobot.com/api/legacy/` respectively before retrying, rather
than guessing at a fix.

## Secrets handling

- Never print a full secret value back to the user or into a file —
  `render_deploy.py`'s `mask()` helper (first 4 / last 4 chars only) is
  used everywhere a secret would otherwise appear, including in dry-run
  payload previews.
- Pass secrets as env vars for the one script invocation, not as CLI
  arguments (which can leak via shell history / process listings) and never
  written into a script file or committed anywhere.
- If the user pastes a token directly into chat, that's their call, but
  don't echo it back in your own response — acknowledge receipt without
  repeating the value.
