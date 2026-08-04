"""Telegram update handlers."""
import logging
import os
import tempfile
from datetime import datetime, timezone

from telegram import ChatPermissions, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from bot import filters as classify
from bot import keywords, media, state
from bot.config import (
    ADMIN_IDS,
    MAX_VIDEO_MB,
    TRUSTED_BOT_IDS,
    VIOLATION_THRESHOLD,
    VIOLATION_WINDOW_SECONDS,
    WARNING_TEXT,
)

_LOCKDOWN_PERMS = dict(
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_other_messages=False,
)

log = logging.getLogger(__name__)


def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def _exposure_seconds(msg) -> float:
    """How long the message was visible to chat members before we deleted it."""
    return (datetime.now(timezone.utc) - msg.date).total_seconds()


async def _notify_admins(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """DM every configured admin instead of posting to the group, so ordinary
    members never see when alarm mode is toggled."""
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=text)
        except Exception:
            log.warning("could not DM admin %s (they may need to /start the bot first)", admin_id)


async def _delete_silently(msg) -> None:
    try:
        await msg.delete()
    except Exception:
        log.warning("could not delete command message")


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
    if not _is_admin(update.effective_user.id):
        return
    await _delete_silently(update.effective_message)
    st = state.get(update.effective_chat.id)
    await _notify_admins(context, f"Alarm mode: {'ON' if st.alarm_active else 'off'}")


async def activate_alarm(context: ContextTypes.DEFAULT_TYPE, chat_id: int, auto: bool = False) -> None:
    """Turn alarm mode on: lock down media send permissions chat-wide so
    there's nothing for anyone already watching to scrape, reactive keyword
    filtering keeps handling text. Reused by /alarm_on and the alerts.in.ua poller."""
    state.set_alarm(chat_id, True, auto=auto)

    lockdown_note = ""
    try:
        chat = await context.bot.get_chat(chat_id)
        original = chat.permissions
        st = state.get(chat_id)
        st.saved_permissions = original
        await context.bot.set_chat_permissions(
            chat_id,
            ChatPermissions(
                can_send_messages=original.can_send_messages if original else True,
                can_send_polls=original.can_send_polls if original else False,
                can_add_web_page_previews=original.can_add_web_page_previews if original else False,
                can_change_info=original.can_change_info if original else False,
                can_invite_users=original.can_invite_users if original else False,
                can_pin_messages=original.can_pin_messages if original else False,
                can_manage_topics=original.can_manage_topics if original else False,
                **_LOCKDOWN_PERMS,
            ),
            # Without this, Telegram derives can_send_photos/videos/etc from
            # can_send_messages and silently ignores our explicit False values.
            use_independent_chat_permissions=True,
        )
    except BadRequest as exc:
        log.warning("could not restrict media in chat %s: %s", chat_id, exc)
        lockdown_note = (
            "\n⚠️ Не вдалось заблокувати надсилання медіа "
            f"({exc.message}). Ймовірно, це базова група — "
            "оновіть її до supergroup, щоб увімкнути цей захист. "
            "Реактивне видалення за ключовими словами й надалі працює."
        )

    source = "автоматично (alerts.in.ua)" if auto else "вручну"
    await _notify_admins(
        context,
        f"🚨 Alarm mode ON ({source}) — фото/відео від учасників заблоковано на рівні чату, "
        "текст фільтрується реактивно." + lockdown_note
    )


async def deactivate_alarm(context: ContextTypes.DEFAULT_TYPE, chat_id: int, auto: bool = False) -> None:
    state.set_alarm(chat_id, False)

    st = state.get(chat_id)
    restore_note = ""
    if st.saved_permissions is not None:
        try:
            await context.bot.set_chat_permissions(
                chat_id, st.saved_permissions, use_independent_chat_permissions=True
            )
        except BadRequest as exc:
            log.warning("could not restore permissions in chat %s: %s", chat_id, exc)
            restore_note = f"\n⚠️ Не вдалось відновити права чату ({exc.message}) — перевірте вручну."
        st.saved_permissions = None

    source = "автоматично (alerts.in.ua)" if auto else "вручну"
    await _notify_admins(context, f"✅ Alarm mode off ({source})." + restore_note)


async def cmd_alarm_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id):
        return
    await _delete_silently(update.effective_message)
    await activate_alarm(context, update.effective_chat.id, auto=False)


