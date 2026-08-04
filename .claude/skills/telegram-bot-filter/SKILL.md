---
name: telegram-bot-filter
description: Expert context for maintaining the Air-Alarm Content Filter Bot — a Ukrainian wartime Telegram moderation bot. Use when adding keywords, debugging filters, changing alarm behavior, modifying admin commands, working with Redis state, reviewing security, or deploying to Render. Provides architecture shortcuts, common task recipes, and security rules specific to this codebase.
user-invocable: true
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
  - Edit
  - Write
---

# telegram-bot-filter Skill

This is a wartime safety application. Mistakes can have real consequences. Read the rules before acting.

## Non-negotiable rules

1. **Never log or expose `bot/keywords.py` content publicly** — the wordlist is what makes the filter hard to evade; leaking it to non-owners defeats it.
2. **Never relax filtering logic without an explicit security review** — widening the regex, removing a check, or disabling a handler weakens protection during live alerts.
3. **Always read `_admins_for(chat_id)`** for admin checks — never check `OWNER_IDS` or `CHAT_ADMINS` directly in handlers.
4. **Delete-before-repost** — all media handlers must delete the original BEFORE processing (blur/repost). A crash mid-pipeline must leave dangerous content gone, not exposed.
5. **Test normalization both ways** — any filter change must be tested with native Cyrillic AND Latin homoglyph substitutes.

---

## Architecture quick reference

| Question | Answer |
|---|---|
| Where are keywords? | `bot/keywords.py` — `STRIKE_TERMS`, `LOCATION_TERMS`, `COORDINATE_RE` |
| How are they matched? | `bot/filters.py` — `classify_text()`, `classify_media()`, after `_normalize()` |
| Where are handlers registered? | `main.py` — `build_application()` |
| How does alarm mode arm/disarm? | `handlers.activate_alarm()` / `deactivate_alarm()` — also called by `air_alert.poll()` |
| How is Redis used? | `bot/store.py` — `get_json` / `set_json`; per-chat key `chat_state:{id}` |
| Who counts as admin? | `handlers._admins_for(chat_id)` — merges CHAT_ADMINS + claimed_admins + OWNER_IDS |
| How does auto-alarm work? | `bot/air_alert.py` polls alerts.in.ua every ALERTS_POLL_SECONDS |
| How do violations work? | `handlers._track_violation()` → `state.log_violation()` + `state.record_violation()` |
| What message types are handled? | PHOTO, VIDEO, VIDEO_NOTE, Document.ALL, TEXT, LOCATION, ANIMATION + catch-all |
| What types are NOT handled? | VOICE (deleted in alarm), Venue (→ on_location), Poll (deleted in alarm) |

---

## Common tasks

### Add a permanent keyword

Edit `bot/keywords.py`. Choose the right list:
- `STRIKE_TERMS` — implies strike result; checked in strict mode (alarm + 2h grace)
- `LOCATION_TERMS` — address/landmark terms; checked in strict mode only
- Coordinates — always blocked; `COORDINATE_RE` handles decimal, `_DMS_RE` handles DMS, `_PLUS_CODE_RE` handles Plus Codes

Use raw regex patterns, `re.IGNORECASE` is applied. Stem the word where it has inflection variants (`r"влучанн"` matches влучання, влучань, etc.).

After editing, redeploy. Runtime `/addkeyword <term>` also works (persists to Redis).

### Add a new bot command

1. Write `async def cmd_<name>(update: Update, context: ContextTypes.DEFAULT_TYPE)` in `bot/handlers.py`
2. Add an admin guard at the top: `if not _is_admin(chat_id, update.effective_user.id): return`
3. Register in `main.py`: `app.add_handler(CommandHandler("name", handlers.cmd_<name>))`
4. Add to README.md commands table

### Add a new message type handler

1. Write the handler in `bot/handlers.py` following the delete-before-repost pattern
2. Register in `main.py` (below existing handlers; also add an EDITED_MESSAGE version)
3. Also register in group 1 catch-all if the type was not previously handled at all

### Debug Redis state (local)

```bash
# Set env vars from .env first, then:
curl "$UPSTASH_REDIS_REST_URL/get/claimed_admins" \
  -H "Authorization: Bearer $UPSTASH_REDIS_REST_TOKEN"

curl "$UPSTASH_REDIS_REST_URL/get/chat_state:-1001234567890" \
  -H "Authorization: Bearer $UPSTASH_REDIS_REST_TOKEN"
```

Or use the `telegram-bot-api` MCP: `tg_get_chat chat_id="-1001234567890"` to check live chat permissions.

### Test keyword detection (no bot token needed)

