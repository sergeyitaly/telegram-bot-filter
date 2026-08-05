<!-- claude-skills-manager:installed-skills -->
## Installed Claude Skills

Claude Code discovers and loads skills under `.claude/skills/` automatically — nothing here needs to be read for that to work. This table is kept up to date purely as a human-readable summary of what's installed and why.

| Skill | Detected via | Description |
|---|---|---|
| deployment-practical | `**/*.tf, **/*.bicep, **/azure.yaml, **/azure.yml, **/Dockerfile, **/Dockerfile.*, **/docker-compose*.yml, **/.gitlab-ci.yml, **/azure-pipelines.yml, **/.env*, **/deployment/**` | Deployment-first delivery — concrete architecture and IaC over theoretical advice. Use when deploying, provisioning infra, debugging first-apply failures, or when the user wants advice that works on the first attempt (not hand-wavy theory). Pair with Practical Focus toggle (architecture-first / deploy-ready). |
| file-style-conventions | `**/*` | Apply two lightweight file-hygiene conventions when writing or editing files - no emoji characters outside Markdown (.md) files, and YAML files (.yml/.yaml) end with exactly one trailing newline. Use whenever creating or editing non-Markdown files that might contain emoji, or any .yml/.yaml file. |
| github-actions-ci | `**/.github/workflows/*.yml, **/.github/workflows/*.yaml, **/.github/**/*.md, **/test/**, **/src/**, **/*.test.ts, **/*.test.js` | Debug GitHub Actions pipeline failures and reproduce CI stages locally. Use when asked to debug CI, fix a failing workflow, reproduce a job with act, or run a pre-flight check before pushing. |
| render-bot-quick-deploy | `**/*` | Fast, scripted deployment of a brand-new Telegram bot (any purpose — not just moderation bots) from zero to a live Render free-tier service, with a dedicated Upstash Redis database and an UptimeRobot keepalive monitor wired up automatically. Use whenever the user wants to spin up a new bot project on Render quickly, asks to "deploy a new bot," mentions RENDER_API_KEY/UPSTASH_API_KEY/UPTIMEROBOT_API_KEY together, or wants to avoid manually clicking through the Render/Upstash/UptimeRobot dashboards for the Nth bot. Different from the deployment-practical skill, which gives general architecture/IaC advice — this skill is the concrete, repeatable "go" button for a new bot repo that already has a Dockerfile and a /health endpoint. |
| self-learning | `**/*` | Maintain a project-local self-learning base of task/command outcomes — record successes and failures with timestamps, durations, and fixes; generate a patterns report (pass rates, recurring errors, known fixes); and surface a learned hint before retrying something that failed before. Use at the start of a session to check learned hints, after running a non-trivial command/skill to record the outcome, when asked "what failed before" or "what did we learn", or to record a manual decision/learning. |
| skill-creator | `**/*` | Create new skills, modify and improve existing skills, and measure skill performance. Use when users want to create a skill from scratch, edit, or optimize an existing skill, run evals to test a skill, benchmark skill performance with variance analysis, or optimize a skill's description for better triggering accuracy. |
| skill-feedback-adaptation | `**/.claude/learning/skill-feedback.jsonl, **/.claude/learning/task-skill-proposals.json, **/.claude/learning/**` | Register user disagreement and negative reactions on agent answers or skill behavior into .claude/learning/skill-feedback.jsonl; on new tasks analyze the prompt and repo to write task-skill-proposals.json from the existing skill library. Use when the user says no, not, wrong, stop, or otherwise disagrees with agent output; when starting a new task or feature; or when asked about skill inefficiency, feedback adaptation, or which skills fit this task. |
| skill-official-updater | `**/*` | At the start of a new session, do a cheap check for new or updated official Anthropic skills (github.com/anthropics/skills) and offer to add or update them in skills_library/. Also use on explicit request ("check for official skill updates", "sync official skills"). |
| skill-usage-insights | `**/.claude/learning/runs.jsonl, **/.claude/skills/**` | Analyze recorded skill usage in this project (.claude/learning/runs.jsonl, written by self-learning) and the skills installed in .claude/skills/ to produce a usage and KPI report - which skills are actively used and reliable, which are failing, and which are unused or low-value, with recommendations on what to add or remove. Use when asked for "skill usage stats", "skill KPIs", "which skills should we add or remove", or "are our installed skills still useful". |
| telegram-bot-filter | `**/*` | Expert context for maintaining the Air-Alarm Content Filter Bot — a Ukrainian wartime Telegram moderation bot. Use when adding keywords, debugging filters, changing alarm behavior, modifying admin commands, working with Redis state, reviewing security, or deploying to Render. Provides architecture shortcuts, common task recipes, and security rules specific to this codebase. |

