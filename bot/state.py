"""In-memory per-chat alarm state.

Free-tier hosting has ephemeral disk, so this intentionally does not persist
across restarts — admins re-arm with /alarm_on if the bot redeploys mid-alert.
"""
from dataclasses import dataclass, field


@dataclass
class ChatState:
    alarm_active: bool = False


_chats: dict[int, ChatState] = {}


def get(chat_id: int) -> ChatState:
    return _chats.setdefault(chat_id, ChatState())


def set_alarm(chat_id: int, active: bool) -> None:
    get(chat_id).alarm_active = active
