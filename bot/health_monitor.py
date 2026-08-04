"""Periodic self-check for bot-wide operational health: Redis reachability
and update-processing backlog.

Both matter more the more groups/members share one bot deployment — a
single overloaded instance now silently fails lockdowns and filtering for
every group at once, not just one, and nobody would otherwise know until a
strike photo slipped through. Runs unconditionally (no external API key
needed, unlike the alerts.in.ua/UptimeRobot pollers) since this only reads
the bot's own internal state.
"""
import logging
import time

from telegram.ext import ContextTypes

from bot import state, store

log = logging.getLogger(__name__)

# Pending updates PTB hasn't gotten to processing yet. A healthy bot keeps
# this near zero; a sustained backlog means handlers can't keep up with
# incoming messages (blur/delete lag = a real leak window widening).
_QUEUE_BACKLOG_THRESHOLD = 20

# Aggregate per-user media rate-limit trips across the whole deployment in
# the trailing window. One user tripping it is normal flood protection;
# many users tripping it at once is either abuse or a real mass-strike
# moment where everyone's posting simultaneously — both worth a heads-up.
_RATE_TRIP_THRESHOLD = 15
_RATE_TRIP_WINDOW_SECONDS = 300

# Don't re-DM the same still-ongoing condition on every poll tick — once per
# cooldown is enough to know it hasn't resolved, and it clears immediately
# (next poll can re-alert) once the condition actually recovers.
_ALERT_COOLDOWN_SECONDS = 1800

_last_alerted: dict[str, float] = {}


def _should_alert(condition: str) -> bool:
    now = time.monotonic()
    last = _last_alerted.get(condition)
    if last is not None and now - last < _ALERT_COOLDOWN_SECONDS:
        return False
    _last_alerted[condition] = now
    return True


def _clear_alert(condition: str) -> None:
    _last_alerted.pop(condition, None)


async def poll(context: ContextTypes.DEFAULT_TYPE) -> None:
    from bot import handlers  # local import: avoids a handlers<->health_monitor cycle

    if store.ENABLED:
        if store.is_healthy():
            _clear_alert("redis")
        elif _should_alert("redis"):
            status = store.health_status()
            reason = f", остання причина: {status['last_failure_reason']}" if status["last_failure_reason"] else ""
            await handlers.notify_all_admins(
                context,
                "⚠️ Проблеми зі сховищем Redis (Upstash): "
                f"{status['consecutive_failures']} невдалих запитів поспіль{reason}. "
                "Стан бота (тривоги, ключові слова, забанені) може НЕ зберігатися "
                "між перезапусками, поки це не вирішиться.",
            )

    queue = getattr(context.application, "update_queue", None)
    if queue is not None:
        depth = queue.qsize()
        if depth < _QUEUE_BACKLOG_THRESHOLD:
            _clear_alert("queue_backlog")
        elif _should_alert("queue_backlog"):
            await handlers.notify_all_admins(
                context,
                f"⚠️ Бот перевантажений: у черзі обробки {depth} повідомлень. "
                "Видалення/блюр можуть запізнюватись.",
            )

    trips = state.count_recent_rate_limit_trips(_RATE_TRIP_WINDOW_SECONDS)
    if trips < _RATE_TRIP_THRESHOLD:
        _clear_alert("rate_trips")
    elif _should_alert("rate_trips"):
        await handlers.notify_all_admins(
            context,
            f"⚠️ Багато користувачів впираються у ліміт обробки медіа "
            f"({trips} за останні {_RATE_TRIP_WINDOW_SECONDS // 60} хв) — "
            "можливий флуд або масштабна подія одночасно в кількох чатах.",
        )