<!-- /claude-skills-manager:installed-skills -->

---

## Project: Air-Alarm Content Filter Bot

Ukrainian wartime Telegram moderation bot. Removes content (coordinates, strike-result photos/videos, address chatter) posted during/after drone or missile strikes that could help an attacker confirm hits or correct aim. Deployed on Render via Docker, persists state to Upstash Redis.

### Repository

`https://github.com/sergeyitaly/telegram-bot-filter` — branch `main` is production.

---

## File map

| File | Role |
|---|---|
| `main.py` | Entry point; registers all handlers and job queues, starts long-polling |
| `bot/config.py` | All env-var parsing — single source of truth for configuration |
| `bot/keywords.py` | Strike/location keyword lists + coordinate regex; supports runtime extension via `/addkeyword` |
| `bot/filters.py` | Stateless classification: `classify_text` / `classify_media`; Unicode normalization; optional OCR |
| `bot/handlers.py` | All Telegram update handlers, alarm lifecycle, violation tracking, admin commands |
| `bot/state.py` | Per-chat alarm state + per-user violation tracking + claimed admins; Redis write-through |
| `bot/store.py` | Thin Upstash Redis REST client; no-op when `UPSTASH_*` vars not set |
| `bot/media.py` | Pillow photo blur + ffmpeg async video blur |
| `bot/air_alert.py` | Polls alerts.in.ua and auto-arms/disarms alarm mode each tick |
| `bot/health.py` | Minimal aiohttp HTTP server on `$PORT` — Render health ping and uptime monitor target |
| `bot/uptime_check.py` | Polls UptimeRobot and DMs owners about completed downtime periods |

---

## Key design decisions (read before making changes)

- **Permission lockdown wins over reactive deletion.** During alarm, `can_send_photos/videos/etc=False` is set chat-wide so members can't send media at all — nothing to scrape. Reactive deletion still handles text.
- **Two filtering modes**: `alarm_active=True` → blur ALL media regardless of content. `strict=True` (alarm OR post-alarm grace window) → keyword-filter text/captions. Coordinates are unconditional — always blocked.
- **Delete-before-repost**: `on_photo`, `on_video`, `_blur_flagged_document` all delete the original BEFORE the blur step so a mid-process crash never leaves dangerous content visible.
- **Per-chat Redis keys** (`chat_state:{id}`): avoids read-modify-write race under concurrent alarm updates. Old `chat_states` shared key is read-only migration path on hydration.
- **`_admins_for(chat_id)`** is the single authority for who counts as admin — merges `CHAT_ADMINS` (env), `claimed_admins` (Redis), and `OWNER_IDS` (global). Always call this before privileged operations.
- **`strict` vs `alarm_active`**: `classify_media` takes both; `alarm_active` alone triggers blanket blur. `strict` gates keyword filtering. `classify_text` only takes `strict`.
- **OCR is optional**: `classify.ocr_image(path)` returns `""` if `pytesseract` is not installed — graceful no-op. Add `pytesseract` + tesseract binary to `requirements.txt` / `Dockerfile` to activate.
- **Edited messages** are fully re-scanned: explicit `EDITED_MESSAGE` handlers are registered in `main.py` for text, photo, video, and document.

---

