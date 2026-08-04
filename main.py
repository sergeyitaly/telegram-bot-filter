import logging

from telegram import Update
from telegram.ext import (
    Application,
    ChatMemberHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from bot import air_alert, handlers, health
from bot.config import ALERTS_API_TOKEN, ALERTS_OBLAST_UIDS, ALERTS_POLL_SECONDS, BOT_TOKEN, PORT

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)


def build_application() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    # Runs before every other handler; drops updates from unauthorized chats.
    app.add_handler(MessageHandler(filters.ALL, handlers.guard_allowed_chat), group=-1)

    app.add_handler(CommandHandler("start", handlers.cmd_start))
    app.add_handler(CommandHandler("status", handlers.cmd_status))
    app.add_handler(CommandHandler("alarm_on", handlers.cmd_alarm_on))
    app.add_handler(CommandHandler("alarm_off", handlers.cmd_alarm_off))
    app.add_handler(CommandHandler("addkeyword", handlers.cmd_addkeyword))
    app.add_handler(CommandHandler("allowbot", handlers.cmd_allowbot))
    app.add_handler(CommandHandler("unmute", handlers.cmd_unmute))
    app.add_handler(CommandHandler("activate", handlers.cmd_activate))
    app.add_handler(CommandHandler("addadmin", handlers.cmd_addadmin))

    app.add_handler(ChatMemberHandler(handlers.on_chat_member_update, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(ChatMemberHandler(handlers.on_my_chat_member_update, ChatMemberHandler.MY_CHAT_MEMBER))

    app.add_handler(MessageHandler(filters.LOCATION, handlers.on_location))
    app.add_handler(MessageHandler(filters.PHOTO, handlers.on_photo))
    app.add_handler(MessageHandler(filters.VIDEO | filters.VIDEO_NOTE, handlers.on_video))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.on_text))

    if ALERTS_API_TOKEN and ALERTS_OBLAST_UIDS:
        app.job_queue.run_repeating(air_alert.poll, interval=ALERTS_POLL_SECONDS, first=10)
        log.info("alerts.in.ua auto-alarm enabled for uids %s", sorted(ALERTS_OBLAST_UIDS))
    else:
        log.info("alerts.in.ua auto-alarm disabled (ALERTS_API_TOKEN/ALERTS_OBLAST_UID not set)")

    return app


def main() -> None:
    health.start_in_background(PORT)
    app = build_application()
    log.info("starting polling")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
