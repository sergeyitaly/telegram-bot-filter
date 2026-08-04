"""Telegram update handlers."""
import asyncio
import logging
import os
import secrets
import tempfile
from datetime import datetime, timezone

from telegram import ChatPermissions, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import ApplicationHandlerStop, ContextTypes

from bot import filters as classify
from bot import keywords, media, state
from bot.config import (
    ALLOWED_CHAT_IDS,
    CHAT_ADMINS,
    CLAIM_TIMEOUT_SECONDS,
    INVITE_TOKENS,
    MAX_VIDEO_MB,
    OWNER_IDS,
    POST_ALARM_GRACE_SECONDS,
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


def _admins_for(chat_id: int) -> set[int]:
    """Owners are admin everywhere; each chat also has its own admin set —
    either hardcoded via CHAT_ADMINS, or self-registered via /claim."""
    return CHAT_ADMINS.get(chat_id, set()) | state.claimed_admins_for(chat_id) | OWNER_IDS


def _is_admin(chat_id: int, user_id: int) -> bool:
    return user_id in _admins_for(chat_id)


def _is_allowed_chat(chat_id: int) -> bool:
    return chat_id in ALLOWED_CHAT_IDS or state.is_claimed(chat_id)


def _is_claim_command(update: Update) -> bool:
    msg = update.effective_message
    if not msg or not msg.text:
        return False
    return msg.text.split()[0].split("@")[0].lower() == "/claim"


def _strict_mode(chat_id: int, st) -> bool:
    """Alarm active, or still within the wind-down window after it ended —
    keyword filtering (not coordinates, those are unconditional) only
    applies during this window, so a chat isn't permanently barred from
    ever discussing a past strike."""
    return st.alarm_active or state.in_post_alarm_grace(chat_id, POST_ALARM_GRACE_SECONDS)


async def guard_allowed_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Registered in an earlier handler group than everything else: drops any
    update from a non-private chat outside the allowlist before it reaches
    filter/command logic — except /claim, which is how a chat gets onto the
    allowlist in the first place. Belt-and-suspenders alongside the
    auto-leave-if-unclaimed job, for the gap before that job fires."""
    chat = update.effective_chat
    if not chat or chat.type == "private":
        return
    if _is_allowed_chat(chat.id) or _is_claim_command(update):
        return
    raise ApplicationHandlerStop


def _exposure_seconds(msg) -> float:
    """How long the message was visible to chat members before we deleted it."""
    return (datetime.now(timezone.utc) - msg.date).total_seconds()


async def _notify_admins(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str) -> None:
    """DM only THIS chat's admins (+ owners), never the group. One bot can
    serve several unrelated groups, so a group's admins must never see
    another group's alarm activity."""
    for admin_id in _admins_for(chat_id):
        try:
            await context.bot.send_message(chat_id=admin_id, text=text)
        except Exception:
            log.warning("could not DM admin %s (they may need to /start the bot first)", admin_id)


async def _notify_owners(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """For bot-wide events that aren't scoped to any one chat (e.g. someone
    adding this shared bot to an unauthorized group) — owners only, since
    per-chat admins have no stake in chats other than their own."""
    for owner_id in OWNER_IDS:
        try:
            await context.bot.send_message(chat_id=owner_id, text=text)
        except Exception:
            log.warning("could not DM owner %s (they may need to /start the bot first)", owner_id)


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
    chat_id = update.effective_chat.id
    if not _is_admin(chat_id, update.effective_user.id):
        return
    await _delete_silently(update.effective_message)
    st = state.get(chat_id)
    await _notify_admins(context, chat_id, f"Alarm mode: {'ON' if st.alarm_active else 'off'}")


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
        context, chat_id,
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
    await _notify_admins(context, chat_id, f"✅ Alarm mode off ({source})." + restore_note)


async def cmd_alarm_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_chat.id, update.effective_user.id):
        return
    await _delete_silently(update.effective_message)
    await activate_alarm(context, update.effective_chat.id, auto=False)


async def cmd_alarm_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_chat.id, update.effective_user.id):
        return
    await _delete_silently(update.effective_message)
    await deactivate_alarm(context, update.effective_chat.id, auto=False)


