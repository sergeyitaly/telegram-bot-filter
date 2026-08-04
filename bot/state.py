"""Per-chat alarm state and per-user violation tracking.

Everything here is in-memory first (fast, no await needed to read), with the
handful of fields where losing state on restart causes real harm — alarm
lockdown, claimed admins — written through to Redis via bot/store.py if
configured. The short-window violation *counter* used for auto-mute is
intentionally in-memory only (minutes-long window, low stakes if reset by a
restart); the violation *audit log* (log_violation/get_violation_log) is
durable and Redis-backed on purpose — it exists specifically so it survives
for a chat admin to review later.
"""
import collections
import inspect
import time
from dataclasses import dataclass

from telegram import ChatPermissions

from bot import store

_CHAT_PERMISSIONS_KEYS = set(inspect.signature(ChatPermissions).parameters.keys())

_CHAT_STATES_KEY = "chat_states"  # legacy shared key — read-only for migration
_CLAIMED_ADMINS_KEY = "claimed_admins"


def _chat_state_key(chat_id: int) -> str:
    return f"chat_state:{chat_id}"


@dataclass
class ChatState:
    alarm_active: bool = False
    # True if the current alarm was armed by the alerts.in.ua poller rather
    # than a manual /alarm_on — lets the poller auto-clear only what it set.
    auto_armed: bool = False
    # Chat permissions to restore on /alarm_off, captured just before we
    # lock media down for /alarm_on.
    saved_permissions: ChatPermissions | None = None
    # Wall-clock unix time of the last alarm_active True->False transition,
    # for the post-alarm keyword-filtering grace period. None = never armed.
    alarm_ended_at: float | None = None

    def to_json(self) -> dict:
        return {
            "alarm_active": self.alarm_active,
            "auto_armed": self.auto_armed,
            "saved_permissions": self.saved_permissions.to_dict() if self.saved_permissions else None,
            "alarm_ended_at": self.alarm_ended_at,
        }

    @classmethod
    def from_json(cls, data: dict) -> "ChatState":
        perms = data.get("saved_permissions")
        return cls(
            alarm_active=data.get("alarm_active", False),
            auto_armed=data.get("auto_armed", False),
            saved_permissions=ChatPermissions(**{k: v for k, v in perms.items() if k in _CHAT_PERMISSIONS_KEYS}) if perms else None,
            alarm_ended_at=data.get("alarm_ended_at"),
        )


_chats: dict[int, ChatState] = {}

# Chats the bot is currently a member of, so the alerts.in.ua poller knows
# which chats to (de)activate alarm mode in. Maintained via my_chat_member
# updates, and seeded from claimed_admins on hydration.
_known_chats: set[int] = set()

# Rolling per-(chat, user) violation timestamps for repeat-offender muting.
_violations: dict[tuple[int, int], list[float]] = {}

# Sliding text buffer for split-message coordinate/keyword detection.
# Keyed by (chat_id, user_id); each entry is (monotonic_time, text).
_user_text_buffer: dict[tuple[int, int], collections.deque] = {}
_CONTEXT_WINDOW_SECONDS = 30
_CONTEXT_MAX_MESSAGES = 5

# Per-user media rate limit — prevents a message flood from swamping ffmpeg
# or the OCR pipeline.  In-memory only (restarts reset it; that's fine, the
# window is short).
_media_rate: dict[tuple[int, int], list[float]] = {}
_RATE_MEDIA_MAX = 5        # max expensive media ops per user per window
_RATE_MEDIA_WINDOW = 60.0  # seconds

# Chats self-activated at runtime (auto-registered when a verified admin
# adds the bot, or via /addadmin), plus their admins. Merged with the
# env-configured CHAT_ADMINS at lookup time.
_claimed_admins: dict[int, set[int]] = {}


async def hydrate() -> None:
    """Load persisted state from Redis (no-op if not configured). Call once
    at startup, before polling begins."""
    global _claimed_admins
    raw_admins = await store.get_json(_CLAIMED_ADMINS_KEY, {})
    _claimed_admins = {int(chat_id): set(ids) for chat_id, ids in raw_admins.items()}
    _known_chats.update(_claimed_admins.keys())

    # Load per-chat state from individual keys (new format — no concurrent-write race).
    for chat_id in list(_claimed_admins.keys()):
        data = await store.get_json(_chat_state_key(chat_id), None)
        if data:
            _chats[chat_id] = ChatState.from_json(data)

    # Migration: also load from old shared blob for chats not yet in new format.
    raw_states = await store.get_json(_CHAT_STATES_KEY, {})
    for chat_id_str, data in raw_states.items():
        cid = int(chat_id_str)
        if cid not in _chats:
            _chats[cid] = ChatState.from_json(data)
            _known_chats.add(cid)


async def _persist_claimed_admins() -> None:
    await store.set_json(_CLAIMED_ADMINS_KEY, {str(k): sorted(v) for k, v in _claimed_admins.items()})


