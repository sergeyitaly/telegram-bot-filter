"""Upstash Redis REST client for state that must survive restarts.

Free-tier hosting has ephemeral memory/disk — everything in bot/state.py
and bot/keywords.py resets on every restart/redeploy. This persists just
the pieces where losing state causes real harm (a stuck media lockdown, a
group needing to re-onboard, a keyword silently reverting), not everything,
to keep the write-through cost low and the code simple.

API docs: https://upstash.com/docs/redis/features/restapi — GET
/get/<key> returns {"result": <value or null>}; POST /set/<key> with the
raw value as the request body returns {"result": "OK"}.

No-ops everywhere if UPSTASH_REDIS_REST_URL/TOKEN aren't configured, so the
bot runs exactly as it did before this existed.
"""
import json
import logging

import httpx

from bot.config import UPSTASH_REDIS_REST_TOKEN, UPSTASH_REDIS_REST_URL

log = logging.getLogger(__name__)

ENABLED = bool(UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN)

_HEADERS = {"Authorization": f"Bearer {UPSTASH_REDIS_REST_TOKEN}"}

# One pooled client for the process lifetime instead of a new TCP+TLS
# handshake per call — every chat_state write and every hydrate-on-startup
# read goes through this, so the per-call setup cost was real, not
# theoretical. Constructing httpx.AsyncClient does no I/O itself (connections
# are opened lazily on first request), so a module-level instance is safe
# without needing an active event loop at import time.
_client = httpx.AsyncClient(timeout=10)

# Tracked so bot/health_monitor.py can DM admins when Redis is unreachable or
# over quota (free-tier daily request/size limits) instead of every write
# silently degrading to in-memory-only with nobody noticing.
_UNHEALTHY_THRESHOLD = 3
_consecutive_failures = 0
_last_failure_reason = ""


def is_healthy() -> bool:
    return _consecutive_failures < _UNHEALTHY_THRESHOLD


def health_status() -> dict:
    return {
        "consecutive_failures": _consecutive_failures,
        "last_failure_reason": _last_failure_reason,
    }


def _record_success() -> None:
    global _consecutive_failures, _last_failure_reason
    _consecutive_failures = 0
    _last_failure_reason = ""


def _record_failure(reason: str) -> None:
    global _consecutive_failures, _last_failure_reason
    _consecutive_failures += 1
    _last_failure_reason = reason[:200]


async def get_json(key: str, default):
    if not ENABLED:
        return default
    try:
        resp = await _client.get(f"{UPSTASH_REDIS_REST_URL}/get/{key}", headers=_HEADERS)
        resp.raise_for_status()
        result = resp.json().get("result")
    except Exception as exc:
        log.exception("redis get failed for key %s — using default", key, extra={"redis_key": key})
        _record_failure(str(exc))
        return default
    _record_success()

    if result is None:
        return default
    try:
        return json.loads(result)
    except (TypeError, ValueError):
        log.warning("redis value for %s wasn't valid JSON — using default", key, extra={"redis_key": key})
        return default


async def set_json(key: str, value) -> None:
    if not ENABLED:
        return
    try:
        resp = await _client.post(
            f"{UPSTASH_REDIS_REST_URL}/set/{key}",
            headers=_HEADERS,
            content=json.dumps(value),
        )
        resp.raise_for_status()
    except Exception as exc:
        log.exception("redis set failed for key %s — change is in-memory only", key, extra={"redis_key": key})
        _record_failure(str(exc))
        return
    _record_success()
