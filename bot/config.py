"""Environment-driven configuration for the bot."""
import os


def _int_list(raw: str) -> set[int]:
    return {int(x) for x in raw.split(",") if x.strip()}


BOT_TOKEN = os.environ["BOT_TOKEN"]

# Telegram user IDs allowed to run /alarm_on, /alarm_off, /addkeyword, /removekeyword.
ADMIN_IDS = _int_list(os.environ.get("ADMIN_IDS", ""))

# Port Render (or any PaaS) injects for the health-check HTTP server.
PORT = int(os.environ.get("PORT", "8080"))

# Skip video blurring above this size to avoid CPU/time limits on free hosting tiers.
MAX_VIDEO_MB = int(os.environ.get("MAX_VIDEO_MB", "20"))

# Strength of the blur applied to flagged photos/videos.
PHOTO_BLUR_RADIUS = int(os.environ.get("PHOTO_BLUR_RADIUS", "35"))
VIDEO_BLUR_STRENGTH = int(os.environ.get("VIDEO_BLUR_STRENGTH", "30"))

WARNING_TEXT = (
    "⚠️ Повідомлення видалено. Публікація наслідків ударів (фото/відео/адреси/координати) "
    "під час чи одразу після атаки допомагає ворогу коригувати наведення. "
    "Дочекайтеся офіційного підтвердження від ДСНС/ОВА."
)
