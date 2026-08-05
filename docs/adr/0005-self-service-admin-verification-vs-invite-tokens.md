# ADR-0005: Verified-admin-status onboarding instead of invite tokens

## Status

Accepted (supersedes an earlier invite-token design)

## Context

This is one deployed bot on one token, meant to serve several unrelated
groups (see the multi-group support in `bot/config.py::CHAT_ADMINS` and
`state.claimed_admins`). Left unguarded, anyone could add the shared bot
to their own chat just to see how the filter behaves, or to squat on it.
Some form of onboarding gate is needed.

The first design built for this was a shared invite-token scheme: the
deployer would generate a token, hand it to a group's admin out of band,
and the bot would check it against that token before activating in a new
chat. This was rejected during development — a shared secret the deployer
has to personally generate and distribute doesn't scale past a handful of
groups, and it recreates exactly the kind of manual bottleneck (every new
group needs the deployer in the loop) that this project's multi-group
support was supposed to eliminate.

## Decision

Gate onboarding on Telegram's own API instead of a shared secret:
`on_my_chat_member_update` calls `get_chat_member` to confirm whoever
added the bot to a chat is an actual `administrator` or `creator` of that
chat, at the moment it's added. If confirmed, the chat self-registers
immediately (`_register_chat_admin`); if not, the bot leaves the chat
immediately and DMs the owners.

## Consequences

- **No deployer bottleneck.** A group's own admin adds the bot and it's
  live — no token to generate, distribute, or revoke, no request to the
  deployer.
- **The trust check is Telegram's, not the bot's own say-so.** The bot
  doesn't ask "are you an admin?" and trust the answer — it independently
  verifies via the Bot API. A non-admin adding the bot (even claiming to
  be one) gets kicked out automatically.
- **No secret to leak.** An invite token, once shared, is a bearer
  credential — anyone who obtains it (screenshot, forward, compromised
  chat) can claim admin status in a new chat. Verified-status onboarding
  has no equivalent artifact to leak.
- **Trade-off**: this ties onboarding to whoever happens to add the bot,
  not a pre-vetted allowlist. `CHAT_ADMINS` (env var) still exists for
  chats the deployer wants to hard-register in advance regardless of who
  adds the bot — the two mechanisms coexist, self-service is the default
  path and pre-registration is the exception.
