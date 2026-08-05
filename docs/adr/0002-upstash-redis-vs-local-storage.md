# ADR-0002: Upstash Redis (REST) instead of local/disk storage

## Status

Accepted

## Context

The bot needs a handful of things to survive a restart: alarm/lockdown
state per chat, self-claimed chat admins, and runtime-added `/addkeyword`
terms. Render's free tier has an ephemeral filesystem — anything written
to disk (SQLite, a JSON file, whatever) is gone on the next redeploy, cold
start after sleep, or periodic forced restart. A paid Render persistent
disk would solve that, but it's a paid feature on a project explicitly
built to run on the free tier.

A traditional Redis client needs a persistent TCP connection (and,
typically, connection pooling) that the rest of this codebase's networking
doesn't otherwise need — every other external call (alerts.in.ua,
UptimeRobot, Telegram's own Bot API client) is a plain async HTTP request.

## Decision

Use Upstash Redis via its REST API (`bot/store.py`: plain `httpx` `GET`/
`POST` calls, no Redis client library), free tier.

## Consequences

- **No new networking pattern.** State persistence fits the same
  request/response shape as everything else the bot already does over
  HTTP, using the same pooled `httpx.AsyncClient` (see `bot/store.py`).
  No connection-pool lifecycle to manage, no separate client library.
- **Free tier is generous enough for this bot's actual write volume**
  (state changes on alarm events and admin actions, not per-message).
- **Everything is opt-in and gracefully degrades.** `store.ENABLED` is
  `False` when `UPSTASH_REDIS_REST_URL`/`_TOKEN` aren't set — the bot runs
  fully in-memory, losing state on restart, exactly as it did before Redis
  was added. No code path assumes persistence is available.
- **Per-chat keys, not one shared blob.** State is stored as
  `chat_state:{chat_id}` per chat rather than one JSON blob for every chat,
  specifically to avoid a read-modify-write race when two chats' alarms
  change concurrently — a lesson learned from an earlier shared-key design
  in this same codebase.
- **Trade-off**: an HTTP round-trip per read/write is slower than an
  in-memory or local-disk read. Acceptable here — nothing on the hot path
  (message classification, blur) touches Redis; only alarm/admin state
  changes do, and those are infrequent relative to message volume.
