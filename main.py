import logging

from telegram import Update
from telegram.ext import (
    Application,
    ChatMemberHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from bot import air_alert, handlers, health, keywords, state, store, uptime_check
from bot.config import (
    ALERTS_API_TOKEN,
    ALERTS_OBLAST_UIDS,
    ALERTS_POLL_SECONDS,
    BOT_TOKEN,
    PORT,
    UPTIMEROBOT_API_KEY,
    UPTIMEROBOT_MONITOR_ID,
    UPTIMEROBOT_POLL_SECONDS,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)


async def _hydrate(_app: Application) -> None:
    """post_init hook: runs inside the same event loop run_polling manages,
    right after Application.initialize() and before polling starts. A
    separate top-level asyncio.run() here would leave nothing for PTB's
    own asyncio.get_event_loop() call to find afterward — that's exactly
    what broke the previous deploy (RuntimeError: no current event loop)."""
    if store.ENABLED:
        await state.hydrate()
        await keywords.hydrate()
        log.info("hydrated state from Redis")
    else:
        log.info("Redis persistence disabled (UPSTASH_REDIS_REST_URL/TOKEN not set) — running in-memory only")


def build_application() -> Application:
    app = Application.builder().token(BOT_TOKEN).post_init(_hydrate).build()

    # Runs before every other handler; drops updates from unauthorized chats.
    app.add_handler(MessageHandler(filters.ALL, handlers.guard_allowed_chat), group=-1)

    app.add_handler(CommandHandler("start", handlers.cmd_start))
    app.add_handler(CommandHandler("status", handlers.cmd_status))
    app.add_handler(CommandHandler("alarm_on", handlers.cmd_alarm_on))
    app.add_handler(CommandHandler("alarm_off", handlers.cmd_alarm_off))
    app.add_handler(CommandHandler("addkeyword", handlers.cmd_addkeyword))
    app.add_handler(CommandHandler("listkeywords", handlers.cmd_listkeywords))
    app.add_handler(CommandHandler("mychats", handlers.cmd_mychats))
    app.add_handler(CommandHandler("allowbot", handlers.cmd_allowbot))
    app.add_handler(CommandHandler("unmute", handlers.cmd_unmute))
    app.add_handler(CommandHandler("violations", handlers.cmd_violations))
    app.add_handler(CommandHandler("activate", handlers.cmd_activate))
    app.add_handler(CommandHandler("addadmin", handlers.cmd_addadmin))

    app.add_handler(ChatMemberHandler(handlers.on_chat_member_update, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(ChatMemberHandler(handlers.on_my_chat_member_update, ChatMemberHandler.MY_CHAT_MEMBER))

    app.add_handler(MessageHandler(filters.LOCATION, handlers.on_location))
    app.add_handler(MessageHandler(filters.PHOTO, handlers.on_photo))
    app.add_handler(MessageHandler(filters.VIDEO | filters.VIDEO_NOTE, handlers.on_video))
    app.add_handler(MessageHandler(filters.Document.ALL, handlers.on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.on_text))

    if ALERTS_API_TOKEN and ALERTS_OBLAST_UIDS:
        app.job_queue.run_repeating(air_alert.poll, interval=ALERTS_POLL_SECONDS, first=10)
        log.info("alerts.in.ua auto-alarm enabled for uids %s", sorted(ALERTS_OBLAST_UIDS))
    else:
        log.info("alerts.in.ua auto-alarm disabled (ALERTS_API_TOKEN/ALERTS_OBLAST_UID not set)")

    if UPTIMEROBOT_API_KEY and UPTIMEROBOT_MONITOR_ID:
        app.job_queue.run_repeating(uptime_check.poll, interval=UPTIMEROBOT_POLL_SECONDS, first=15)
        log.info("uptimerobot downtime reporting enabled for monitor %s", UPTIMEROBOT_MONITOR_ID)
    else:
        log.info("uptimerobot downtime reporting disabled (UPTIMEROBOT_API_KEY/UPTIMEROBOT_MONITOR_ID not set)")

    return app


def main() -> None:
    health.start_in_background(PORT)
    app = build_application()
    log.info("starting polling")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