## Running locally

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# Install ffmpeg: choco install ffmpeg (Windows) / apt install ffmpeg (Linux)
cp .env.example .env             # fill in BOT_TOKEN + OWNER_IDS
export $(cat .env | xargs)       # load env vars (or use direnv)
python main.py
```

Test the health endpoint:
```bash
curl http://localhost:8080/health
```

Build and smoke-test the Docker image:
```bash
docker build -t telegram-bot-filter .
docker run --env-file .env telegram-bot-filter
```

Run the test suite:
```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

One-time setup to run tests automatically before every `git push` (this repo
has had two real production crashes that the test suite would have caught):
```bash
git config core.hooksPath .githooks
```
Bypass for the rare exception with `git push --no-verify`.

---

## Common development tasks

### Add a new keyword (permanent)
Edit `STRIKE_TERMS` or `LOCATION_TERMS` in `bot/keywords.py`, then redeploy. Runtime-added keywords via `/addkeyword` persist only if `UPSTASH_REDIS_REST_URL` is configured.

### Add a new bot command
1. Write `async def cmd_<name>(update, context)` in `bot/handlers.py`
2. Register with `CommandHandler("name", handlers.cmd_<name>)` in `main.py`
3. Add it to the commands table in `README.md`

### Inspect Redis state directly
```bash
# Claimed admins:
curl "$UPSTASH_REDIS_REST_URL/get/claimed_admins" -H "Authorization: Bearer $UPSTASH_REDIS_REST_TOKEN"
# A specific chat's alarm state:
curl "$UPSTASH_REDIS_REST_URL/get/chat_state:-1001234567890" -H "Authorization: Bearer $UPSTASH_REDIS_REST_TOKEN"
# Violation audit log for a chat:
curl "$UPSTASH_REDIS_REST_URL/get/violations_log:-1001234567890" -H "Authorization: Bearer $UPSTASH_REDIS_REST_TOKEN"
```

### Run linting
```bash
pip install ruff
ruff check . --select E,W,F --ignore E501
```

---

## Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `BOT_TOKEN` | Yes | — | From @BotFather |
| `OWNER_IDS` | Yes | — | Comma-sep numeric Telegram user IDs; admin everywhere |
| `CHAT_ADMINS` | No | `""` | Pre-register chats: `-1001...:uid,uid; -1002...:uid` |
| `UPSTASH_REDIS_REST_URL` | No | `""` | State persistence across restarts |
| `UPSTASH_REDIS_REST_TOKEN` | No | `""` | Redis auth token |
| `ALERTS_API_TOKEN` | No | `""` | alerts.in.ua auto-alarm token |
| `ALERTS_OBLAST_UID` | No | `""` | Oblast UID(s) for auto-alarm, comma-separated |
| `ALERTS_POLL_SECONDS` | No | `60` | How often to poll alerts.in.ua |
| `MAX_VIDEO_MB` | No | `20` | Skip blur above this size (CPU timeout guard) |
| `PHOTO_BLUR_RADIUS` | No | `35` | Pillow GaussianBlur radius |
| `VIDEO_BLUR_STRENGTH` | No | `30` | ffmpeg boxblur strength |
| `VIOLATION_THRESHOLD` | No | `3` | Hits before auto-mute |
| `VIOLATION_WINDOW_SECONDS` | No | `600` | Window for auto-mute counter |
| `REPORT_VIOLATION_THRESHOLD` | No | `10` | All-time count before admin DM |
| `AUTO_KICK_ON_REPORT_THRESHOLD` | No | `false` | Auto-remove on threshold (vs just notify) |
| `POST_ALARM_GRACE_SECONDS` | No | `7200` | Keyword filter duration after alarm-off |
| `HEALTH_MONITOR_POLL_SECONDS` | No | `120` | Redis/backlog self-check interval; DMs admins if unhealthy |
| `PORT` | No | `8080` | HTTP health server port (Render injects this) |

---

## Deployment (Render + Docker)

