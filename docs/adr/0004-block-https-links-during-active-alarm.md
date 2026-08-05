# ADR-0004: Block generic HTTPS links during active alarm

## Status

Accepted

## Context

Coordinates and map-service URLs are blocked unconditionally at all times
(`bot/keywords.py`: `has_coordinates`, `has_map_url`) — a direct location
leak, not a timing-sensitive one, and blocking them permanently has no
real cost to normal chat use. Generic `https://` links (a news article
about the strike, a photo hosted elsewhere) are different: they're
completely normal chat content outside of an alarm, and members should be
able to share official updates and articles freely during the post-alarm
grace window.

The specific risk during an *active* alarm is Telegram's own behavior:
when a message containing a URL is posted, Telegram's servers generate a
link preview (title, description, thumbnail image) and attach it to the
message automatically, server-side, before the bot's own handler even
runs. If that article describes strike results or shows a photo, the
preview itself has already revealed that content the moment the message
was delivered — deleting the message a moment later does not un-render a
preview that every recipient already saw.

## Decision

Block any message containing an `https://` URL, but only while
`alarm_active` is `True` — not during the post-alarm grace window, and not
outside alarm mode entirely (`bot/filters.py::classify_text`,
`keywords.has_url`).

## Consequences

- **Closes the same class of leak as ADR-0003, for a different content
  type.** Coordinates/map URLs are blocked always because there's no
  legitimate reason to ever share them during a threat window; generic
  URLs are blocked only during the active alarm because the specific
  mechanism (server-side preview rendering) is only dangerous while the
  situation is still unfolding.
- **The grace window stays permissive.** Once the alarm ends, members can
  immediately share links to official confirmations, news coverage, etc.
  — exactly the kind of legitimate post-alarm discussion this bot is
  designed not to permanently suppress (see `POST_ALARM_GRACE_SECONDS`).
- **Trade-off**: this blocks *all* links during an alarm, including
  harmless ones (a meme, an unrelated article) — the bot has no way to
  distinguish a link's preview content without fetching and rendering it
  itself, which isn't worth building. Members can still share links as
  plain text (`example.com` without `https://`) if they strip the scheme,
  though this makes the URL non-clickable and won't generate a preview
  either — an acceptable, self-limiting workaround given the alternative
  is a live preview leaking strike details.