async def cmd_alarm_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id):
        return
    await _delete_silently(update.effective_message)
    await deactivate_alarm(context, update.effective_chat.id, auto=False)


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


async def cmd_allowbot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /allowbot <bot_id>")
        return
    bot_id = int(context.args[0])
    TRUSTED_BOT_IDS.add(bot_id)
    await update.message.reply_text(f"Bot {bot_id} whitelisted — add it to the group now.")


async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /unmute <user_id>")
        return
    user_id = int(context.args[0])
    chat_id = update.effective_chat.id
    try:
        chat = await context.bot.get_chat(chat_id)
        await context.bot.restrict_chat_member(
            chat_id, user_id, permissions=chat.permissions, use_independent_chat_permissions=True
        )
    except BadRequest as exc:
        await update.message.reply_text(f"Failed: {exc.message}")
        return
    await update.message.reply_text(f"User {user_id} unmuted.")


async def on_my_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Track which chats the bot is currently a member of, so the
    alerts.in.ua poller knows where to (de)activate alarm mode."""
    cmu = update.my_chat_member
    chat_id = cmu.chat.id
    if cmu.new_chat_member.status in ("left", "kicked"):
        state.unregister_chat(chat_id)
    else:
        state.register_chat(chat_id)


async def _track_violation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Repeat offenders (deliberate or careless insiders) get auto-muted
    pending admin review, instead of just having each message deleted."""
    user = update.effective_message.from_user
    if user is None or _is_admin(user.id):
        return
    chat_id = update.effective_chat.id
    count = state.record_violation(chat_id, user.id, VIOLATION_WINDOW_SECONDS)
    if count < VIOLATION_THRESHOLD:
        return
    try:
        await context.bot.restrict_chat_member(
            chat_id, user.id,
            ChatPermissions(can_send_messages=False, **_LOCKDOWN_PERMS, can_send_polls=False),
        )
    except Exception:
        log.exception("failed to restrict repeat offender %s in chat %s", user.id, chat_id)
        return
    await _notify_admins(
        context,
        f"🚫 Учасника @{user.username or '?'} (id {user.id}) обмежено після "
        f"{count} порушень за останні {VIOLATION_WINDOW_SECONDS // 60} хв. "
        f"Перевірте вручну: /unmute {user.id}",
    )


async def on_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Auto-kick any bot account that joins without being explicitly
    whitelisted via /allowbot. Can't detect human-operated "userbot" scraper
    accounts — Telegram doesn't expose that distinction to the Bot API — this
    only stops other *bot* accounts an attacker might add to scrape the chat."""
    cmu = update.chat_member
    new, old = cmu.new_chat_member, cmu.old_chat_member
    user = new.user

    if user.id == context.bot.id or not user.is_bot:
        return

    just_joined = (
        new.status in ("member", "administrator", "restricted")
        and old.status in ("left", "kicked")
    )
    if not just_joined or user.id in TRUSTED_BOT_IDS:
        return

    chat_id = cmu.chat.id
    try:
        await context.bot.ban_chat_member(chat_id, user.id)
    except Exception:
        log.exception("failed to kick unauthorized bot %s from chat %s", user.id, chat_id)
        return

    log.info("kicked unauthorized bot %s from chat %s", user.id, chat_id)
    await _notify_admins(
        context,
        f"🤖⛔ Видалено неавторизований бот із групи: "
        f"@{user.username or '?'} (id {user.id}). "
        f"Якщо це довірений бот — /allowbot {user.id}, потім додай його знову.",
    )


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
    log.info(
        "deleted text in chat %s after %.2fs exposure: %s",
        update.effective_chat.id, _exposure_seconds(msg), verdict.reason,
    )
    await _warn(update)
    await _track_violation(update, context)


async def on_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    try:
        await msg.delete()
    except Exception:
        log.exception("failed to delete location message")
        return
    log.info(
        "deleted live location in chat %s after %.2fs exposure",
        update.effective_chat.id, _exposure_seconds(msg),
    )
    await _warn(update)
    await _track_violation(update, context)


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
    log.info(
        "blurred photo in chat %s after %.2fs exposure: %s",
        update.effective_chat.id, _exposure_seconds(msg), verdict.reason,
    )
    await _track_violation(update, context)


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
        await _track_violation(update, context)
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
    log.info(
        "processed video in chat %s after %.2fs exposure: %s",
        update.effective_chat.id, _exposure_seconds(msg), verdict.reason,
    )
    await _track_violation(update, context)
