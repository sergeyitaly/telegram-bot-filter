"""Regression test for the activate_alarm double-capture bug: a redundant
/alarm_on (or a race with the alerts.in.ua auto-poller) must never overwrite
the saved original permissions with the already-locked state, or alarm_off
ends up "restoring" the chat right back to locked. See bot/handlers.py
activate_alarm for the fix (saved_permissions is only captured while None).
"""
import pytest
from telegram import ChatPermissions
from telegram.error import BadRequest

from bot import handlers, state

CHAT_ID = -1009999999


class FakeChat:
    def __init__(self, permissions: ChatPermissions):
        self.permissions = permissions


class FakeBot:
    """Stands in for context.bot: mirrors real Telegram semantics just
    enough for activate_alarm/deactivate_alarm — a single mutable
    permissions state that get_chat reads and set_chat_permissions writes."""

    def __init__(self, initial_permissions: ChatPermissions):
        self._permissions = initial_permissions
        self.set_calls: list[ChatPermissions] = []
        self.fail_next_set = False

    async def get_chat(self, chat_id):
        return FakeChat(self._permissions)

    async def set_chat_permissions(self, chat_id, permissions, use_independent_chat_permissions=True):
        if self.fail_next_set:
            self.fail_next_set = False
            raise BadRequest("Not enough rights to restrict/unrestrict chat member")
        self.set_calls.append(permissions)
        self._permissions = permissions

    async def send_message(self, *args, **kwargs):
        pass


class FakeContext:
    def __init__(self, bot: FakeBot):
        self.bot = bot


def _open_permissions() -> ChatPermissions:
    return ChatPermissions(
        can_send_messages=True,
        can_send_audios=True,
        can_send_documents=True,
        can_send_photos=True,
        can_send_videos=True,
        can_send_video_notes=True,
        can_send_voice_notes=True,
        can_send_other_messages=True,
        can_send_polls=True,
        can_add_web_page_previews=True,
        can_change_info=True,
        can_invite_users=True,
        can_pin_messages=True,
        can_manage_topics=True,
    )


@pytest.fixture(autouse=True)
def clean_state():
    state._chats.clear()
    yield
    state._chats.clear()


async def test_repeated_activate_then_deactivate_restores_original():
    initial = _open_permissions()
    bot = FakeBot(initial)
    ctx = FakeContext(bot)

    await handlers.activate_alarm(ctx, CHAT_ID)
    await handlers.activate_alarm(ctx, CHAT_ID)
    await handlers.activate_alarm(ctx, CHAT_ID)
    await handlers.deactivate_alarm(ctx, CHAT_ID)

    assert bot._permissions.to_dict() == initial.to_dict()


async def test_first_activate_locks_media_down():
    initial = _open_permissions()
    bot = FakeBot(initial)
    ctx = FakeContext(bot)

    await handlers.activate_alarm(ctx, CHAT_ID)

    locked = bot._permissions
    assert locked.can_send_photos is False
    assert locked.can_send_videos is False
    assert locked.can_send_documents is False
    assert locked.can_send_voice_notes is False
    # Non-media permissions from the original are preserved, not clobbered.
    assert locked.can_send_messages is True
    assert locked.can_pin_messages is True


async def test_saved_permissions_not_overwritten_by_redundant_activate():
    initial = _open_permissions()
    bot = FakeBot(initial)
    ctx = FakeContext(bot)

    await handlers.activate_alarm(ctx, CHAT_ID)
    captured_after_first = state.get(CHAT_ID).saved_permissions.to_dict()

    # Redundant activation: at this point bot._permissions is already the
    # LOCKED state. Before the fix, this call re-read "current" (locked)
    # permissions and stored those as saved_permissions, losing the original.
    await handlers.activate_alarm(ctx, CHAT_ID)
    captured_after_second = state.get(CHAT_ID).saved_permissions.to_dict()

    assert captured_after_second == captured_after_first == initial.to_dict()


async def test_failed_restore_keeps_saved_permissions_for_retry():
    initial = _open_permissions()
    bot = FakeBot(initial)
    ctx = FakeContext(bot)

    await handlers.activate_alarm(ctx, CHAT_ID)
    assert state.get(CHAT_ID).saved_permissions.to_dict() == initial.to_dict()

    # Restore API call fails (e.g. bot temporarily lost admin rights).
    bot.fail_next_set = True
    await handlers.deactivate_alarm(ctx, CHAT_ID)

    # The chat is still locked (the failed call never applied), and the
    # original must still be saved -- clearing it here would strand the
    # chat locked with nothing left to restore from.
    assert bot._permissions.can_send_photos is False
    assert state.get(CHAT_ID).saved_permissions is not None
    assert state.get(CHAT_ID).saved_permissions.to_dict() == initial.to_dict()

    # Retrying (once the underlying issue is fixed) succeeds and restores.
    await handlers.deactivate_alarm(ctx, CHAT_ID)
    assert bot._permissions.to_dict() == initial.to_dict()
    assert state.get(CHAT_ID).saved_permissions is None
