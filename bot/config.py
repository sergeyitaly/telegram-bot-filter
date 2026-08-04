"""Environment-driven configuration for the bot."""
import os


def _int_list(raw: str) -> set[int]:
    return {int(x) for x in raw.split(",") if x.strip()}


def _parse_chat_admins(raw: str) -> dict[int, set[int]]:
    """'-100111:11,22; -100222:33' -> {-100111: {11,22}, -100222: {33}}."""
    result: dict[int, set[int]] = {}
    for entry in raw.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        chat_str, _, admins_str = entry.partition(":")
        result[int(chat_str.strip())] = _int_list(admins_str)
    return result


BOT_TOKEN = os.environ["BOT_TOKEN"]

# The bot deployer(s): always admin in every chat, and the only ones notified
# about bot-wide security events (e.g. an unauthorized chat add) since those
# aren't scoped to any one group.
OWNER_IDS = _int_list(os.environ.get("OWNER_IDS", ""))

# Per-chat admins you hardcode in advance: "chat_id:user_id,user_id;
# chat_id:user_id". Each chat's admins can run /alarm_on etc. and get DMs
# ONLY about their own chat — one bot deployment can serve several unrelated
# groups without their admins seeing each other's alarm activity. A chat not
# listed here isn't automatically served either: on_my_chat_member_update
# self-registers a chat the moment it's added, but only if the person who
# added the bot is a verified Telegram admin/creator of that chat — anyone
# else gets the bot removed immediately. See bot/handlers.py.
CHAT_ADMINS = _parse_chat_admins(os.environ.get("CHAT_ADMINS", ""))
ALLOWED_CHAT_IDS = set(CHAT_ADMINS.keys())

# Bot accounts allowed to join without being auto-kicked (e.g. other moderation
# bots you deliberately add). Extendable at runtime via /allowbot.
TRUSTED_BOT_IDS = _int_list(os.environ.get("TRUSTED_BOT_IDS", ""))

# Port Render (or any PaaS) injects for the health-check HTTP server.
PORT = int(os.environ.get("PORT", "8080"))

# Skip video blurring above this size to avoid CPU/time limits on free hosting tiers.
MAX_VIDEO_MB = int(os.environ.get("MAX_VIDEO_MB", "20"))

# Strength of the blur applied to flagged photos/videos.
PHOTO_BLUR_RADIUS = int(os.environ.get("PHOTO_BLUR_RADIUS", "35"))
VIDEO_BLUR_STRENGTH = int(os.environ.get("VIDEO_BLUR_STRENGTH", "30"))

# How many filter hits within VIOLATION_WINDOW_SECONDS before a non-admin
# member gets auto-restricted (muted) pending admin review.
VIOLATION_THRESHOLD = int(os.environ.get("VIOLATION_THRESHOLD", "3"))
VIOLATION_WINDOW_SECONDS = int(os.environ.get("VIOLATION_WINDOW_SECONDS", "600"))

# Separate from the above: a cumulative, no-time-window count of a user's
# TOTAL logged violations in a chat (persisted — see state.log_violation),
# for spotting a pattern across many separate alarms rather than a single
# burst. Crossing a multiple of this notifies admins to review /violations
# <user_id> and decide whether escalation (e.g. to police) is warranted.
REPORT_VIOLATION_THRESHOLD = int(os.environ.get("REPORT_VIOLATION_THRESHOLD", "10"))

# If true, crossing REPORT_VIOLATION_THRESHOLD also kicks the user from that
# chat (removed, not banned — they can rejoin via invite link) and DMs them
# privately why. Off by default: the filter has real false-positive risk
# (see bot/keywords.py), and an automated removal from a community based on
# an imperfect keyword match is a much bigger step than the admin
# notification alone. Opt in deliberately, and only if you've weighed that
# tradeoff for your own chat(s) — this is a single global setting for every
# chat this deployment serves, not configurable per chat.
AUTO_KICK_ON_REPORT_THRESHOLD = os.environ.get("AUTO_KICK_ON_REPORT_THRESHOLD", "").lower() in ("1", "true", "yes")

# How long after alarm mode turns off (відбій) strike-result keyword/caption
# text filtering keeps applying, before winding down to no filtering at all.
# Coordinates are always blocked regardless of this window — that's a direct
# location leak, not a timing-sensitive one. Default 2 hours.
POST_ALARM_GRACE_SECONDS = int(os.environ.get("POST_ALARM_GRACE_SECONDS", "7200"))

# Optional: auto-arm alarm mode from real air-raid status via alerts.in.ua
# (https://devs.alerts.in.ua/) instead of relying only on manual /alarm_on.
# Leave ALERTS_API_TOKEN empty to disable this entirely.
ALERTS_API_TOKEN = os.environ.get("ALERTS_API_TOKEN", "")

# Comma-separated location_oblast_uid values to watch — alarm arms if ANY of
# them has an active air_raid alert. Some oblasts are one UID (e.g. м. Київ =
# 31); densely populated ones are broken into per-raion UIDs that share the
# oblast name, so covering the whole oblast means listing all of them (e.g.
# Київська область = 73,74,75,76,77,78,79, its 7 raions). Find UIDs for your
# region at https://devs.alerts.in.ua/.
ALERTS_OBLAST_UIDS = {u.strip() for u in os.environ.get("ALERTS_OBLAST_UID", "").split(",") if u.strip()}
ALERTS_POLL_SECONDS = int(os.environ.get("ALERTS_POLL_SECONDS", "15"))

# Optional: retrospectively DM owners about downtime UptimeRobot recorded for
# this bot's own /health monitor. This can only ever be retrospective — code
# that isn't running can't report that it isn't running — so it's checked
# periodically while the bot IS up, surfacing "you were down from X to Y"
# after the fact (e.g. right after a Render free-tier cold start). Leave
# UPTIMEROBOT_API_KEY empty to disable.
UPTIMEROBOT_API_KEY = os.environ.get("UPTIMEROBOT_API_KEY", "")
UPTIMEROBOT_MONITOR_ID = os.environ.get("UPTIMEROBOT_MONITOR_ID", "")
UPTIMEROBOT_POLL_SECONDS = int(os.environ.get("UPTIMEROBOT_POLL_SECONDS", "600"))

# Optional: persist claimed admins, custom keywords, and alarm/lockdown
# state across restarts via Upstash Redis's REST API (no TCP client needed,
# fits the same request/response style as the other pollers). Free tier at
# upstash.com. Leave either empty to run fully in-memory as before — the
# bot still works, it just re-loses this state on every restart.
UPSTASH_REDIS_REST_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "")
UPSTASH_REDIS_REST_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")

WARNING_TEXT = (
    "⚠️ Повідомлення видалено. Публікація наслідків ударів (фото/відео/адреси/координати) "
    "під час чи одразу після атаки допомагає ворогу коригувати наведення. "
    "Дочекайтеся офіційного підтвердження від ДСНС/ОВА."
)
