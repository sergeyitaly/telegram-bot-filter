# Architecture Decision Records

Why the bot is shaped the way it is, not just what it does. Each ADR
records a decision, the alternative(s) considered, and the trade-offs
accepted — so a future change doesn't accidentally re-litigate (or
silently undo) reasoning that was already worked through.

Format: [Michael Nygard's ADR template](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions)
(Status / Context / Decision / Consequences). New ADRs are numbered
sequentially and are not edited after acceptance — if a decision changes,
write a new ADR that supersedes the old one and say so in both files'
Status sections, matching [ADR-0005](0005-self-service-admin-verification-vs-invite-tokens.md).

| ADR | Decision |
|---|---|
| [0001](0001-long-polling-vs-webhooks.md) | Long polling instead of webhooks |
| [0002](0002-upstash-redis-vs-local-storage.md) | Upstash Redis (REST) instead of local/disk storage |
| [0003](0003-permission-lockdown-vs-reactive-deletion.md) | Chat-wide permission lockdown instead of reactive deletion |
| [0004](0004-block-https-links-during-active-alarm.md) | Block generic HTTPS links during active alarm |
| [0005](0005-self-service-admin-verification-vs-invite-tokens.md) | Verified-admin-status onboarding instead of invite tokens |
| [0006](0006-human-reviewed-audit-log-vs-automated-escalation.md) | Human-reviewed audit log instead of automated escalation |

See the [README's architecture diagram](../../README.md#architecture) for
how these pieces fit together, and the `telegram-bot-filter` skill's
"Known operational bugs" table for incidents that came out of some of
these decisions' sharp edges (e.g. ADR-0003's permission-restore state
machine).
