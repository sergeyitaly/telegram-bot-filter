# ADR-0006: Human-reviewed audit log instead of automated escalation

## Status

Accepted

## Context

Repeat offenders — someone who keeps posting strike-result content across
multiple alerts — are a real pattern worth surfacing to chat admins. The
tempting automated version is: track violations, and once a user crosses a
threshold, escalate automatically (report to authorities, ban outright,
etc.) without a human in the loop.

This bot's filter has real, acknowledged false-positive risk (see
`bot/keywords.py` — colloquial euphemisms like "бавовна" are deliberately
broad to catch evasion, which also means they can fire on unrelated
usage). Automating an escalation with real-world consequences (a police
report, a permanent ban) on top of an imperfect keyword match is a
categorically bigger and harder-to-reverse action than deleting a message,
and the two shouldn't share the same trust level.

## Decision

`state.log_violation`/`get_violation_log` maintain a durable, per-chat,
per-user audit trail (timestamp, matched reason, message text — never
media) that chat admins review manually via `/violations [user_id]`.
Crossing `REPORT_VIOLATION_THRESHOLD` DMs admins to go look, it does not
itself take any action. The bot never contacts any authority and never
decides an account is malicious on its own; any escalation beyond the bot
(reporting someone, contacting police) is entirely a human admin's
decision, made after reviewing the log.

`AUTO_KICK_ON_REPORT_THRESHOLD` (default **off**) is a narrower, separate
opt-in: crossing the threshold can *optionally* remove (not ban) the user
from that one chat and DM them why. Removal from one community chat and
reporting someone to authorities are very different magnitudes of
consequence — the audit log is the trust boundary the bot is comfortable
crossing itself is limited to; the log always exists, one further and much
smaller step (removal) is opt-in, and reporting to anyone outside the chat
is never automated at all.

## Consequences

- **A false positive costs a manual review, not an unrecoverable action.**
  The worst case of the filter mismatching text is an admin spending a
  minute on `/violations` and concluding it was nothing — not a wrongful
  report or an unjust ban that already happened.
- **The audit log itself can't become a new leak vector.** It's
  deliberately text-only — never the flagged photo/video — for the same
  reason the filter exists in the first place: a compromised or
  subpoenaed log shouldn't reproduce the content it was built to suppress.
- **Slower response to a genuine repeat offender.** A human has to
  actually read `/violations` and act; there's no path from "threshold
  crossed" to "reported" without that step. Accepted deliberately — the
  cost of a slow correct response is much lower than a fast wrong one at
  this consequence level.
- **`AUTO_KICK_ON_REPORT_THRESHOLD` is the one narrow exception**, and even
  it only removes (rejoinable via invite link), never bans, and only from
  that one chat.
