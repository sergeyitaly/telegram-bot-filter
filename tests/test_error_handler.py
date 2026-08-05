"""on_error is the global PTB catch-all (see main.py: app.add_error_handler)
-- with SENTRY_DSN set, its log.error(..., exc_info=...) call is what Sentry's
logging integration turns into an event. These tests check it actually logs
usable chat_id/user_id context and, just as important, never itself raises --
an error handler that crashes on a malformed update would be a real problem
(PTB has nowhere further to route it)."""
import logging
from unittest.mock import MagicMock

from telegram import Update

from bot import handlers


class FakeContext:
    def __init__(self, error: Exception):
        self.error = error


def _make_update(chat_id: int, user_id: int) -> Update:
    update = MagicMock(spec=Update)
    update.effective_chat = MagicMock(id=chat_id)
    update.effective_user = MagicMock(id=user_id)
    return update


async def test_logs_chat_and_user_id_from_update(caplog):
    update = _make_update(chat_id=-1001234, user_id=555)
    try:
        raise ValueError("boom")
    except ValueError as exc:
        with caplog.at_level(logging.ERROR):
            await handlers.on_error(update, FakeContext(exc))

    record = caplog.records[-1]
    assert record.levelno == logging.ERROR
    assert record.chat_id == -1001234
    assert record.user_id == 555
    assert record.exc_info is not None


async def test_does_not_raise_on_none_update(caplog):
    """job_queue-triggered errors (air_alert.poll, health_monitor.poll, ...)
    call the error handler with update=None -- there's no Telegram update
    involved at all."""
    try:
        raise RuntimeError("poll failure")
    except RuntimeError as exc:
        with caplog.at_level(logging.ERROR):
            await handlers.on_error(None, FakeContext(exc))

    record = caplog.records[-1]
    assert record.chat_id is None
    assert record.user_id is None


async def test_does_not_raise_on_non_update_object(caplog):
    try:
        raise KeyError("weird")
    except KeyError as exc:
        with caplog.at_level(logging.ERROR):
            await handlers.on_error(object(), FakeContext(exc))

    assert caplog.records[-1].chat_id is None


async def test_handles_missing_effective_chat_or_user(caplog):
    """A channel post or similar update type can have effective_user None
    even though it's a real Update -- must not crash on that either."""
    update = MagicMock(spec=Update)
    update.effective_chat = MagicMock(id=-999)
    update.effective_user = None

    try:
        raise ValueError("boom")
    except ValueError as exc:
        with caplog.at_level(logging.ERROR):
            await handlers.on_error(update, FakeContext(exc))

    record = caplog.records[-1]
    assert record.chat_id == -999
    assert record.user_id is None
