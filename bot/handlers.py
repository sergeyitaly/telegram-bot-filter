"""Telegram update handlers."""
import asyncio
import logging
import os
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
    AUTO_KICK_ON_REPORT_THRESHOLD,
    CHAT_ADMINS,
    MAX_VIDEO_MB,
    OWNER_IDS,
    POST_ALARM_GRACE_SECONDS,
    REPORT_VIOLATION_THRESHOLD,
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
    either hardcoded via CHAT_ADMINS, or self-registered on add/via /addadmin."""
    return CHAT_ADMINS.get(chat_id, set()) | state.claimed_admins_for(chat_id) | OWNER_IDS


def _is_admin(chat_id: int, user_id: int) -> bool:
    return user_id in _admins_for(chat_id)


def _is_allowed_chat(chat_id: int) -> bool:
    return chat_id in ALLOWED_CHAT_IDS or state.is_claimed(chat_id)


def _is_activate_command(update: Update) -> bool:
    msg = update.effective_message
    if not msg or not msg.text:
        return False
    return msg.text.split()[0].split("@")[0].lower() == "/activate"


def _strict_mode(chat_id: int, st) -> bool:
    """Alarm active, or still within the wind-down window after it ended —
    keyword filtering (not coordinates, those are unconditional) only
    applies during this window, so a chat isn't permanently barred from
    ever discussing a past strike."""
    return st.alarm_active or state.in_post_alarm_grace(chat_id, POST_ALARM_GRACE_SECONDS)


async def guard_allowed_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Registered in an earlier handler group than everything else: drops any
    update from a non-private chat outside the allowlist before it reaches
    filter/command logic — except /activate, the recovery path if this
    chat's registration was lost (e.g. a restart) and needs re-establishing."""
    chat = update.effective_chat
    if not chat or chat.type == "private":
        return
    if _is_allowed_chat(chat.id) or _is_activate_command(update):
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


async def _announce(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str) -> None:
    """Post directly in the group, for the one thing every member should see:
    whether alarm mode is currently on. This isn't sensitive — the underlying
    fact (an active air-raid alert) is already public via official apps and
    sirens, and it's the reason members' photos/videos are being blocked
    right now, so hiding it just confuses people. Contrast with
    _notify_admins: violation/claim/bot-kick details stay admin-only, those
    are moderation mechanics, not a public status."""
    try:
        await context.bot.send_message(chat_id, text)
    except Exception:
        log.exception("failed to announce in chat %s", chat_id)


async def notify_owners(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
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
    await state.set_alarm(chat_id, True, auto=auto)

    lockdown_note = ""
    try:
        chat = await context.bot.get_chat(chat_id)
        original = chat.permissions
        await state.set_saved_permissions(chat_id, original)
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
            f"⚠️ Alarm ON у чаті {chat_id}, але не вдалось заблокувати медіа "
            f"({exc.message}). Ймовірно, базова група — оновіть до supergroup. "
            "Реактивне видалення за ключовими словами й надалі працює."
        )

    await _announce(
        context, chat_id,
        "🚨 Тривога: увімкнено — фото/відео від учасників заблоковано, "
        "текст фільтрується."
    )
    if lockdown_note:
        await _notify_admins(context, chat_id, lockdown_note)


async def deactivate_alarm(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    await state.set_alarm(chat_id, False)

    st = state.get(chat_id)
    restore_note = ""
    if st.saved_permissions is not None:
        try:
            await context.bot.set_chat_permissions(
                chat_id, st.saved_permissions, use_independent_chat_permissions=True
            )
        except BadRequest as exc:
            log.warning("could not restore permissions in chat %s: %s", chat_id, exc)
            restore_note = f"⚠️ Alarm OFF у чаті {chat_id}, але не вдалось відновити права чату ({exc.message}) — перевірте вручну."
        await state.set_saved_permissions(chat_id, None)

    await _announce(context, chat_id, "✅ Тривога: відбій.")
    if restore_note:
        await _notify_admins(context, chat_id, restore_note)


async def cmd_alarm_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_chat.id, update.effective_user.id):
        return
    await _delete_silently(update.effective_message)
    await activate_alarm(context, update.effective_chat.id, auto=False)


async def cmd_alarm_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_chat.id, update.effective_user.id):
        return
    await _delete_silently(update.effective_message)
    await deactivate_alarm(context, update.effective_chat.id)


