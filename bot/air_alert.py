"""Poll alerts.in.ua for the configured oblast and auto-toggle alarm mode.

API docs: https://devs.alerts.in.ua/ — GET /v1/alerts/active.json returns
{"alerts": [{"alert_type": "air_raid", "location_oblast_uid": "31",
"finished_at": null, ...}, ...]}. An alert with finished_at == null is
still ongoing.
"""
import logging

import httpx
from telegram.ext import ContextTypes

from bot.config import ALERTS_API_TOKEN, ALERTS_OBLAST_UID

log = logging.getLogger(__name__)

_ACTIVE_ALERTS_URL = "https://api.alerts.in.ua/v1/alerts/active.json"

# Logged only on change, so a steady state doesn't spam the log every tick.
_last_logged_active: bool | None = None


async def _fetch_oblast_air_raid_active() -> bool:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(_ACTIVE_ALERTS_URL, params={"token": ALERTS_API_TOKEN})
        resp.raise_for_status()
        data = resp.json()
    for alert in data.get("alerts", []):
        if (
            alert.get("alert_type") == "air_raid"
            and alert.get("finished_at") is None
            and str(alert.get("location_oblast_uid")) == ALERTS_OBLAST_UID
        ):
            return True
    return False


async def poll(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs every ALERTS_POLL_SECONDS. Re-syncs every known chat to the
    current real-world alert state each tick (not just on edges) — this is
    what makes it self-healing: a chat that registers mid-alert, or a bot
    that restarts mid-alert (e.g. Render free tier waking from sleep), still
    ends up correctly armed on the very next tick instead of waiting for the
    alert to toggle off and on again."""
    global _last_logged_active
    try:
        now_active = await _fetch_oblast_air_raid_active()
    except Exception:
        log.exception("alerts.in.ua poll failed")
        return

    if now_active != _last_logged_active:
        log.info("alerts.in.ua: air_raid_active=%s in oblast %s", now_active, ALERTS_OBLAST_UID)
        _last_logged_active = now_active

    # Local import: avoids a handlers<->air_alert import cycle at module load time.
    from bot import handlers, state

    for chat_id in state.known_chats():
        st = state.get(chat_id)
        if now_active and not st.alarm_active:
            await handlers.activate_alarm(context, chat_id, auto=True)
        elif not now_active and st.alarm_active and st.auto_armed:
            await handlers.deactivate_alarm(context, chat_id)
