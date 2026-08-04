import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)

from bot import handlers, health
from bot.config import BOT_TOKEN, PORT

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)


def build_application() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", handlers.cmd_start))
    app.add_handler(CommandHandler("status", handlers.cmd_status))
    app.add_handler(CommandHandler("alarm_on", handlers.cmd_alarm_on))
    app.add_handler(CommandHandler("alarm_off", handlers.cmd_alarm_off))
    app.add_handler(CommandHandler("addkeyword", handlers.cmd_addkeyword))

    app.add_handler(MessageHandler(filters.LOCATION, handlers.on_location))
    app.add_handler(MessageHandler(filters.PHOTO, handlers.on_photo))
    app.add_handler(MessageHandler(filters.VIDEO | filters.VIDEO_NOTE, handlers.on_video))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.on_text))

    return app


def main() -> None:
    health.start_in_background(PORT)
    app = build_application()
    log.info("starting polling")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
