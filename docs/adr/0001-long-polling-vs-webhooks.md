# ADR-0001: Long polling instead of webhooks

## Status

Accepted

## Context

python-telegram-bot supports two ways to receive updates: long polling
(`Application.run_polling()`, the bot repeatedly calls `getUpdates`) or
webhooks (Telegram pushes updates to a public HTTPS endpoint the bot
exposes). This bot runs on Render's free tier, which:

- Sleeps a web service after ~15 minutes with no incoming HTTP traffic,
  waking on the next request with a cold-start delay.
- Gives every service a public HTTPS URL, so webhooks are technically
  available, not just polling.

A webhook-based design would need: registering the webhook URL with
Telegram, verifying incoming requests actually came from Telegram (a secret
token in the URL path or a header, checked on every request) rather than an
arbitrary caller, and a route to receive `POST` bodies — real, if modest,
additional surface area.

## Decision

Use long polling (`app.run_polling(allowed_updates=Update.ALL_TYPES)`).

## Consequences

- **No inbound HTTPS surface to secure.** The bot only makes outbound calls
  (to Telegram, alerts.in.ua, Upstash, UptimeRobot, Sentry). There's no
  webhook endpoint to spoof, no secret token to leak, no request to
  validate.
- **Plays better with Render's free-tier sleep cycle.** A sleeping webhook
  service can miss the moment Telegram tries to deliver — Telegram retries
  webhook failures for a while but on its own schedule, not the bot's. A
  polling bot just makes its next `getUpdates` call whenever it wakes up
  and picks up the queued backlog Telegram already held for it; the
  semantics are simpler to reason about.
- **A separate HTTP server is still needed anyway** — Render's own health
  check and UptimeRobot both need something to ping (`bot/health.py`, a
  minimal aiohttp server) to prevent the free-tier sleep in the first
  place. Long polling doesn't remove that requirement, it just decouples it
  from update delivery.
- **Trade-off**: polling has slightly higher latency than a push-based
  webhook (bounded by the poll interval PTB uses internally) and holds an
  open outbound connection. Neither matters at this bot's scale or for its
  actual sensitivity (seconds of alarm-arm latency, not milliseconds).