1. Push to `main` on GitHub.
2. Render auto-deploys from the `Dockerfile` (connected repo, Docker environment).
3. Required env vars in Render dashboard: `BOT_TOKEN`, `OWNER_IDS`. Leave `PORT` unset — Render injects it.
4. Recommended: configure Upstash Redis (`UPSTASH_REDIS_REST_URL` + `_TOKEN`) so state survives redeploys.
5. Set up UptimeRobot to ping `https://<your-app>.onrender.com/health` every 5 min (prevents free-tier sleep after 15 min idle).
6. Optionally configure auto-alarm: `ALERTS_API_TOKEN` + `ALERTS_OBLAST_UID`.

---

## Security model

- **`bot/keywords.py`**: keyword list is secret from non-owner admins — never log or expose via `/listkeywords` in group context.
- **`bot/state.py → log_violation`**: stores text only, never media — audit log cannot become a secondary leak vector.
- **`bot/filters.py → _normalize`**: defends against homoglyph bypass (Latin lookalike letters, zero-width chars). Test any filter change with both Cyrillic originals and Latin substitutes.
- **`bot/handlers.py → _admins_for`**: single trust boundary for admin checks — always use this, never check `OWNER_IDS` or `CHAT_ADMINS` directly in handlers.
- **`bot/handlers.py → on_my_chat_member_update`**: verifies via the Telegram API that whoever added the bot is an actual admin/creator of that chat before activating — the API check, not the user's say-so.

---

## MCP servers

Defined in `.mcp.json` (project root), auto-approved via `enableAllProjectMcpServers` in `.claude/settings.json`.

| Server | Purpose |
|---|---|
| `telegram-bot-api` | Live Bot API calls — `tg_get_me`, `tg_get_chat`, `tg_get_chat_administrators`, `tg_get_chat_member`, `tg_send_message`, `tg_get_updates`. Needs `BOT_TOKEN`. Custom zero-dependency Node.js server in `.claude/mcp-servers/telegram-bot-api.mjs`. |
| `fetch` | General HTTP via `@modelcontextprotocol/server-fetch` — use to call the GitHub REST API, Upstash Redis REST API, or alerts.in.ua directly when debugging. |
| `render` | Render's official hosted MCP server (`https://mcp.render.com/mcp`), bridged to stdio via `mcp-remote` — manage/inspect Render services, deploys, and logs. Needs `RENDER_API_KEY`. Backs the `render-bot-quick-deploy` skill. |
| `upstash` | Official `@upstash/mcp-server` — manage Upstash Redis databases (create/list/query), QStash, and Workflow at the account level. Needs `UPSTASH_EMAIL` + `UPSTASH_API_KEY` (Upstash Console → Account → API Keys) — distinct from the per-database `UPSTASH_REDIS_REST_URL`/`_TOKEN` the bot itself uses at runtime. |
| `uptimerobot` | UptimeRobot's official hosted MCP server (`https://mcp.uptimerobot.com/mcp`), bridged to stdio via `mcp-remote` — manage monitors, alert contacts, maintenance windows. Needs `UPTIMEROBOT_API_KEY` — the **Main** key, not the read-only one `bot/uptime_check.py` uses; MCP write operations reject a read-only key. |

**`.env` fallback**: `render`/`upstash`/`uptimerobot`/`telegram-bot-api` each load `.claude/mcp-servers/dotenv-util.mjs` at startup, which fills in any of the above vars *not already set in the shell* from the repo's `.env` (gitignored) before launching. Shell-exported values always win; `.env` is just a fallback so these don't need re-exporting every session. `fetch` needs no credentials.

### Telegram MCP usage examples

```
# Verify bot is running:
tg_get_me

# Check current chat permissions (verify lockdown is applied):
tg_get_chat chat_id="-1001234567890"

# See who Telegram considers an admin:
tg_get_chat_administrators chat_id="-1001234567890"

# Check a specific member's status:
tg_get_chat_member chat_id="-1001234567890" user_id=123456789
```

Note: each server's required env var(s) need to resolve *before Claude Code starts* — either exported in the shell, or present in the repo's `.env` (see the `.env` fallback note above). A server whose var is missing at startup just won't connect; everything else keeps working. Restarting the session picks up newly-added `.mcp.json` entries or `.env` changes; neither hot-reloads mid-session.
