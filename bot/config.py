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

# Per-chat admins: "chat_id:user_id,user_id; chat_id:user_id". Each chat's
# admins can run /alarm_on etc. and get DMs ONLY about their own chat — one
# bot deployment can serve several unrelated groups without their admins
# seeing each other's alarm activity. A chat not listed here isn't served at
# all: the bot auto-leaves it (see on_my_chat_member_update) rather than
# letting anyone add this single shared bot to their own group to probe it.
CHAT_ADMINS = _parse_chat_admins(os.environ.get("CHAT_ADMINS", ""))
ALLOWED_CHAT_IDS = set(CHAT_ADMINS.keys())

# Shared secrets you hand out privately (Signal, in person, etc.) to admins
# you trust to self-activate the bot in their own group, without you having
# to edit CHAT_ADMINS and redeploy for every new chat. A chat still isn't
# served until someone both knows a valid token AND is verified (via the
# Telegram API, not just their say-so) as an actual admin of that specific
# chat — see /claim in bot/handlers.py.
INVITE_TOKENS = {t.strip() for t in os.environ.get("INVITE_TOKENS", "").split(",") if t.strip()}
CLAIM_TIMEOUT_SECONDS = int(os.environ.get("CLAIM_TIMEOUT_SECONDS", "600"))

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

# Optional: auto-arm alarm mode from real air-raid status via alerts.in.ua
# (https://devs.alerts.in.ua/) instead of relying only on manual /alarm_on.
# Leave ALERTS_API_TOKEN empty to disable this entirely.
ALERTS_API_TOKEN = os.environ.get("ALERTS_API_TOKEN", "")
ALERTS_OBLAST_UID = os.environ.get("ALERTS_OBLAST_UID", "")
ALERTS_POLL_SECONDS = int(os.environ.get("ALERTS_POLL_SECONDS", "60"))

WARNING_TEXT = (
    "⚠️ Повідомлення видалено. Публікація наслідків ударів (фото/відео/адреси/координати) "
    "під час чи одразу після атаки допомагає ворогу коригувати наведення. "
    "Дочекайтеся офіційного підтвердження від ДСНС/ОВА."
)