async def _persist_chat_state(chat_id: int) -> None:
    # Write to a per-chat key so concurrent updates to different chats never
    # race on a shared blob (old chat_states key was a read-modify-write race).
    await store.set_json(_chat_state_key(chat_id), _chats[chat_id].to_json())


def claimed_admins_for(chat_id: int) -> set[int]:
    return _claimed_admins.get(chat_id, set())


def all_claimed_chat_ids() -> set[int]:
    return set(_claimed_admins.keys())


async def add_chat_admin(chat_id: int, user_id: int) -> None:
    _claimed_admins.setdefault(chat_id, set()).add(user_id)
    await _persist_claimed_admins()


async def remove_chat_admin(chat_id: int, user_id: int) -> None:
    if chat_id in _claimed_admins:
        _claimed_admins[chat_id].discard(user_id)
        if not _claimed_admins[chat_id]:
            del _claimed_admins[chat_id]
    await _persist_claimed_admins()


def is_claimed(chat_id: int) -> bool:
    return chat_id in _claimed_admins


def get(chat_id: int) -> ChatState:
    # Every handler touches this, so piggyback chat registration here too —
    # covers chats the bot joined before my_chat_member tracking existed.
    _known_chats.add(chat_id)
    return _chats.setdefault(chat_id, ChatState())


async def set_alarm(chat_id: int, active: bool, auto: bool = False) -> None:
    st = get(chat_id)
    if st.alarm_active and not active:
        st.alarm_ended_at = time.time()
    st.alarm_active = active
    st.auto_armed = auto if active else False
    await _persist_chat_state(chat_id)


async def set_saved_permissions(chat_id: int, permissions: ChatPermissions | None) -> None:
    get(chat_id).saved_permissions = permissions
    await _persist_chat_state(chat_id)


def in_post_alarm_grace(chat_id: int, grace_seconds: int) -> bool:
    ended_at = get(chat_id).alarm_ended_at
    return ended_at is not None and time.time() - ended_at < grace_seconds


def register_chat(chat_id: int) -> None:
    _known_chats.add(chat_id)


def unregister_chat(chat_id: int) -> None:
    _known_chats.discard(chat_id)
    _chats.pop(chat_id, None)


def known_chats() -> set[int]:
    return set(_known_chats)


def get_user_context(chat_id: int, user_id: int, new_text: str) -> str:
    """Append new_text to the user's recent message buffer and return the
    concatenated context string. Lets the classifier detect coordinates or
    keywords that were deliberately split across multiple messages."""
    key = (chat_id, user_id)
    now = time.monotonic()
    buf = _user_text_buffer.setdefault(key, collections.deque())
    while buf and now - buf[0][0] > _CONTEXT_WINDOW_SECONDS:
        buf.popleft()
    buf.append((now, new_text))
    if len(buf) > _CONTEXT_MAX_MESSAGES:
        buf.popleft()
    return " ".join(t for _, t in buf)


def check_media_rate(chat_id: int, user_id: int) -> bool:
    """Return True if this user is within the rate limit for expensive media
    processing (OCR / ffmpeg blur).  Return False if they are flooding —
    caller should delete the message without expensive processing."""
    key = (chat_id, user_id)
    now = time.monotonic()
    times = _media_rate.get(key, [])
    times = [t for t in times if now - t < _RATE_MEDIA_WINDOW]
    times.append(now)
    _media_rate[key] = times
    return len(times) <= _RATE_MEDIA_MAX


def record_violation(chat_id: int, user_id: int, window_seconds: int) -> int:
    """Record a filter hit for this user and return how many they've had
    within the trailing window."""
    key = (chat_id, user_id)
    now = time.monotonic()
    hits = [t for t in _violations.get(key, []) if now - t < window_seconds]
    hits.append(now)
    _violations[key] = hits
    return len(hits)


# Cap per chat so the log can't grow unbounded over a long-lived deployment.
_VIOLATION_LOG_MAX = 500


async def log_violation(chat_id: int, user_id: int, username: str | None, reason: str, text: str) -> None:
    """Append a durable audit-log entry for a flagged message — for a chat's
    admin to review and decide whether escalation (e.g. to police) is
    warranted. No-op if Redis isn't configured; deliberately text-only, no
    media, so this log can't itself become a copy of the content it's
    meant to help suppress."""
    key = f"violations_log:{chat_id}"
    entries = await store.get_json(key, [])
    entries.append({
        "ts": time.time(),
        "user_id": user_id,
        "username": username,
        "reason": reason,
        "text": text[:500],  # bounded, this is an audit trail, not a full transcript
    })
    if len(entries) > _VIOLATION_LOG_MAX:
        entries = entries[-_VIOLATION_LOG_MAX:]
    await store.set_json(key, entries)


async def get_violation_log(chat_id: int, user_id: int | None = None) -> list[dict]:
    entries = await store.get_json(f"violations_log:{chat_id}", [])
    if user_id is not None:
        entries = [e for e in entries if e.get("user_id") == user_id]
    return entries
