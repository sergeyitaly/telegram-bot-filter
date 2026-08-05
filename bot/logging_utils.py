"""JSON log formatting. Render captures stdout as the log stream, and a
structured line here is what makes bot/health_monitor.py's alerts (and any
future error-tracking integration) actionable — filterable by chat_id or
level instead of grepping free text across every module's differently
worded messages.
"""
import json
import logging

_RESERVED = set(logging.makeLogRecord({}).__dict__.keys()) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        # Anything passed via logging's `extra={...}` kwarg (chat_id,
        # user_id, redis_key, ...) rides along as its own JSON field.
        for key, value in record.__dict__.items():
            if key not in _RESERVED:
                payload[key] = value
        return json.dumps(payload, default=str)


def configure(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