async def cmd_addkeyword(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_chat.id, update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /addkeyword <термін> [location]")
        return
    tier = "location" if context.args[-1] == "location" else "strike"
    term = " ".join(context.args[:-1] if tier == "location" else context.args)
    keywords.add_term(term, tier)
    await update.message.reply_text(
        f"Added \"{term}\" to {tier} keywords. Note: this list is shared across "
        "every chat this bot serves, not just yours."
    )


async def cmd_allowbot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Owners only, not per-chat admins: this whitelists a bot across every
    # chat the deployment serves, not just the one the command was run in.
    if update.effective_user.id not in OWNER_IDS:
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /allowbot <bot_id>")
        return
    bot_id = int(context.args[0])
    TRUSTED_BOT_IDS.add(bot_id)
    await update.message.reply_text(f"Bot {bot_id} whitelisted — add it to the group now.")


async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not _is_admin(chat_id, update.effective_user.id):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /unmute <user_id>")
        return
    user_id = int(context.args[0])
    try:
        chat = await context.bot.get_chat(chat_id)
        await context.bot.restrict_chat_member(
            chat_id, user_id, permissions=chat.permissions, use_independent_chat_permissions=True
        )
    except BadRequest as exc:
        await update.message.reply_text(f"Failed: {exc.message}")
        return
    await update.message.reply_text(f"User {user_id} unmuted.")


async def cmd_claim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Self-service activation: requires BOTH a valid invite token AND that
    the caller is verified (via the Telegram API, not their say-so) as an
    actual admin/creator of this specific chat — a token alone isn't enough
    to activate the bot in a chat you don't administer."""
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private" or not state.is_pending_claim(chat_id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /claim <token>")
        return

    token = context.args[0]
    if not any(secrets.compare_digest(token, valid) for valid in INVITE_TOKENS):
        await update.message.reply_text("❌ Invalid token.")
        return

    member = await context.bot.get_chat_member(chat_id, update.effective_user.id)
    if member.status not in ("administrator", "creator"):
        await update.message.reply_text("❌ Only a Telegram admin of this group can claim the bot.")
        return

    state.add_chat_admin(chat_id, update.effective_user.id)
    state.clear_pending_claim(chat_id)
    state.register_chat(chat_id)
    await update.message.reply_text(
        "✅ Activated. Alarm mode, filtering, and admin commands are now live "
        "for this group. Add co-admins with /addadmin <user_id>."
    )
    await _notify_owners(
        context,
        f"✅ Chat {chat_id} ({update.effective_chat.title}) self-claimed by "
        f"@{update.effective_user.username or '?'} (id {update.effective_user.id}).",
    )


async def cmd_addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not _is_admin(chat_id, update.effective_user.id):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /addadmin <user_id>")
        return
    state.add_chat_admin(chat_id, int(context.args[0]))
    await update.message.reply_text(f"User {context.args[0]} can now run admin commands in this chat.")


async def _leave_if_unclaimed(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.data
    if not state.is_pending_claim(chat_id):
        return  # claimed (or already left) in the meantime
    state.clear_pending_claim(chat_id)
    try:
        await context.bot.leave_chat(chat_id)
    except Exception:
        log.exception("failed to leave unclaimed chat %s", chat_id)
    await _notify_owners(context, f"⏱️ Chat {chat_id} was never claimed within {CLAIM_TIMEOUT_SECONDS}s — left.")


async def on_my_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Track which chats the bot is currently a member of, so the
    alerts.in.ua poller knows where to (de)activate alarm mode. Also gates
    onboarding: this is one deployed bot on one token, so anyone could
    otherwise add it to their own chat to see how the filter behaves. A newly
    added chat gets a claim window instead of being served (or left)
    immediately — see cmd_claim."""
    cmu = update.my_chat_member
    chat_id = cmu.chat.id

    if cmu.new_chat_member.status in ("left", "kicked"):
        state.unregister_chat(chat_id)
        state.clear_pending_claim(chat_id)
        return

    if _is_allowed_chat(chat_id):
        state.register_chat(chat_id)
        return

    if not INVITE_TOKENS:
        # No self-service configured at all — fall back to immediate leave.
        adder = cmu.from_user
        try:
            await context.bot.leave_chat(chat_id)
        except Exception:
            log.exception("failed to leave unauthorized chat %s", chat_id)
        await _notify_owners(
            context,
            "🔒 Bot was added to an unauthorized chat and left automatically "
            "(no INVITE_TOKENS configured, so self-claim is off).\n"
            f"Chat: {cmu.chat.title or cmu.chat.type} (id {chat_id})\n"
            f"Added by: @{adder.username or '?'} (id {adder.id})",
        )
        return

    state.mark_pending_claim(chat_id)
    try:
        await context.bot.send_message(
            chat_id,
            "🔒 This bot needs to be activated before it will do anything here.\n"
            f"A Telegram admin of this group must run /claim <token> within "
            f"{CLAIM_TIMEOUT_SECONDS // 60} minutes, or the bot leaves automatically.",
        )
    except Exception:
        log.exception("failed to post claim instructions in chat %s", chat_id)
    context.job_queue.run_once(_leave_if_unclaimed, when=CLAIM_TIMEOUT_SECONDS, data=chat_id)


async def _track_violation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Repeat offenders (deliberate or careless insiders) get auto-muted
    pending admin review, instead of just having each message deleted."""
    user = update.effective_message.from_user
    chat_id = update.effective_chat.id
    if user is None or _is_admin(chat_id, user.id):
        return
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
        context, chat_id,
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
        context, chat_id,
        f"🤖⛔ Видалено неавторизований бот із групи: "
        f"@{user.username or '?'} (id {user.id}). "
        f"Якщо це довірений бот — /allowbot {user.id}, потім додай його знову.",
    )


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    chat_id = update.effective_chat.id
    st = state.get(chat_id)
    verdict = classify.classify_text(msg.text, _strict_mode(chat_id, st))
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
    chat_id = update.effective_chat.id
    st = state.get(chat_id)
    verdict = classify.classify_media(msg.caption or "", st.alarm_active, _strict_mode(chat_id, st))
    if not verdict.flagged:
        return

    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.UPLOAD_PHOTO)
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "in.jpg")
        dst = os.path.join(tmp, "out.jpg")
        photo = msg.photo[-1]
        tg_file = await photo.get_file()
        await tg_file.download_to_drive(src)
        await asyncio.to_thread(media.blur_photo, src, dst)

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
    chat_id = update.effective_chat.id
    st = state.get(chat_id)
    verdict = classify.classify_media(msg.caption or "", st.alarm_active, _strict_mode(chat_id, st))
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
        blurred_ok = await media.blur_video(src, dst)

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
