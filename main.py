import logging

import sentry_sdk
from telegram import Update
from telegram.ext import (
    Application,
    ChatMemberHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from bot import air_alert, handlers, health, health_monitor, keywords, logging_utils, state, store, uptime_check
from bot.config import (
    ALERTS_API_TOKEN,
    ALERTS_OBLAST_UIDS,
    ALERTS_POLL_SECONDS,
    BOT_TOKEN,
    HEALTH_MONITOR_POLL_SECONDS,
    PORT,
    SENTRY_DSN,
    UPTIMEROBOT_API_KEY,
    UPTIMEROBOT_MONITOR_ID,
    UPTIMEROBOT_POLL_SECONDS,
)

logging_utils.configure(level=logging.INFO)
log = logging.getLogger(__name__)

if SENTRY_DSN:
    # No AsyncioIntegration: it needs a running event loop to instrument,
    # and this repo has already had two real crashes from event-loop timing
    # (see _hydrate below) — not worth the risk for a feature that's pure
    # error-reporting, not tracing. The default LoggingIntegration (always
    # on) already turns any log.error()/log.exception() call into a Sentry
    # event with no extra config, which is all this needs.
    sentry_sdk.init(dsn=SENTRY_DSN, traces_sample_rate=0.0)
    log.info("Sentry error tracking enabled")
else:
    log.info("Sentry error tracking disabled (SENTRY_DSN not set)")


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

    # Global catch-all for any exception a handler doesn't catch itself.
    # Without this, PTB logs it internally and moves on — with SENTRY_DSN
    # set, on_error's log.error(..., exc_info=...) is what actually turns
    # that into a Sentry event instead of a line nobody reads.
    app.add_error_handler(handlers.on_error)

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
    app.add_handler(CommandHandler("removeadmin", handlers.cmd_removeadmin))

    app.add_handler(ChatMemberHandler(handlers.on_chat_member_update, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(ChatMemberHandler(handlers.on_my_chat_member_update, ChatMemberHandler.MY_CHAT_MEMBER))

    # LOCATION catches both live-location and venue messages — Telegram sets
    # message.location on both types, so no separate venue handler is needed.
    app.add_handler(MessageHandler(filters.LOCATION, handlers.on_location))
    app.add_handler(MessageHandler(filters.PHOTO, handlers.on_photo))
    app.add_handler(MessageHandler(filters.VIDEO | filters.VIDEO_NOTE, handlers.on_video))
    # Animation (GIF) — same blur/delete pipeline as regular video.
    app.add_handler(MessageHandler(filters.ANIMATION, handlers.on_video))
    app.add_handler(MessageHandler(filters.Document.ALL, handlers.on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.on_text))
    # Voice and audio: delete during alarm, admin-alert during grace.
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handlers.on_voice))
    # Polls and stickers: routed through the deny-by-default catch-all below.

    # Edited messages: same classification pipeline — without these, editing a
    # flagged message after posting is a trivial bypass for all text/media filters.
    _edited = filters.UpdateType.EDITED_MESSAGE
    app.add_handler(MessageHandler(_edited & (filters.TEXT & ~filters.COMMAND), handlers.on_text))
    app.add_handler(MessageHandler(_edited & filters.PHOTO, handlers.on_photo))
    app.add_handler(MessageHandler(_edited & (filters.VIDEO | filters.VIDEO_NOTE), handlers.on_video))
    app.add_handler(MessageHandler(_edited & filters.ANIMATION, handlers.on_video))
    app.add_handler(MessageHandler(_edited & filters.Document.ALL, handlers.on_document))
    app.add_handler(MessageHandler(_edited & (filters.VOICE | filters.AUDIO), handlers.on_voice))

    # Deny-by-default catch-all in group 1 (runs after all specific handlers
    # above). During active alarm: deletes any message type not explicitly handled
    # so future Telegram API additions cannot silently create a bypass.
    app.add_handler(MessageHandler(filters.ALL, handlers.on_alarm_catchall), group=1)

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

    app.job_queue.run_repeating(health_monitor.poll, interval=HEALTH_MONITOR_POLL_SECONDS, first=30)
    log.info("health monitor enabled (every %ss): redis + processing backlog", HEALTH_MONITOR_POLL_SECONDS)

    return app


def main() -> None:
    health.start_in_background(PORT)
    app = build_application()
    log.info("starting polling")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
