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
| How is bot-wide health checked? | `bot/health_monitor.py` — polls Redis health, PTB update-queue depth, and aggregate rate-limit trips every `HEALTH_MONITOR_POLL_SECONDS`; DMs via `handlers.notify_all_admins()` (owners + every chat's admins, not just one chat) |
| How is logging formatted? | `bot/logging_utils.py` — JSON lines to stdout; pass `extra={"chat_id": ..., ...}` on a log call to add structured fields |
| How are unhandled exceptions tracked? | `handlers.on_error()`, registered via `app.add_error_handler()` in `main.py` — logs with chat_id/user_id context; becomes a Sentry event automatically if `SENTRY_DSN` is set (opt-in, see CLAUDE.md env vars) |
| Where are the tests? | `tests/` (pytest) — see "Testing" section below |
| Why is it shaped this way? | [docs/adr/](../../../docs/adr/) — Architecture Decision Records. [README's diagram](../../../README.md#architecture) for the data-flow picture. |

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

Fastest path: `/redis-inspect` (uses the `upstash` MCP if connected). Manual
fallback:

```bash
# Set env vars from .env first, then:
curl "$UPSTASH_REDIS_REST_URL/get/claimed_admins" \
  -H "Authorization: Bearer $UPSTASH_REDIS_REST_TOKEN"

curl "$UPSTASH_REDIS_REST_URL/get/chat_state:-1001234567890" \
  -H "Authorization: Bearer $UPSTASH_REDIS_REST_TOKEN"
```

Or use the `telegram-bot-api` MCP: `tg_get_chat chat_id="-1001234567890"` to check live chat permissions.

### Run the test suite

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

Or `/deploy-check` for the fuller pre-push ritual (compile-check + lint +
tests + `build_application()` startup regression test). `.githooks/pre-push`
runs pytest and ruff automatically before every push once enabled via
`git config core.hooksPath .githooks` — don't bypass it with `--no-verify`
without a real reason; this repo has had two production crashes the suite
would have caught, and CI's `ruff check .` has independently caught a lint
failure (`F401` unused import) that neither the hook nor `/deploy-check`
were checking for at the time — both do now.

CI (`.github/workflows/ci.yml`) runs `python -m py_compile bot/*.py main.py`
(a glob, not a hardcoded file list — the hardcoded version silently stopped
checking new files twice: `health_monitor.py` and `logging_utils.py` were
both added over several commits before anyone noticed neither was in the
list), the test suite, and `ruff check .` from repo root. `.cursor/`/`.kiro/`
are gitignored specifically so a stray `git add -A` can't pull vendored
skill copies into a commit and break that last check.

When adding a test, follow the pattern in `tests/test_alarm_lockdown.py` or
`tests/test_health_monitor.py`: a small `Fake*` stand-in for `context.bot`/
`context.application` (no real Telegram/network calls), state cleared via an
`autouse` fixture between tests, assertions on the actual object mutated
(e.g. `bot._permissions`) rather than just "did it raise."

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

0. Run `/deploy-check` first — don't push on the strength of "looks right"
1. Push to `main` — Render auto-builds from `Dockerfile`; `.githooks/pre-push`
   runs the test suite as a final gate if enabled
2. Required env vars: `BOT_TOKEN`, `OWNER_IDS`
3. Recommended: `UPSTASH_REDIS_REST_URL` + `_TOKEN` (state survives redeploys),
   `SENTRY_DSN` (unhandled exceptions become alerts, not just log lines)
4. For auto-alarm: `ALERTS_API_TOKEN` + `ALERTS_OBLAST_UID`
5. Set UptimeRobot to ping `/health` every 5 min (prevents Render free-tier sleep)

Deploying a *different* bot (not this one) from zero on Render — new
service, dedicated Upstash database, UptimeRobot monitor, all via API —
use the `render-bot-quick-deploy` skill instead, not this checklist.

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

## Known operational bugs (fixed) — not attacks, just real incidents

These broke the bot's own moderation mechanics for legitimate admins, no
adversary required. Distinct from the attack-surface table above (that's
about content bypassing the filter; this is about the filter itself
malfunctioning).

| Bug | Status |
|---|---|
| `activate_alarm` re-captured "current" permissions on every call, including redundant ones — a double `/alarm_on` (or a race with the auto-poller) could capture the already-locked state as the "original," so `/alarm_off` restored the chat right back to locked, permanently | FIXED — capture gated on `saved_permissions is None`, not call order; see `tests/test_alarm_lockdown.py`. Found via live testing against the real bot/chat, not code review — the test group was actually stuck locked. |
| `deactivate_alarm` cleared `saved_permissions` even when the restore API call failed (`BadRequest`), losing the true original the same way | FIXED — only clears on a successful restore; `tests/test_alarm_lockdown.py::test_failed_restore_keeps_saved_permissions_for_retry` |
| `ChatPermissions.__init__()` rejected `can_send_media_messages` (a legacy field present in `ChatPermissions.to_dict()` output) when reconstructing `saved_permissions` from Redis on startup — crashed the whole process on every deploy while any chat had a saved lockdown | FIXED — `bot/state.py::ChatState.from_json` filters to `inspect.signature(ChatPermissions).parameters` before unpacking |
| `asyncio.run(_hydrate())` before `app.run_polling()` closed the event loop PTB needed, crashing startup | FIXED — hydration moved into PTB's own `post_init` hook (`main.py::_hydrate`), which runs inside the loop `run_polling` manages |
| `tests/` passed locally under every invocation used during development (`python -m pytest`, which adds cwd to `sys.path`) but failed CI's `pytest tests/ -v` (bare, no `-m`) with `ModuleNotFoundError: No module named 'bot'` — the two invocation styles resolve imports differently and nobody had run the bare form locally before it shipped | FIXED — `pytest.ini` sets `pythonpath = .`, making both forms equivalent. Reproduce this class of bug by running the exact bare form CI/docs use, not just whatever happens to already work. |

If you touch `activate_alarm`/`deactivate_alarm`/`ChatState.from_json`/the
`post_init` wiring, run `tests/test_alarm_lockdown.py` specifically — this
exact class of bug has bitten this codebase twice already.

---

## Custom slash commands

Invoke these in any Claude Code session inside this repo:

| Command | Purpose |
|---|---|
| `/security-audit` | Red-team audit of recent changes; updates attack surface table |
| `/filter-test` | Run battery of classify_text / normalize / coordinate tests |
| `/update-docs` | Sync CLAUDE.md, SKILL.md, and README after significant changes |
| `/deploy-check` | Pre-push smoke test: compile-check, full test suite, build_application() startup regression test |
| `/render-status` | Live deploy status + recent logs via the `render` MCP |
| `/redis-inspect` | Browse persisted Redis state (alarm/lockdown, admins, violations) via the `upstash` MCP |

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
- New operational bug found/fixed, especially via live testing rather than
  code review (update "Known operational bugs" table)
- New test file added (mention it in the "Run the test suite" section)
- New MCP server added (update CLAUDE.md's MCP servers table; note here too
  if it backs a specific workflow, like `render`/`upstash` do for deploy)

Concretely observed failure mode: updating CLAUDE.md every time (it's what
gets read back into the *next* agent turn) while treating this file and
README as optional, "update when it feels big enough." That's how this file
went stale for an entire session's worth of changes (health_monitor.py,
logging_utils.py, Sentry, the tests/ directory, two real production bugs)
despite CLAUDE.md staying current the whole time. Check all three, every
time — not just the one that happens to be in context already.

---

## Testing the Telegram MCP

With `BOT_TOKEN` set in environment, use the MCP tools:

```
tg_get_me                          — verify bot is online
tg_get_chat chat_id="-100..."      — check chat permissions (lockdown applied?)
tg_get_chat_administrators ...     — compare Telegram admins vs bot's claimed_admins
tg_get_chat_member ... user_id=... — check a member's restriction status
```
