"""Poll alerts.in.ua for the configured oblast(s) and auto-toggle alarm mode.

API docs: https://devs.alerts.in.ua/ — GET /v1/alerts/active.json returns
{"alerts": [{"alert_type": "air_raid", "location_oblast_uid": "31",
"finished_at": null, ...}, ...]}. An alert with finished_at == null is
still ongoing.
"""
import logging

import httpx
from telegram.ext import ContextTypes

from bot.config import ALERTS_API_TOKEN, ALERTS_OBLAST_UIDS

log = logging.getLogger(__name__)

_ACTIVE_ALERTS_URL = "https://api.alerts.in.ua/v1/alerts/active.json"

# One pooled client for the process lifetime instead of a new TCP+TLS
# handshake every ALERTS_POLL_SECONDS tick.
_client = httpx.AsyncClient(timeout=10)

# Logged only on change, so a steady state doesn't spam the log every tick.
_last_logged_active: bool | None = None


async def _fetch_oblast_air_raid_active() -> bool:
    """True if ANY watched UID has an active air_raid alert — some oblasts
    are one UID, others are split per-raion, so "the whole oblast" can mean
    several UIDs (see ALERTS_OBLAST_UIDS)."""
    resp = await _client.get(_ACTIVE_ALERTS_URL, params={"token": ALERTS_API_TOKEN})
    resp.raise_for_status()
    data = resp.json()
    for alert in data.get("alerts", []):
        if (
            alert.get("alert_type") == "air_raid"
            and alert.get("finished_at") is None
            and str(alert.get("location_oblast_uid")) in ALERTS_OBLAST_UIDS
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
        log.info("alerts.in.ua: air_raid_active=%s in uids %s", now_active, sorted(ALERTS_OBLAST_UIDS))
        _last_logged_active = now_active

    # Local import: avoids a handlers<->air_alert import cycle at module load time.
    from bot import handlers, state

    for chat_id in state.known_chats():
        st = state.get(chat_id)
        if now_active and not st.alarm_active:
            await handlers.activate_alarm(context, chat_id, auto=True)
        elif not now_active and st.alarm_active and st.auto_armed:
            await handlers.deactivate_alarm(context, chat_id)
        elif now_active and st.alarm_active:
            # Re-apply lockdown every tick: a native Telegram admin can silently
            # override group permissions while the alarm is active, downgrading
            # the proactive media block back to a race. This self-heals it.
            await handlers.reapply_lockdown(context, chat_id)
