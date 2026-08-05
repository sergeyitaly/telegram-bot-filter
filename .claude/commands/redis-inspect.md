Inspect this bot's persisted Redis state (Upstash) — a faster path than the
manual `curl` commands in `CLAUDE.md`'s "Inspect Redis state directly"
section, when the `upstash` MCP is available.

## 1. Confirm the `upstash` MCP is connected

Check the available tools list for anything namespaced like
`mcp__upstash__*`. If nothing is there, fall back to the manual `curl`
approach already documented in `CLAUDE.md` (needs `UPSTASH_REDIS_REST_URL`
and `UPSTASH_REDIS_REST_TOKEN` — the per-database runtime credentials, NOT
`UPSTASH_API_KEY`/`UPSTASH_EMAIL`, which are account-level and what this
MCP server itself authenticates with). Tell the user to restart the
session if they expected the MCP to be connected — it only loads at
startup.

## 2. Find the right database

This bot's database name matches whatever was used when it (or the target
bot, if inspecting one deployed via `render-bot-quick-deploy`) was set up.
List databases via the MCP if unsure which one, rather than guessing.

## 3. Keys worth checking (this bot's actual Redis schema)

Read-only unless the user explicitly asks to modify something:

| Key | Contents | Set by |
|---|---|---|
| `claimed_admins` | `{chat_id: [user_id, ...]}` — self-registered admins | `bot/state.py` |
| `chat_state:{chat_id}` | Per-chat alarm/lockdown state (`alarm_active`, `saved_permissions`, `alarm_ended_at`) | `bot/state.py` |
| `chat_states` | Legacy shared blob, read-only migration path — should be empty/stale on a healthy deployment past its first migration | `bot/state.py` |
| `violations_log:{chat_id}` | Durable audit log of flagged messages (text only, never media) | `bot/state.py` |
| `custom_keywords` | `{"strike": [...], "location": [...]}` — runtime `/addkeyword` additions | `bot/keywords.py` |

## 4. Report

Summarize what's found in plain terms (e.g. "chat -1001234 is currently
locked down, alarm armed 40 minutes ago" rather than dumping raw JSON
unprompted). If a `chat_state:{id}` shows `alarm_active: false` but
`saved_permissions` is non-null, or vice versa, flag it explicitly — that
combination indicates a stuck lockdown (see the `activate_alarm`/
`deactivate_alarm` invariant in `bot/handlers.py` and its regression tests
in `tests/test_alarm_lockdown.py`).