```python
from bot.filters import classify_text, _normalize
from bot.keywords import hydrate  # no-op without Redis, uses hardcoded lists

# Test normalization
print(_normalize("бaвовна"))  # Latin 'a' → should normalize to Cyrillic 'а'

# Test classification
v = classify_text("бавовна в центрі міста", strict=True)
print(v.flagged, v.reason)  # True, "strike-result keyword"

# Test coordinate detection
v = classify_text("50.45, 30.52", strict=False)
print(v.flagged, v.reason)  # True, "coordinates shared"
```

### Deploy to Render

1. Push to `main` — Render auto-builds from `Dockerfile`
2. Required env vars: `BOT_TOKEN`, `OWNER_IDS`
3. Recommended: `UPSTASH_REDIS_REST_URL` + `_TOKEN` (state survives redeploys)
4. For auto-alarm: `ALERTS_API_TOKEN` + `ALERTS_OBLAST_UID`
5. Set UptimeRobot to ping `/health` every 5 min (prevents Render free-tier sleep)

---

## Security checklist for PRs touching filter logic

- [ ] Normalization tested with homoglyph variants?
- [ ] Regex tested for both Ukrainian and Russian variants of the term?
- [ ] New handler follows delete-before-repost order?
- [ ] Admin exemption NOT applied to audit logging?
- [ ] New message type has a corresponding EDITED_MESSAGE handler?
- [ ] Coordinate patterns cover decimal, DMS, and Plus Code formats?
- [ ] URL/link messages handled during strict mode?
- [ ] No keyword list exposure in logs, DMs to non-owners, or API responses?

---

## Known attack surfaces (from red-team audit 2026-08-05)

| Attack | Status |
|---|---|
| Animation/GIF type missing | FIXED — catch-all handler in group 1 |
| Poll/Venue/Sticker/Animation missing | FIXED — Animation→on_video, Voice→on_voice, POLL/Sticker→catch-all; `filters.LOCATION` catches Venue too |
| Map URL bypass | FIXED — `_MAP_URL_RE` in `keywords.py`, checked unconditionally in `classify_text` |
| DMS / Plus Code coordinate formats | FIXED — `_DMS_RE` and `_PLUS_CODE_RE` in `keywords.py` |
| Generic URL link-preview bypass | FIXED — `_URL_RE` blocks https:// during active alarm only |
| Voice messages unfiltered | FIXED — deleted during alarm + violation logged; admin-notified during grace |
| Split coordinates across messages | FIXED — 30s/5-msg sliding context window via `state.get_user_context()` |
| Unicode math/tag block/emoji-digit bypass | FIXED — extended `_normalize()` |
| 60-second alarm gap | FIXED — `ALERTS_POLL_SECONDS` default lowered to 15 |
| Admin silent exemption | FIXED — admin-posted flagged content logged + other admins notified |
| Telegram Stories | NOT FIXABLE — Bot API limitation |
| Userbot scrapers | PARTIAL — join restrictions during alarm; no API solution |

**filters.Venue.ALL does not exist in PTB 21.x.** Venue messages set `message.location`, so `filters.LOCATION` already catches them. Never use `filters.Venue.ALL`.

---

## Custom slash commands

Invoke these in any Claude Code session inside this repo:

| Command | Purpose |
|---|---|
| `/security-audit` | Red-team audit of recent changes; updates attack surface table |
| `/filter-test` | Run battery of classify_text / normalize / coordinate tests |
| `/update-docs` | Sync CLAUDE.md, SKILL.md, and README after significant changes |

---

## Keeping this skill current

**After every significant PR or set of changes, run `/update-docs` to sync agent files and push.**

This skill file, CLAUDE.md, and README are loaded at session start. Stale context causes future sessions to make wrong assumptions. The rule:

1. Make code changes
2. Run `/update-docs` (or manually edit this file + CLAUDE.md + README)
3. `git add .claude/skills/telegram-bot-filter/SKILL.md CLAUDE.md README.md`
4. Commit + push

Things that MUST trigger a skill update:
- New message type handled (add to architecture table + attack surface table)
- New PTB filter gotcha discovered (add a "never do X" note like the Venue one above)
- New command added (update architecture quick reference)
- New env var added (update CLAUDE.md env vars table)
- New attack vector found or fixed (update attack surface table)
- New custom slash command added (update commands table above)

---

## Testing the Telegram MCP

With `BOT_TOKEN` set in environment, use the MCP tools:

```
tg_get_me                          — verify bot is online
tg_get_chat chat_id="-100..."      — check chat permissions (lockdown applied?)
tg_get_chat_administrators ...     — compare Telegram admins vs bot's claimed_admins
tg_get_chat_member ... user_id=... — check a member's restriction status
```
