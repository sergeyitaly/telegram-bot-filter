"""Retrospectively report downtime UptimeRobot recorded for this bot's own
/health monitor. Necessarily retrospective: a process that's down can't run
code to say so, so this only ever surfaces "you were down from X to Y" once
the bot is back up and this poll runs — never a live "still down" alert.

API docs: https://uptimerobot.com/api/legacy/ — POST /v2/getMonitors with
logs=1 returns {"monitors": [{"logs": [{"type": 1, "datetime": <unix>,
"duration": <seconds>}, ...]}]}. Log type 1 = down, 2 = up; duration is
only meaningful on a down entry (how long that down period lasted).
"""
import logging
from datetime import datetime, timezone

import httpx
from telegram.ext import ContextTypes

from bot.config import UPTIMEROBOT_API_KEY, UPTIMEROBOT_MONITOR_ID

log = logging.getLogger(__name__)

_API_URL = "https://api.uptimerobot.com/v2/getMonitors"

# datetime (unix ts) of the most recent down-period we've already reported,
# so the same event isn't DM'd again on every subsequent poll.
_last_reported_at: int | None = None


async def _fetch_latest_down_log() -> dict | None:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            _API_URL,
            data={
                "api_key": UPTIMEROBOT_API_KEY,
                "monitors": UPTIMEROBOT_MONITOR_ID,
                "logs": "1",
                "format": "json",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    if data.get("stat") != "ok":
        log.warning("uptimerobot API returned non-ok: %s", data)
        return None

    monitors = data.get("monitors", [])
    if not monitors:
        return None

    down_logs = [
        entry for entry in monitors[0].get("logs", [])
        if entry.get("type") == 1 and entry.get("duration")
    ]
    if not down_logs:
        return None
    return max(down_logs, key=lambda e: e["datetime"])


async def poll(context: ContextTypes.DEFAULT_TYPE) -> None:
    global _last_reported_at
    try:
        latest = await _fetch_latest_down_log()
    except Exception:
        log.exception("uptimerobot poll failed")
        return

    if latest is None:
        return
    if _last_reported_at is not None and latest["datetime"] <= _last_reported_at:
        return
    _last_reported_at = latest["datetime"]

    started = datetime.fromtimestamp(latest["datetime"], tz=timezone.utc)
    minutes = latest["duration"] // 60
    log.info("uptimerobot: downtime detected, started %s, %s min", started.isoformat(), minutes)

    from bot import handlers  # local import: avoids a handlers<->uptime_check cycle

    await handlers.notify_owners(
        context,
        f"⚠️ UptimeRobot зафіксував, що бот був недоступний "
        f"{minutes} хв, починаючи з {started.strftime('%Y-%m-%d %H:%M UTC')}.",
    )
