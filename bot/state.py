"""In-memory per-chat alarm state and per-user violation tracking.

Free-tier hosting has ephemeral disk, so this intentionally does not persist
across restarts — admins re-arm with /alarm_on if the bot redeploys mid-alert.
"""
import time
from dataclasses import dataclass


@dataclass
class ChatState:
    alarm_active: bool = False
    # True if the current alarm was armed by the alerts.in.ua poller rather
    # than a manual /alarm_on — lets the poller auto-clear only what it set.
    auto_armed: bool = False
    # Chat permissions to restore on /alarm_off, captured just before we
    # lock media down for /alarm_on.
    saved_permissions: object = None
    # time.monotonic() of the last alarm_active True->False transition, for
    # the post-alarm keyword-filtering grace period. None = never armed yet.
    alarm_ended_at: float | None = None


_chats: dict[int, ChatState] = {}

# Chats the bot is currently a member of, so the alerts.in.ua poller knows
# which chats to (de)activate alarm mode in. Maintained via my_chat_member updates.
_known_chats: set[int] = set()

# Rolling per-(chat, user) violation timestamps for repeat-offender muting.
_violations: dict[tuple[int, int], list[float]] = {}

# Chats self-activated at runtime (auto-registered when a verified admin
# adds the bot, or via /addadmin), plus their admins. Merged with the
# env-configured CHAT_ADMINS at lookup time. In-memory like everything else
# here: a restart clears it, and an admin needs to run /activate once to
# recover — see cmd_activate in bot/handlers.py.
_claimed_admins: dict[int, set[int]] = {}


def claimed_admins_for(chat_id: int) -> set[int]:
    return _claimed_admins.get(chat_id, set())


def add_chat_admin(chat_id: int, user_id: int) -> None:
    _claimed_admins.setdefault(chat_id, set()).add(user_id)


def is_claimed(chat_id: int) -> bool:
    return chat_id in _claimed_admins


def get(chat_id: int) -> ChatState:
    # Every handler touches this, so piggyback chat registration here too —
    # covers chats the bot joined before my_chat_member tracking existed.
    _known_chats.add(chat_id)
    return _chats.setdefault(chat_id, ChatState())


def set_alarm(chat_id: int, active: bool, auto: bool = False) -> None:
    st = get(chat_id)
    if st.alarm_active and not active:
        st.alarm_ended_at = time.monotonic()
    st.alarm_active = active
    st.auto_armed = auto if active else False


def in_post_alarm_grace(chat_id: int, grace_seconds: int) -> bool:
    ended_at = get(chat_id).alarm_ended_at
    return ended_at is not None and time.monotonic() - ended_at < grace_seconds


def register_chat(chat_id: int) -> None:
    _known_chats.add(chat_id)


def unregister_chat(chat_id: int) -> None:
    _known_chats.discard(chat_id)
    _chats.pop(chat_id, None)


def known_chats() -> set[int]:
    return set(_known_chats)


def record_violation(chat_id: int, user_id: int, window_seconds: int) -> int:
    """Record a filter hit for this user and return how many they've had
    within the trailing window."""
    key = (chat_id, user_id)
    now = time.monotonic()
    hits = [t for t in _violations.get(key, []) if now - t < window_seconds]
    hits.append(now)
    _violations[key] = hits
    return len(hits)