async def cmd_addkeyword(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_chat.id, update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /addkeyword <термін> [location]")
        return
    tier = "location" if context.args[-1] == "location" else "strike"
    term = " ".join(context.args[:-1] if tier == "location" else context.args)
    await keywords.add_term(term, tier)
    await update.message.reply_text(
        f"Added \"{term}\" to {tier} keywords. Note: this list is shared across "
        "every chat this bot serves, not just yours."
    )


async def cmd_listkeywords(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only, DM'd to the caller only (not broadcast to every owner,
    not visible to chat admins): the exact keyword list is what makes the
    filter hard to trivially evade by rephrasing around it. Exposing it to
    every self-service chat admin — a much wider trust boundary than the
    deployer alone — would defeat that."""
    if update.effective_user.id not in OWNER_IDS:
        return
    if update.effective_chat.type != "private":
        await _delete_silently(update.effective_message)

    strike = "\n".join(f"• {t}" for t in keywords.STRIKE_TERMS)
    location = "\n".join(f"• {t}" for t in keywords.LOCATION_TERMS)
    text = (
        f"Strike terms ({len(keywords.STRIKE_TERMS)}):\n{strike}\n\n"
        f"Location terms ({len(keywords.LOCATION_TERMS)}):\n{location}"
    )
    try:
        await context.bot.send_message(chat_id=update.effective_user.id, text=text)
    except Exception:
        log.exception("failed to DM keyword list to owner %s", update.effective_user.id)


async def cmd_mychats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only, DM'd to the caller only: every chat this deployment is
    currently authorized in — env-hardcoded CHAT_ADMINS plus self-service
    claimed chats. Best-effort per chat: one being unreachable (e.g. the
    bot was removed outside its own auto-leave path) doesn't hide the rest."""
    if update.effective_user.id not in OWNER_IDS:
        return
    if update.effective_chat.type != "private":
        await _delete_silently(update.effective_message)

    chat_ids = sorted(ALLOWED_CHAT_IDS | state.all_claimed_chat_ids())
    if not chat_ids:
        await context.bot.send_message(chat_id=update.effective_user.id, text="No authorized chats yet.")
        return

    lines = []
    for chat_id in chat_ids:
        admins = _admins_for(chat_id) - OWNER_IDS
        source = "CHAT_ADMINS" if chat_id in ALLOWED_CHAT_IDS else "self-service"
        try:
            chat = await context.bot.get_chat(chat_id)
            title = chat.title or chat.type
        except Exception:
            title = "⚠️ unreachable (bot may have been removed)"
        lines.append(f"{title} (id {chat_id})\n  source: {source}, admins: {sorted(admins) or 'none'}")

    text = f"{len(chat_ids)} authorized chat(s):\n\n" + "\n".join(lines)
    try:
        for chunk_start in range(0, len(text), 3500):
            await context.bot.send_message(
                chat_id=update.effective_user.id, text=text[chunk_start:chunk_start + 3500]
            )
    except Exception:
        log.exception("failed to DM chat list to owner %s", update.effective_user.id)


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


def _format_violation_entry(e: dict) -> str:
    when = datetime.fromtimestamp(e["ts"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"{when} — @{e.get('username') or '?'} (id {e['user_id']}) — {e['reason']}\n  \"{e['text']}\""


def _format_violation_report(entries: list[dict], user_id: int | None) -> str:
    if not entries:
        return "No logged violations for this chat" + (f" / user {user_id}." if user_id else ".")

    shown = entries[-50:]  # most recent 50, avoid an unbounded message
    header = f"{len(entries)} total entr{'y' if len(entries) == 1 else 'ies'}"
    if len(entries) > 50:
        header += " (showing most recent 50)"
    body = "\n".join(_format_violation_entry(e) for e in shown)
    return f"{header}:\n\n{body}"


async def cmd_violations(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Chat-admin only, DM'd to the caller only. This is an audit trail for
    a human to review and, if warranted, escalate outside the bot (e.g. to
    police) — the bot never makes that call itself. /violations [user_id]:
    all recent entries, or just one user's."""
    chat_id = update.effective_chat.id
    if not _is_admin(chat_id, update.effective_user.id):
        return
    if update.effective_chat.type != "private":
        await _delete_silently(update.effective_message)

    user_id = int(context.args[0]) if context.args and context.args[0].isdigit() else None
    entries = await state.get_violation_log(chat_id, user_id)
    text = _format_violation_report(entries, user_id)

    try:
        for chunk_start in range(0, len(text), 3500):
            await context.bot.send_message(
                chat_id=update.effective_user.id, text=text[chunk_start:chunk_start + 3500]
            )
    except Exception:
        log.exception("failed to DM violation log to %s", update.effective_user.id)


async def _register_chat_admin(context: ContextTypes.DEFAULT_TYPE, chat, user) -> None:
    await state.add_chat_admin(chat.id, user.id)
    state.register_chat(chat.id)
    await _announce(
        context, chat.id,
        "✅ Bot activated for this group. Alarm mode and content filtering are now live."
    )
    await notify_owners(
        context,
        f"✅ Chat {chat.id} ({chat.title}) auto-activated — added by verified admin "
        f"@{user.username or '?'} (id {user.id}).",
    )


async def cmd_activate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Recovery path: re-establish this chat's registration if it was lost
    (e.g. a bot restart cleared in-memory state). Same verified-admin check
    as the automatic on-add flow, no token — being a real Telegram
    admin/creator of *this* chat is the only requirement."""
    chat_id = update.effective_chat.id
    if update.effective_chat.type == "private" or _is_allowed_chat(chat_id):
        return
    member = await context.bot.get_chat_member(chat_id, update.effective_user.id)
    if member.status not in ("administrator", "creator"):
        return
    await _register_chat_admin(context, update.effective_chat, update.effective_user)


async def cmd_addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not _is_admin(chat_id, update.effective_user.id):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /addadmin <user_id>")
        return
    await state.add_chat_admin(chat_id, int(context.args[0]))
    await update.message.reply_text(f"User {context.args[0]} can now run admin commands in this chat.")


async def on_my_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Track which chats the bot is currently a member of, so the
    alerts.in.ua poller knows where to (de)activate alarm mode. Also gates
    onboarding: this is one deployed bot on one token, so anyone could
    otherwise add it to their own chat to see how the filter behaves. Auto-
    registers immediately if the Telegram API confirms whoever added it is
    an actual admin/creator of that chat; otherwise leaves right away."""
    cmu = update.my_chat_member
    chat_id = cmu.chat.id

    if cmu.new_chat_member.status in ("left", "kicked"):
        state.unregister_chat(chat_id)
        return

    if _is_allowed_chat(chat_id):
        state.register_chat(chat_id)
        return

    adder = cmu.from_user
    member = await context.bot.get_chat_member(chat_id, adder.id)
    if member.status in ("administrator", "creator"):
        await _register_chat_admin(context, cmu.chat, adder)
        return

    try:
        await context.bot.leave_chat(chat_id)
    except Exception:
        log.exception("failed to leave unauthorized chat %s", chat_id)
    await notify_owners(
        context,
        "🔒 Bot was added by someone who isn't an admin of that chat — left automatically.\n"
        f"Chat: {cmu.chat.title or cmu.chat.type} (id {chat_id})\n"
        f"Added by: @{adder.username or '?'} (id {adder.id})",
    )


async def _kick_repeat_offender(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user, total: int) -> None:
    """Remove (not ban — they can rejoin via invite link) a user whose
    all-time violation count crossed REPORT_VIOLATION_THRESHOLD, and DM them
    why. DM'ing first since it may well fail silently (most members have
    never started a chat with the bot) — the removal itself doesn't depend
    on that succeeding."""
    try:
        await context.bot.send_message(
            chat_id=user.id,
            text=(
                "⛔ Тебе видалено з групи автоматично.\n\n"
                f"Причина: {total} зафіксованих випадків публікації вмісту, "
                "що стосується наслідків ударів/тривог (координати, адреси, фото/відео "
                "з місць ударів), який автоматично блокується для безпеки учасників.\n\n"
                "Якщо вважаєш це помилкою — зв'яжись з адміністрацією групи."
            ),
        )
    except Exception:
        log.info("could not DM removed user %s (likely never started the bot)", user.id)

    try:
        await context.bot.ban_chat_member(chat_id, user.id)
        await context.bot.unban_chat_member(chat_id, user.id, only_if_banned=True)
    except Exception:
        log.exception("failed to kick repeat offender %s from chat %s", user.id, chat_id)
        return

    await _notify_admins(
        context, chat_id,
        f"⛔ Учасника @{user.username or '?'} (id {user.id}) видалено з групи "
        f"після {total} зафіксованих порушень за весь час. Може повернутись "
        f"за посиланням-запрошенням. Деталі: /violations {user.id}",
    )


async def _track_violation(
    update: Update, context: ContextTypes.DEFAULT_TYPE, reason: str, text: str = ""
) -> None:
    """Two independent mechanisms on every flagged message:
    1. Auto-mute on a short-window burst (existing) — fast reaction to
       someone actively spamming right now.
    2. A durable audit-log entry (text + metadata, no media — see
       state.log_violation) plus a periodic admin notice once the user's
       ALL-TIME count in this chat crosses a multiple of
       REPORT_VIOLATION_THRESHOLD — for a pattern across many separate
       alarms, e.g. a suspected spotter who never triggers the burst
       threshold but keeps doing it every single alert. The bot never
       decides anything here; it just gives the admin something to
       review via /violations and escalate (e.g. to police) if warranted.
    """
    user = update.effective_message.from_user
    chat_id = update.effective_chat.id
    if user is None or _is_admin(chat_id, user.id):
        return

    await state.log_violation(chat_id, user.id, user.username, reason, text)
    total = len(await state.get_violation_log(chat_id, user.id))
    if total and total % REPORT_VIOLATION_THRESHOLD == 0:
        if AUTO_KICK_ON_REPORT_THRESHOLD:
            await _kick_repeat_offender(context, chat_id, user, total)
        else:
            await _notify_admins(
                context, chat_id,
                f"📋 Учасник @{user.username or '?'} (id {user.id}) має вже {total} "
                f"зафіксованих порушень у цьому чаті за весь час. "
                f"Перевірте: /violations {user.id}",
            )

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
    await _track_violation(update, context, verdict.reason, msg.text or "")


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
    loc = msg.location
    loc_text = f"[live location: {loc.latitude},{loc.longitude}]" if loc else "[live location]"
    await _track_violation(update, context, "live location shared", loc_text)


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
    await _track_violation(update, context, verdict.reason, msg.caption or "[photo, no caption]")


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
        await _track_violation(update, context, "oversized video, deleted unblurred", msg.caption or "")
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
    await _track_violation(update, context, verdict.reason, msg.caption or "[video, no caption]")
