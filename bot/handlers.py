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


async def notify_all_admins(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """DM owners plus every chat's admins — for operational issues (Redis
    down, the bot falling behind on processing) that affect every group this
    deployment serves at once, unlike _notify_admins which scopes to one
    chat's own admins. One user gets one DM even if they admin several chats."""
    all_ids: set[int] = set(OWNER_IDS)
    for chat_id in set(CHAT_ADMINS.keys()) | state.all_claimed_chat_ids():
        all_ids |= _admins_for(chat_id)
    for admin_id in all_ids:
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
        # Invariant: saved_permissions is non-None iff we're currently holding
        # a lockdown. Gating capture on that value (not on alarm_active, and
        # not on whether this looks like the "first" call) means a redundant
        # activation — double /alarm_on, a race with the auto-poller, anything
        # a future refactor might add — can never re-read "current" (by then
        # already-locked) permissions and clobber the true original. Only
        # deactivate_alarm is allowed to clear it back to None.
        original = state.get(chat_id).saved_permissions
        if original is None:
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
        else:
            # Only clear the saved original once it's actually been restored.
            # Clearing it unconditionally (even on failure) loses the true
            # original the same way the activate_alarm bug did: the state
            # would show no lockdown while the live chat is still locked, and
            # nothing would be left to retry the restore with.
            await state.set_saved_permissions(chat_id, None)

    await _announce(context, chat_id, "✅ Тривога: відбій.")
    if restore_note:
        await _notify_admins(context, chat_id, restore_note)


async def reapply_lockdown(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    """Re-enforce media lockdown permissions for a chat that is currently in
    alarm mode. Called each air-alert poll tick to undo any permission changes
    a native Telegram admin may have silently applied over the bot's lockdown."""
    st = state.get(chat_id)
    if not st.alarm_active:
        return
    base = st.saved_permissions
    try:
        await context.bot.set_chat_permissions(
            chat_id,
            ChatPermissions(
                can_send_messages=base.can_send_messages if base else True,
                can_send_polls=base.can_send_polls if base else False,
                can_add_web_page_previews=base.can_add_web_page_previews if base else False,
                can_change_info=base.can_change_info if base else False,
                can_invite_users=base.can_invite_users if base else False,
                can_pin_messages=base.can_pin_messages if base else False,
                can_manage_topics=base.can_manage_topics if base else False,
                **_LOCKDOWN_PERMS,
            ),
            use_independent_chat_permissions=True,
        )
    except Exception:
        log.debug("could not re-apply lockdown in chat %s", chat_id)


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


async def cmd_removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove a previously-added claimed admin. Global owners can only be
    revoked by other owners, not by per-chat admins."""
    chat_id = update.effective_chat.id
    if not _is_admin(chat_id, update.effective_user.id):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /removeadmin <user_id>")
        return
    target_id = int(context.args[0])
    if target_id in OWNER_IDS and update.effective_user.id not in OWNER_IDS:
        await update.message.reply_text("Global owners can only be removed by other owners.")
        return
    await state.remove_chat_admin(chat_id, target_id)
    await update.message.reply_text(f"Admin {target_id} removed from this chat.")


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
    1. Auto-mute on a short-window burst — fast reaction to active spamming.
    2. A durable audit-log entry plus a periodic admin notice once the
       user's ALL-TIME count crosses a multiple of REPORT_VIOLATION_THRESHOLD.

    Admins are exempt from auto-delete and auto-mute, but their flagged
    content is still audit-logged and the chat's other admins are notified —
    an admin account is a higher-value target for social engineering.
    """
    user = update.effective_message.from_user
    chat_id = update.effective_chat.id
    if user is None:
        return
    if _is_admin(chat_id, user.id):
        log.warning("admin %s posted flagged content in chat %s: %s", user.id, chat_id, reason)
        await _notify_admins(
            context, chat_id,
            f"⚠️ Адмін @{user.username or '?'} (id {user.id}) надіслав вміст, "
            f"що відповідає фільтру ({reason}):\n\"{text[:200]}\"\n"
            f"Адміни звільнені від автовидалення, але це зафіксовано.",
        )
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
    # Forwarded messages: PTB delivers message.text / message.photo / etc. for
    # the FORWARDED CONTENT, not the forwarder's own text.  forward_origin is
    # just metadata about the source.  No special handling needed — the same
    # handler catches both original and forwarded messages transparently.
    msg = update.effective_message
    chat_id = update.effective_chat.id
    st = state.get(chat_id)
    strict = _strict_mode(chat_id, st)
    verdict = classify.classify_text(msg.text, strict, alarm_active=st.alarm_active)

    # Sliding context window: re-classify with recent messages from the same
    # user to catch coordinates or keywords split across 2-3 messages.
    if not verdict.flagged and strict and msg.from_user:
        ctx = state.get_user_context(chat_id, msg.from_user.id, msg.text or "")
        if len(ctx) > len(msg.text or ""):
            ctx_verdict = classify.classify_text(ctx, strict, alarm_active=st.alarm_active)
            if ctx_verdict.flagged:
                verdict = classify.Verdict(True, ctx_verdict.reason + " (split across messages)")

    if not verdict.flagged:
        # Still record in buffer even for clean messages, so future messages
        # have context — but only in strict mode to avoid unbounded growth.
        if strict and msg.from_user:
            state.get_user_context(chat_id, msg.from_user.id, msg.text or "")
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


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Voice/audio messages.

    During active alarm: delete unconditionally.
    During grace period: transcribe with faster-whisper (if installed) and
    classify the transcript; fall back to admin-notify if not available.
    """
    msg = update.effective_message
    chat_id = update.effective_chat.id
    st = state.get(chat_id)

    if st.alarm_active:
        try:
            await msg.delete()
        except Exception:
            log.exception("failed to delete voice/audio during alarm")
            return
        log.info("deleted voice/audio in chat %s during alarm", chat_id)
        await _warn(update)
        await _track_violation(update, context, "voice/audio during active alarm", "[audio]")
        return

    if not _strict_mode(chat_id, st):
        return

    # Grace period — try to transcribe and classify.
    transcript = ""
    voice_or_audio = msg.voice or msg.audio
    if voice_or_audio:
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "voice.ogg")
            try:
                tg_file = await voice_or_audio.get_file()
                await tg_file.download_to_drive(src)
                transcript = await asyncio.to_thread(media.transcribe_voice, src)
            except Exception:
                log.debug("could not download voice for transcription in chat %s", chat_id)

    if transcript:
        verdict = classify.classify_text(transcript, strict=True)
        if verdict.flagged:
            try:
                await msg.delete()
            except Exception:
                log.exception("failed to delete voice after transcription flag")
                return
            await _warn(update)
            await _track_violation(update, context, f"voice: {verdict.reason}",
                                   transcript[:200])
            return

    username = msg.from_user.username if msg.from_user else None
    note = " — транскрипцію перевірено, порушень не виявлено" if transcript \
        else " — автотранскрипція недоступна (faster-whisper не встановлено)"
    await _notify_admins(
        context, chat_id,
        f"🎙 @{username or '?'} надіслав голосове/аудіо під час вікна "
        f"фільтрації{note}. Перевірте вручну.",
    )


async def on_alarm_catchall(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Deny-by-default catch-all registered in handler group 1 (runs after all
    specific handlers). Deletes any message type that has no explicit handler
    while alarm mode is active, so adding a new Telegram message type cannot
    silently create a bypass."""
    msg = update.effective_message
    chat_id = update.effective_chat.id
    st = state.get(chat_id)
    if not st.alarm_active:
        return
    try:
        await msg.delete()
    except Exception:
        log.warning("catch-all: could not delete unhandled type in chat %s", chat_id)
        return
    log.info(
        "catch-all deleted unhandled message type in chat %s after %.2fs",
        chat_id, _exposure_seconds(msg),
    )
    await _warn(update)
    caption = getattr(msg, "caption", "") or ""
    await _track_violation(update, context, "unhandled message type during alarm",
                           caption or "[no text]")


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
    strict = _strict_mode(chat_id, st)
    verdict = classify.classify_media(msg.caption or "", st.alarm_active, strict)

    # Not flagged by caption/alarm and not in strict mode — nothing to do.
    if not verdict.flagged and not strict:
        return

    # Rate limit: if this user is flooding photos, skip OCR/blur and just delete.
    # Prevents a 1000-photo flood from swamping the processing pipeline.
    if msg.from_user and not state.check_media_rate(chat_id, msg.from_user.id):
        if verdict.flagged:
            try:
                await msg.delete()
            except Exception:
                pass
            await _warn(update)
            await _track_violation(update, context, "photo flood (rate limited)", "[rate limited]")
        return

    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "in.jpg")
        dst = os.path.join(tmp, "out.jpg")
        tg_file = await msg.photo[-1].get_file()
        await tg_file.download_to_drive(src)

        # OCR: check for screenshotted text only when caption didn't already flag it.
        if not verdict.flagged:
            ocr_text = classify.ocr_image(src)
            if ocr_text:
                verdict = classify.classify_text(ocr_text, strict)

        if not verdict.flagged:
            return

        await asyncio.to_thread(media.blur_photo, src, dst)

        # Delete BEFORE repost so a crash after blur never leaves the original visible.
        try:
            await msg.delete()
        except Exception:
            log.exception("failed to delete photo message")
            return

        await context.bot.send_photo(
            chat_id=chat_id,
            photo=open(dst, "rb"),
            caption=f"🔒 Фото заблюрено ({verdict.reason}).\n{WARNING_TEXT}",
        )
    log.info(
        "blurred photo in chat %s after %.2fs exposure: %s",
        chat_id, _exposure_seconds(msg), verdict.reason,
    )
    await _track_violation(update, context, verdict.reason, msg.caption or "[photo, no caption]")


def _document_kind(document) -> str:
    """'photo'/'video' for an image/video sent uncompressed as a file (the
    two-tap way to skip client-side compression, and — before this handler
    existed — skip this bot's filtering entirely); 'other' otherwise."""
    mime = (document.mime_type or "").lower()
    name = (document.file_name or "").lower()
    if mime.startswith("image/") or name.endswith((".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic")):
        return "photo"
    if mime.startswith("video/") or name.endswith((".mp4", ".mov", ".mkv", ".avi", ".webm")):
        return "video"
    return "other"


async def _delete_flagged_document(
    update: Update, context: ContextTypes.DEFAULT_TYPE, verdict, caption_or_name: str, log_msg: str
) -> None:
    """No way to blur an arbitrary file or an oversized one — delete outright."""
    msg = update.effective_message
    try:
        await msg.delete()
    except Exception:
        log.exception("failed to delete document")
        return
    await _warn(update)
    log.info(log_msg)
    await _track_violation(update, context, verdict.reason, caption_or_name)


async def _blur_flagged_document(
    update: Update, context: ContextTypes.DEFAULT_TYPE, doc, kind: str, verdict, caption_or_name: str
) -> None:
    msg = update.effective_message
    chat_id = update.effective_chat.id
    action = ChatAction.UPLOAD_PHOTO if kind == "photo" else ChatAction.UPLOAD_VIDEO
    await context.bot.send_chat_action(chat_id, action)
    ext = "jpg" if kind == "photo" else "mp4"
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, f"in.{ext}")
        dst = os.path.join(tmp, f"out.{ext}")
        tg_file = await doc.get_file()
        await tg_file.download_to_drive(src)

        # Delete BEFORE blur so a crash during processing never leaves the original visible.
        try:
            await msg.delete()
        except Exception:
            log.exception("failed to delete flagged document")
            return

        if kind == "photo":
            await asyncio.to_thread(media.blur_photo, src, dst)
            blurred_ok = True
        else:
            blurred_ok = await media.blur_video(src, dst)

        if blurred_ok:
            sender = context.bot.send_photo if kind == "photo" else context.bot.send_video
            file_kw = "photo" if kind == "photo" else "video"
            await sender(
                chat_id=chat_id,
                **{file_kw: open(dst, "rb")},
                caption=f"🔒 Заблюрено ({verdict.reason}), надіслано як файл.\n{WARNING_TEXT}",
            )
        else:
            await _warn(update)
    log.info(
        "processed %s-document in chat %s after %.2fs exposure: %s",
        kind, chat_id, _exposure_seconds(msg), verdict.reason,
    )
    await _track_violation(update, context, verdict.reason, caption_or_name or f"[{kind} document, no caption]")


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A document is Telegram's "send without compression" path — the same
    photo/video content, just a different message type. Also covers arbitrary
    files (zip, pdf, etc.) that can carry embedded GPS data or coordinates in
    filenames.

    During active alarm: delete ALL documents immediately without attempting
    classification or blur — any file type can leak data, and the alarm
    lockdown should have prevented this from being sent at all.

    Outside alarm: classify by caption/filename, route image/video through
    the blur pipeline, delete other/oversized files outright.
    """
    msg = update.effective_message
    doc = msg.document
    chat_id = update.effective_chat.id
    st = state.get(chat_id)
    caption_or_name = msg.caption or doc.file_name or ""

    if st.alarm_active:
        try:
            await msg.delete()
        except Exception:
            log.exception("failed to delete document during alarm")
            return
        await _warn(update)
        log.info("deleted document %r in chat %s during alarm", caption_or_name, chat_id)
        await _track_violation(update, context, "document during active alarm",
                               caption_or_name or "[document]")
        return

    strict = _strict_mode(chat_id, st)
    verdict = classify.classify_media(caption_or_name, False, strict)
    if not verdict.flagged:
        return

    kind = _document_kind(doc)

    if kind == "other":
        await _delete_flagged_document(
            update, context, verdict, caption_or_name or "[document, no caption/name]",
            f"deleted flagged document in chat {chat_id}: {verdict.reason}",
        )
        return

    if doc.file_size and doc.file_size > MAX_VIDEO_MB * 1024 * 1024:
        await _delete_flagged_document(
            update, context, verdict, caption_or_name,
            f"deleted oversized {kind}-document ({doc.file_size} bytes) in chat {chat_id}",
        )
        return

    await _blur_flagged_document(update, context, doc, kind, verdict, caption_or_name)


async def on_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    video = msg.video or msg.video_note
    chat_id = update.effective_chat.id
    st = state.get(chat_id)
    verdict = classify.classify_media(msg.caption or "", st.alarm_active, _strict_mode(chat_id, st))
    if not verdict.flagged:
        return

    # Rate limit: prevent ffmpeg being flooded with concurrent video blurs.
    if msg.from_user and not state.check_media_rate(chat_id, msg.from_user.id):
        try:
            await msg.delete()
        except Exception:
            pass
        await _warn(update)
        await _track_violation(update, context, "video flood (rate limited)", "[rate limited]")
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

        # Delete BEFORE blur so a mid-process crash never leaves the original visible.
        try:
            await msg.delete()
        except Exception:
            log.exception("failed to delete video message")
            return

        blurred_ok = await media.blur_video(src, dst)
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
