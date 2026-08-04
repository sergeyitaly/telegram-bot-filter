"""Telegram update handlers."""
import logging
import os
import tempfile

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot import filters as classify
from bot import keywords, media, state
from bot.config import ADMIN_IDS, MAX_VIDEO_MB, WARNING_TEXT

log = logging.getLogger(__name__)


def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def _warn(update: Update) -> None:
    try:
        await update.effective_chat.send_message(
            WARNING_TEXT,
            reply_to_message_id=None,
        )
    except Exception:
        log.exception("failed to send warning")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Air-alarm content filter is active. Admins: /alarm_on, /alarm_off, "
        "/addkeyword <слово>, /status."
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    st = state.get(update.effective_chat.id)
    await update.message.reply_text(
        f"Alarm mode: {'ON' if st.alarm_active else 'off'}"
    )


async def cmd_alarm_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id):
        return
    state.set_alarm(update.effective_chat.id, True)
    await update.message.reply_text("🚨 Alarm mode ON — all photos/videos will be blurred, location chatter blocked.")


async def cmd_alarm_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id):
        return
    state.set_alarm(update.effective_chat.id, False)
    await update.message.reply_text("✅ Alarm mode off.")


async def cmd_addkeyword(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /addkeyword <термін> [location]")
        return
    tier = "location" if context.args[-1] == "location" else "strike"
    term = " ".join(context.args[:-1] if tier == "location" else context.args)
    keywords.add_term(term, tier)
    await update.message.reply_text(f"Added \"{term}\" to {tier} keywords.")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    st = state.get(update.effective_chat.id)
    verdict = classify.classify_text(msg.text, st.alarm_active)
    if not verdict.flagged:
        return
    try:
        await msg.delete()
    except Exception:
        log.exception("failed to delete text message")
        return
    log.info("deleted text in chat %s: %s", update.effective_chat.id, verdict.reason)
    await _warn(update)


async def on_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    try:
        await msg.delete()
    except Exception:
        log.exception("failed to delete location message")
        return
    log.info("deleted live location in chat %s", update.effective_chat.id)
    await _warn(update)


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    st = state.get(update.effective_chat.id)
    verdict = classify.classify_media(msg.caption or "", st.alarm_active)
    if not verdict.flagged:
        return

    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.UPLOAD_PHOTO)
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "in.jpg")
        dst = os.path.join(tmp, "out.jpg")
        photo = msg.photo[-1]
        tg_file = await photo.get_file()
        await tg_file.download_to_drive(src)
        media.blur_photo(src, dst)

        try:
            await msg.delete()
        except Exception:
            log.exception("failed to delete photo message")
            return

        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=open(dst, "rb"),
            caption=f"🔒 Фото заблюрено ({verdict.reason}).\n{WARNING_TEXT}",
        )
    log.info("blurred photo in chat %s: %s", update.effective_chat.id, verdict.reason)


async def on_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    video = msg.video or msg.video_note
    st = state.get(update.effective_chat.id)
    verdict = classify.classify_media(msg.caption or "", st.alarm_active)
    if not verdict.flagged:
        return

    if video.file_size and video.file_size > MAX_VIDEO_MB * 1024 * 1024:
        try:
            await msg.delete()
        except Exception:
            log.exception("failed to delete oversized video")
            return
        await _warn(update)
        log.info("deleted oversized video (%s bytes) in chat %s", video.file_size, update.effective_chat.id)
        return

    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.UPLOAD_VIDEO)
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "in.mp4")
        dst = os.path.join(tmp, "out.mp4")
        tg_file = await video.get_file()
        await tg_file.download_to_drive(src)
        blurred_ok = media.blur_video(src, dst)

        try:
            await msg.delete()
        except Exception:
            log.exception("failed to delete video message")
            return

        if blurred_ok:
            await context.bot.send_video(
                chat_id=update.effective_chat.id,
                video=open(dst, "rb"),
                caption=f"🔒 Відео заблюрено ({verdict.reason}).\n{WARNING_TEXT}",
            )
        else:
            await _warn(update)
    log.info("processed video in chat %s: %s", update.effective_chat.id, verdict.reason)
