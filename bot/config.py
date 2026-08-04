"""Environment-driven configuration for the bot."""
import os


def _int_list(raw: str) -> set[int]:
    return {int(x) for x in raw.split(",") if x.strip()}


BOT_TOKEN = os.environ["BOT_TOKEN"]

# Telegram user IDs allowed to run /alarm_on, /alarm_off, /addkeyword, /removekeyword.
ADMIN_IDS = _int_list(os.environ.get("ADMIN_IDS", ""))

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
