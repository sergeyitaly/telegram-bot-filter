# ADR-0003: Chat-wide permission lockdown instead of reactive deletion

## Status

Accepted — this is the central architectural decision of the whole
project; every other alarm-mode behavior builds on it.

## Context

The naive approach to blocking dangerous content is reactive: watch every
message, classify it, delete it if flagged. That's what this bot does for
text (keyword filtering has to be reactive — there's no way to block a
message before it's typed). It is not sufficient for photos and videos
during an active alert.

Telegram delivers a message to every member and every bot in the chat the
instant it's posted, independently of when *this* bot gets around to
reacting to it. No amount of reaction-speed tuning closes that gap: a
human already looking at the chat, or a userbot scraping it, sees the
photo before any delete call lands. A strike-result photo is exactly the
kind of content where "briefly visible" is already the failure — a single
screenshot or forward defeats the entire point of deleting it a moment
later.

## Decision

When alarm mode activates, lock chat-wide permissions
(`can_send_photos`/`can_send_videos`/`can_send_documents`/etc. = `False`
via `setChatPermissions` with `use_independent_chat_permissions=True`) so
non-admin members cannot send media **at all** while the alarm is active.
Text stays reactive (keyword-filtered on delete), since blocking text
outright would prevent legitimate coordination during an alert.

## Consequences

- **Nothing to scrape.** If the content was never sent, there's no window
  where it's visible to delete-race against. This converts a timing
  problem (react fast enough) into a permissions problem (don't allow it
  in the first place), which is strictly stronger.
- **Admins are always exempt** from chat permissions by Telegram's own
  design — this only restricts non-admin members, which is the intended
  scope (admins are trusted, and the bot has no mechanism to restrict them
  even if it wanted to).
- **The lockdown must be reapplied, not just set once.** A native Telegram
  admin can silently override chat permissions through the normal Telegram
  UI at any time. `air_alert.poll`'s `reapply_lockdown()` re-enforces it on
  every tick while the alarm is active, self-healing that override within
  one poll interval instead of requiring the bot to notice and react.
- **Redundant activation must not corrupt the original state.** Because
  the lockdown mutates real chat permissions and must be reversed on
  `/alarm_off`, the code has to track what the chat's permissions were
  *before* lockdown, restore them accurately, and never lose that value —
  see the two related bugs and fixes documented in the `telegram-bot-filter`
  skill's "Known operational bugs" table (`activate_alarm`/
  `deactivate_alarm` double-capture and failed-restore issues). Reactive
  deletion has no equivalent failure mode; it's stateless per message.
- **Trade-off**: this only works in supergroups, where the bot can be
  granted the *restrict members* admin permission. A legacy basic group
  can't have per-member restrictions applied by a bot; `activate_alarm`
  detects the `BadRequest` from Telegram in that case and falls back to
  announcing that lockdown failed, with reactive text filtering as the
  only remaining protection.
