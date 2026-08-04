"""health_monitor.poll should DM admins when Redis is unreachable, the
update-processing queue is backing up, or many users are tripping the media
rate limit at once — and stay quiet (respecting cooldown) once each
condition clears."""
import time

import pytest

from bot import handlers, health_monitor, state, store

CHAT_ID = -1008888888
ADMIN_ID = 42


class FakeBot:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))


class FakeQueue:
    def __init__(self, depth: int = 0):
        self._depth = depth

    def qsize(self) -> int:
        return self._depth


class FakeApplication:
    def __init__(self, queue_depth: int = 0):
        self.update_queue = FakeQueue(queue_depth)


class FakeContext:
    def __init__(self, queue_depth: int = 0):
        self.bot = FakeBot()
        self.application = FakeApplication(queue_depth)


@pytest.fixture(autouse=True)
def clean_module_state(monkeypatch):
    monkeypatch.setattr(handlers, "CHAT_ADMINS", {CHAT_ID: {ADMIN_ID}})
    monkeypatch.setattr(store, "_consecutive_failures", 0)
    monkeypatch.setattr(store, "_last_failure_reason", "")
    state._chats.clear()
    state._claimed_admins.clear()
    state._global_rate_trips.clear()
    health_monitor._last_alerted.clear()
    yield
    state._chats.clear()
    state._claimed_admins.clear()
    state._global_rate_trips.clear()
    health_monitor._last_alerted.clear()


async def test_redis_unhealthy_notifies_admins(monkeypatch):
    monkeypatch.setattr(store, "ENABLED", True)
    monkeypatch.setattr(store, "_consecutive_failures", store._UNHEALTHY_THRESHOLD)
    monkeypatch.setattr(store, "_last_failure_reason", "timeout")

    ctx = FakeContext()
    await health_monitor.poll(ctx)

    assert any("Redis" in text for _, text in ctx.bot.sent)
    assert ADMIN_ID in [chat_id for chat_id, _ in ctx.bot.sent]


async def test_redis_alert_respects_cooldown_then_clears(monkeypatch):
    monkeypatch.setattr(store, "ENABLED", True)
    monkeypatch.setattr(store, "_consecutive_failures", store._UNHEALTHY_THRESHOLD)

    ctx = FakeContext()
    await health_monitor.poll(ctx)
    first_count = len(ctx.bot.sent)
    assert first_count > 0

    # Still unhealthy on the very next tick -- cooldown suppresses a repeat.
    await health_monitor.poll(ctx)
    assert len(ctx.bot.sent) == first_count

    # Recovers -- no new alert, and the cooldown state is cleared.
    monkeypatch.setattr(store, "_consecutive_failures", 0)
    await health_monitor.poll(ctx)
    assert len(ctx.bot.sent) == first_count
    assert "redis" not in health_monitor._last_alerted


async def test_queue_backlog_notifies_admins():
    ctx = FakeContext(queue_depth=health_monitor._QUEUE_BACKLOG_THRESHOLD)
    await health_monitor.poll(ctx)

    assert any("перевантажений" in text for _, text in ctx.bot.sent)


async def test_queue_depth_below_threshold_stays_quiet():
    ctx = FakeContext(queue_depth=health_monitor._QUEUE_BACKLOG_THRESHOLD - 1)
    await health_monitor.poll(ctx)

    assert ctx.bot.sent == []


async def test_rate_limit_trip_burst_notifies_admins():
    for _ in range(health_monitor._RATE_TRIP_THRESHOLD):
        state._global_rate_trips.append(time.monotonic())

    ctx = FakeContext()
    await health_monitor.poll(ctx)

    assert any("ліміт обробки медіа" in text for _, text in ctx.bot.sent)


async def test_notify_all_admins_dedupes_owner_and_chat_admin(monkeypatch):
    monkeypatch.setattr(handlers, "OWNER_IDS", {ADMIN_ID})  # same id as the chat admin
    monkeypatch.setattr(handlers, "CHAT_ADMINS", {CHAT_ID: {ADMIN_ID}})

    ctx = FakeContext()
    await handlers.notify_all_admins(ctx, "test")

    assert ctx.bot.sent.count((ADMIN_ID, "test")) == 1
