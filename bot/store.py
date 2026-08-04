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


async def get_json(key: str, default):
    if not ENABLED:
        return default
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{UPSTASH_REDIS_REST_URL}/get/{key}", headers=_HEADERS)
            resp.raise_for_status()
            result = resp.json().get("result")
    except Exception:
        log.exception("redis get failed for key %s — using default", key)
        return default

    if result is None:
        return default
    try:
        return json.loads(result)
    except (TypeError, ValueError):
        log.warning("redis value for %s wasn't valid JSON — using default", key)
        return default


async def set_json(key: str, value) -> None:
    if not ENABLED:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{UPSTASH_REDIS_REST_URL}/set/{key}",
                headers=_HEADERS,
                content=json.dumps(value),
            )
            resp.raise_for_status()
    except Exception:
        log.exception("redis set failed for key %s — change is in-memory only", key)
